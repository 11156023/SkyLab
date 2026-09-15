# AI Teacher Judge 全流程與安全界線總覽

> 產生日期：2026-09-13。分析方法：4 個 subagent 分工（chat pipeline / 腳本 pipeline / 安全界線 / API+資料模型），全部關鍵函式引用已用 `rg` 交叉驗證；另追加生成迴圈深挖（§4-6 ~ §4-12，fix_script_content patch 機制與 coverage 邊界情況）。
> 分析層級：Campus backend（`backend/app/ai/teacher_judge/`）。AI data plane 為 `VLLM_BASE_URL` direct-vLLM OpenAI-compatible endpoint（非 LiteLLM public relay）。
>
> **2026-09-14 修訂**：原文分析後，chat 提案機制經 4 個 commit 重構（`f2b6841f` 提案改以 server 端工具呼叫為唯一來源、`6938ff0c` 修復 json_object 封鎖工具呼叫、`86196d33` 移除 description 欄位、`855e536c` 收斂對話焦點規模與逾時 coercion）。本次已對照 HEAD `855e536c` 重新驗證全文：§0/§1/§2/§3 改寫為現行「工具呼叫唯一提案通道」架構，§2-A 保留為已提交的歷史記錄並標註被取代的項目，§4-§9 修正行號與機制描述。行號基準：HEAD `855e536c`。

---

## 0. 一頁總覽

```text
老師建立 session（綁檢查表來源檔 teacher_judge_files）
        │
        ▼
┌─ 對話層（session / messages）─────────────────────────────────────────┐
│ chat 傳送需求 → 指令審核（check_steps 白名單）→ 追問細節（conversation_focus）
│        → 發起提案（工具呼叫：create/edit_checklist_item，server 端暫存）
│        → 再編輯（revision 樂觀鎖 × 3 閘）
│        → 全表潤飾（is_refine，強制 list_checklist）
└──────────────┬───────────────────────────────────────────────────────┘
               │ 前端套用提案：PATCH /judge/files/{fid}/analysis（revision+1）
               ▼
┌─ 腳本層（script artifact / run）─────────────────────────────────────┐
│ 製作腳本（生成迴圈：LLM→靜態閘→coverage 閘→AI 審查→修復 patch，最多 4 次重試）
│        → 人工/自動核准（approved）→ 建立 run（manual scope、≤5 台）
│        → 背景執行（paramiko SSH root@學生 VM，60s timeout）
│        → 結果驗證（teacher_judge_result.v1 schema）→ AI 判讀 → completed
└──────────────┬───────────────────────────────────────────────────────┘
               ▼
        下游消費（學生成績投影 ai_assignment_service._check_to_student）
```

安全哲學一句話：**對話層防「prompt injection 與幻覺提案」，腳本層防「生成碼越權執行」，兩層之間用 `analysis_revision` 樂觀鎖交接。對話提案一律經 server 端工具呼叫暫存與驗證，回覆文字中的項目資料一律忽略（tools-first）。**

### 0-B 預計調整的 chat 流程（目標狀態 Mermaid 圖）

```mermaid
flowchart TD
    A["老師發起評分項目"] --> B["AI 審核<br/>語意限制：不得更動機器<br/>禁 sudo / rm 等高危指令"]
    B --> C{"評估：有缺資料？"}
    C -- "有缺" --> D["請老師補充資料"]
    D --> A
    C -- "沒缺" --> E["發起提案<br/>rubric_proposal（暫存）"]
    E --> F{"導師同意變更？"}
    F -- "不同意" --> H["提案作廢，留在對話"]
    H --> A
    F -- "同意" --> G["導師編輯既有項目或新增<br/>套用提案 revision+1"]
    G --> I["儲存 → 建立腳本"]
    I --> J{"最後一道安全限制<br/>潤飾檢查：缺資訊 / 安全問題？"}
    J -- "有缺失" --> K["返回訊息給老師"]
    K --> L["老師調整後重送"]
    L --> I
    J -- "沒缺" --> M["製作腳本完成<br/>approved 可執行"]

    style B fill:#fff3e0,stroke:#e65100
    style J fill:#fff3e0,stroke:#e65100
    style M fill:#e8f5e9,stroke:#1b5e20
```

| 節點 | 對應實作（現行程式碼） | 章節 |
|---|---|---|
| 老師發起評分項目 | `create_message` → `chat_with_rubric` | §階段 1 |
| AI 審核（語意/高危指令限制） | `create_checklist_item`/`edit_checklist_item` 參數驗證 + `_proposal_candidate_rejection` 拒絕理由（§階段 2、§3） | §階段 2 |
| 有缺 → 請老師補充 | `proposal_status=needs_information` + `conversation_focus` 跨輪注入 | §階段 3 |
| 發起提案 | 工具呼叫 staging → `rubric_proposal` response 欄位 | §階段 4、§2-B |
| 導師同意 → 編輯/新增 → 儲存 | 前端套用 → `update_file_analysis`（revision+1） | §階段 4/5 |
| 儲存 → 建立腳本 | `create_session_script` → `create_artifact` → `build_reviewed_script` | §4-1/4-7 |
| 最後一道安全限制 | 靜態閘（policy+quality）+ coverage 閘 + AI reviewer；失敗 → fix patch / 重新生成 / `review_failed` | §4-7~4-12、§5 #18-23 |
| 製作腳本完成 | `_resolve_status` → approved | §4-2' |

---

## 1. API 端點地圖

全域前置：`/api/v1`，router 掛載於 `backend/app/api/main.py:84-87`。所有端點依賴 `InstructorUser`（`backend/app/api/deps/auth.py:88`，student 一律擋），班級層以 `require_teaching_access(user, class.owner_id)`（`backend/app/core/authorizers.py:54-65`）驗 owner 或 `TEACHING_OWNERSHIP_BYPASS`（admin）。

### 1-1 Sessions（`backend/app/api/routes/teacher_judge_sessions.py`，prefix `/teaching-classes/{id}/judge/sessions`）

| 方法 | 路徑 | 函式 | 用途 | 呼叫 service |
|---|---|---|---|---|
| GET | `/` | `list_sessions` :131 | 列 session（置頂→活動時間） | `session_public_many` |
| POST | `/` | `create_session` :159 | 建 session（blank / existing） | `create_blank_file`、`validate_selected_file`、`ensure_selected_file_available` |
| POST | `/{sid}/fork` | `fork_session` :204 | 複製 session | `fork_session_data`（session_service.py:486） |
| GET | `/{sid}` | `get_session_detail` :223 | 取單一 session | `session_public` |
| PATCH | `/{sid}` | `update_session` :234 | 改設定/來源/狀態/置頂 | `clear_session_messages` 等 |
| POST | `/{sid}/archive` | `archive_session` :297 | 歸檔 | 委派 `update_session` |
| DELETE | `/{sid}` | `delete_session` :313 | 刪除 | `delete_session_data`（session_service.py:138） |
| POST | `/{sid}/attachments` | `upload_session_attachment` :328 | 上傳附件（pending，≤5 個） | `create_attachment`（attachment_service.py:91） |
| DELETE | `/{sid}/attachments/{aid}` | `delete_session_attachment` :372 | 刪 pending 附件 | `delete_attachment` |
| GET | `/{sid}/messages` | `list_messages` :391 | 訊息列表（cursor 分頁） | `message_public` |
| DELETE | `/{sid}/messages` | `clear_messages` :438 | 清空對話 | `clear_session_messages` |
| **POST** | **`/{sid}/messages`** | **`create_message` :452** | **chat 主入口** | `chat_with_rubric`、`analyze_attachments_itemwise` |
| POST | `/{sid}/scripts` | `create_session_script` :629 | 產生腳本（revision 預檢 + 空表 422） | `create_artifact`（script_artifact_service.py:1440） |
| GET | `/{sid}/runs` | `list_session_runs` :676 | 列 run 摘要 | 直接 SQL |
| GET | `/{sid}/runs/{rid}` | `get_session_run` :715 | run 詳情 | `_run_to_public`（script_run_service.py:38） |
| POST | `/{sid}/scripts/{aid}/runs` | `create_session_run` :744 | 建 run + 背景執行 | `create_script_run`（script_run_service.py:224）+ `submit(execute_script_run)` |

### 1-2 Scripts（`backend/app/api/routes/teacher_judge_scripts.py`，prefix `/teaching-classes/{id}/judge/scripts`）

| 方法 | 路徑 | 函式 | 呼叫 service |
|---|---|---|---|
| GET | `/` | `list_class_teacher_judge_scripts` :70 | `list_artifacts` |
| POST | `/` | `create_class_teacher_judge_script` :85 | `create_artifact` |
| GET | `/{script_id}` | `get_class_teacher_judge_script` :107 | `get_artifact_public` |
| POST | `/{script_id}/regenerate` | `regenerate_class_teacher_judge_script` :124 | `regenerate_artifact`（:1533） |
| POST | `/{script_id}/approve` | `approve_class_teacher_judge_script` :144 | `approve_artifact`（:1663） |
| PATCH | `/{script_id}` | `rename_class_teacher_judge_script` :162 | `rename_artifact` |
| POST | `/{script_id}/runs` | `create_class_teacher_judge_script_run` :181 | `create_script_run` + `submit` |
| GET | `/{script_id}/runs/{rid}` | `get_class_teacher_judge_script_run` :208 | `get_script_run_public` |
| POST | `/{script_id}/archive` | `archive_class_teacher_judge_script` :227 | `archive_artifact`（:1694） |
| DELETE | `/{script_id}` | `delete_class_teacher_judge_script` :244 | `delete_artifact`（:1738） |

### 1-3 Files / Rubric（`teacher_judge_files.py`、`rubric.py`）

| 方法 | 路徑 | 函式 | 用途 |
|---|---|---|---|
| GET | `.../judge/files/` | `list_class_teacher_judge_files` :43 | 列檢查表來源檔 |
| GET | `.../judge/files/{fid}/download` | `download_class_teacher_judge_file` :55 | 下載原檔 |
| **PATCH** | **`.../judge/files/{fid}/analysis`** | `update_class_teacher_judge_file_analysis` :73 | **套用提案（樂觀鎖）→ `update_file_analysis`（file_service.py:169）** |
| POST | `/api/v1/rubric/download-excel` | `download_excel` :23 | 匯出 Excel（`normalize_items_for_export` → `export_to_excel`，export.py:32） |
| GET | `/api/v1/rubric/health` | `health_check` :51 | `vllm_configured=bool(VLLM_MODEL_NAME)` |

---

## 2. Chat Pipeline：七階段逐一對照程式碼

### 階段 1 — chat 傳送需求

**入口**：`POST /teaching-classes/{id}/judge/sessions/{sid}/messages` → `create_message`（teacher_judge_sessions.py:452）。

流程（行號皆在該 route 檔）：
1. `_access`（:109）班級權限 → `get_session`（session_service.py:129）確認 session 屬本班 → `ensure_active`（session_service.py:308）archived 直接 409。
2. `selected_file_for_chat`（session_service.py:120）：chat 允許**無選檔**啟動（純詢問）；有選檔才 `require_selected_file`。
3. **revision 預檢**（:464-476）：`payload.analysis_revision != file.analysis_revision` → 409 `teacher_judge_analysis_revision_conflict`。
4. 輸入驗證：content 空且無附件 → 422（:477-478）；content ≤ 20,000 字、附件 ≤ 5（schemas.py:214-222）；`get_pending_attachments`（attachment_service.py:64）驗附件歸屬與狀態。
5. **先存 user message**（:480-493）：`redact_message_content`（session_service.py:93，遮蔽 password/token/PEM）→ flush → 附件綁 `message_id` → commit。refine 時 user message 預先標 `ui_hidden=True`。**先持久化，模型呼叫前**。
6. 載入指令目錄 `get_enabled_template_commands(include_cross_template=True)`（template_command_service.py:78）+ `rubric_context = json.dumps(file.analysis_json)`（無檔時 `"{}"`）。
7. 分支：有附件且非 refine → `analyze_attachments_itemwise`（service.py:2311，會帶入 `teacher_message=payload.content.strip()`）；否則 → `chat_with_rubric`（service.py:1898），history 由 `bounded_history(..., exclude_attachments_for_message_id=user_message.id)` 組出。

```python
# service.py:1898（簽名節錄）
async def chat_with_rubric(messages, rubric_context, is_refine=False,
    template_key="linux", template_commands=None, environment_keys=None,
    attachment_context=None, analysis_revision=None, rubric_available=None,
) -> TeacherJudgeChatResult  # 欄位：reply / proposal / metrics /
                              # conversation_focus / proposal_status / tool_calls
```

> 舊版 `allow_add_without_rubric` 與 `source_title` 參數已隨 tools-first 重構移除：無 rubric 時根本不提供提案工具（見 §3），item id 一律由 server 配發。

### 階段 2 — 指令審核（提案工具參數的多層 server 端驗證）

「指令」= 提案工具參數裡的 `check_steps`（引用 template command 目錄）。每個 step 帶 `(template_key, command_key, parameters)`，兩個 key 的語意不同：

- **`command_key`**：平台在 **DB 目錄**（`teacher_judge_template_commands`）登錄的受控執行能力 — check_steps 必須引用目錄指令；目錄內含通用受控指令 `system.run_command`（GENERAL_COMMAND），其 `parameters.argv/cwd/timeout_seconds/success_criteria` 由模型提出並經伺服器驗證 — 亦即**實際執行的命令內容可以超出登錄目錄**，只是必須包在受控 step 內、受 timeout 1–300s 夾擠/coercion 與生成端 policy/quality 驗證。
- **`template_key`**：系統資訊種類／平台環境（`SUPPORTED_TEMPLATE_KEYS = {linux, python, n8n, postgresql}`），決定檢查針對哪個 stack；**不是提案閘** — step 只填 `command_key` 且全目錄唯一時，server 會自動補齊 template_key（template_command_service.py:188-198）；`include_cross_template=True` 時目錄保證追加 `system.run_command`。

伺服器端驗證鏈（提案只經工具通道，逐層發生在 `_execute_checklist_tool`，service.py:1390-1647）：

| 層 | 函式 | 行為 | 引用 |
|---|---|---|---|
| 工具分派 | `_execute_checklist_tool`（:1390） | 4 個工具：`list_checklist`/`get_checklist_item`（唯讀）+ `create_checklist_item`/`edit_checklist_item`（提案）；未知名稱/參數 → `{"error": "不支援的工具或參數"}` | service.py:1417-1419, 1647 |
| edit 讀表閘 | 同上 | `edit_checklist_item` 前必須先以 `list_checklist` 或 `get_checklist_item` 讀過該 id（`read_ids`），否則拒絕並要求先讀表 | service.py:1551-1557 |
| id 防偽 | `_mint_proposal_item_id`（:626） | create 的 id 一律由 server 配發 `item-{uuid8}`；模型不能指定或冒用既有 id | service.py:1484-1487 |
| 標題防重 | `_duplicate_title_owner`（:639） | create 標題撞 snapshot 或本輪已 staging 候選（casefold+空白正規化）→ 拒絕並回 `duplicate` outcome + 錯誤訊息導向 `edit_checklist_item` | service.py:1465-1483 |
| JSON reply 解析 | `_parse_chat_reply_payload`（:1373）＋`_reply_payload_object`（:1319） | 從裸 JSON / fenced JSON / 混雜文字萃取 `reply/proposal_status/conversation_focus`；**reply 內的任何 updated_items 一律忽略**（:2013-2015 註解） | service.py:1307-1387 |
| item 正規化 | `_normalize_rubric_items`（:500） | 別名容錯（detection/suggestion/bool detectable）；`missing_step_information` 缺口自動併入；`auto` 但無有效 check_steps → 降級 `manual`（非 partial，:563-571）；`auto` 但 `detection_method` 空缺 → `partial` + 缺口「腳本取證方式」；step 參數缺口（argv/success_criteria…）→ `auto` 降級 `partial`；`partial` 無缺口 → 補預設缺口文（:584-587）；export 用 `strip_auto_fallback=False`（:611） | service.py:510-608 |
| step 正規化＋修復 | `_normalize_check_steps`（:371） | 先走 `validate_check_steps` 白名單；**被丟棄的 step 若 argv 有效，server 端改寫成 `system.run_command` step 再驗證一次回收**（:425-472）— 模型不必重呼叫即恢復 | service.py:379-472 |
| 目錄白名單 | `validate_check_steps_with_issues`（template_command_service.py:165） | 只接受 `(template_key, command_key)` 能對上 enabled 目錄的 step；未啟用 → `unknown_command` issue、step 丟棄（或由上層回收） | template_command_service.py:165-239 |
| timeout 夾擠/coercion | `coerce_timeout_seconds`（:17）＋同上 | `system.run_command` timeout 非 1–300 整數 → 直接補 `DEFAULT_SYSTEM_COMMAND_TIMEOUT_SECONDS=30`；**其他 command** 的數字字串/整數浮點會被 coerce 後採用（:230-233） | template_command_service.py:13-45, 221-233 |
| 候選拒絕 | `_proposal_candidate_rejection`（:704） | ready_only 模式下三類拒絕：①auto 但 check_steps 驗證失敗（附可用目錄清單）；②manual 但未列缺口且 `system.run_command` 可用（capability review）；③partial 缺口全部屬「模型可自行補的參數」→ 給 `_PARAMETER_GAP_FIELD_HINTS` 欄位提示要求重呼叫工具 | service.py:704-757 |
| 提案比較 | `_PROPOSAL_COMPARE_FIELDS`（:760-769） | 8 欄逐欄比較（title/checked/detectable/judgement_mode/detection_method/fallback/missing_information/check_steps）— edit 無變更 → `no_change` outcome；**description 欄位已移除** | service.py:772-773, 1583-1600 |
| ready 宣稱修復 | `_READY_REMINDER_INSTRUCTION`（:346）+ forced tool_choice | 宣稱 ready 卻沒有 staging → 系統提醒重呼叫（最多 2 次）；第 2 次提醒直接 `tool_choice` 強制 `get_checklist_item`（有 target_item_id）或 `create_checklist_item` | service.py:1725-1766 |
| 最終攔截 | `_proposal_unavailable_reply`（:1022） | ready/文字宣稱建提案但無有效提案 → fallback 回覆（rejected_ops 給出原因；無 rubric 給固定訊息 `_NO_RUBRIC_READY_REPLY` :357） | service.py:2047-2081 |

**不再存在的舊機制**（已被工具通道取代）：`parse_chat_update`（updated_items JSON 解析）、`_resolve_add_candidates`/`_reassign_new_add_ids`（id 衝突確定性改寫 — 由 server 配發 id + duplicate 拒絕取代）、`_proposal_changes`（9 欄 diff — 由 `_PROPOSAL_COMPARE_FIELDS` 8 欄 staging 取代，**delete operation 不再提供**）、`proposal_repair_kind` 6 種修復迴圈（由 ready reminder × 2 + forced tool_choice 取代）、`_rubric_snapshot_for_repair` 快照注入、`needs_rubric_read` 修復旗標。

### 階段 3 — 追問細節（conversation_focus 跨輪記憶）

- **輸出契約**（prompt.py:115-133）：`proposal_status ∈ {ready, needs_information, unsupported, none}`；`conversation_focus` 帶 `turn_kind ∈ {question, requirement, follow_up}`，`requirements[]` 最多 **4 條**、每條 `focus_key/status/known_information/missing_information/target_item_id`，known/missing 各最多 3 條、每條 ≤30 字。
- **修剪**：`_conversation_focus_from_content`（service.py:74-131）— focus_key 截 40 字、known/missing 每條截 80 字、最多 4 條需求；status 由伺服器重推導（raw 宣稱 ready 且本輪真有提案才保留 ready，否則依 missing 清空與否 / 明確 unsupported → needs_information/unsupported/none）。
- **儲存**：寫進 assistant message `metadata_json["conversation_focus"]` + `source_file_id`（routes:555-559）。
- **跨輪注入**：`bounded_history`（session_service.py:605-716）反向掃描，找到同 `source_file_id` 的最新 focus，以 pseudo message「【目前未解需求焦點｜結構化資料，以最新對話與目前檢查表為準】」插入 history 倒數第二位（:683-701）。**這是 pending question 的記憶載體；不再依 status 過濾 requirement**（收斂改在生成端：4 條 × 3×80 字硬上限）。
- **歷史雜訊過濾**：`ui_hidden=True` 的訊息不進 history，但**當輪最新一則除外**（:644-653）；摘要 pseudo message 帶「【摘要結束；以下較新的對話與目前檢查表版本優先】」收尾（:705-715），注入前截 `SUMMARY_CONTEXT_CHARACTER_LIMIT=8000` 字（:702-704）。
- **缺口渲染**：`_teacher_missing_gap_reply`（service.py:947）以 marker 分類（位置缺口 / 通過方式缺口 / `_TEACHER_INTERNAL_GAP_MARKERS` 內部術語過濾）組成老師可讀文字，不外洩 schema 名稱。
- **防重複追問**：prompt.py:76 明令不得連續提出實質相同的問題；伺服器端 `_structured_requirement_needs_candidate`（service.py:134-153）— focus 指向具體需求（非 question turn）卻無缺口也無候選 → 計為「宣稱 ready 卻沒呼叫工具」，觸發 `_READY_REMINDER_INSTRUCTION` 重呼叫（含 `_structured_requirement_target_item` :156 找出指向的既有項目）。

### 階段 4 — 發起提案（工具呼叫 staging）

- **觸發**：`_run_proposal_tool_loop` 執行 `create_checklist_item`/`edit_checklist_item` 成功 → `staged_ops` 非空 → `updated_items = [entry["item"].model_dump() + operation]`（service.py:2013-2018）；**reply 文字不會產生提案**（註解 :2013-2014：「Proposals come exclusively from server-validated tool calls」）。
- **Schema**：每筆 `operation ∈ {add, update}`（**delete 已移除**）+ `TeacherJudgeRubricItem` 欄位（schemas.py:34-63，無 description）；回應欄位 `rubric_proposal` + `base_revision`（schemas.py:236-241）。
- **提案不落 DB**：`message_type="rubric_proposal"` 仍只有定義與 migration，route 中 assistant 一律寫 `chat`（routes:566-572）、失敗寫 `system_notice`（routes:579-588）— **提案純以 response 欄位交前端暫存，頁面刷新即消失**（有意契約，prompt.py:44/195 明言「暫存提案」「老師確認套用後才會寫入檢查表」）。
- **工具結果可視化**：每次工具呼叫都產生 teacher-facing `tool_calls` outcome（`read`/`staged`/`rejected`/`duplicate`/`no_change`），存進 assistant `metadata_json["tool_calls"]`（routes:560-563），與模型文案獨立。
- **狀態重推導**：`_proposal_status_claims_ready`（service.py:776）只信機器欄位不信文案；`_reply_claims_created`（:173）以文案 marker 判定「宣稱已建提案」— 無 staging 即為假，fallback 攔截（:2047-2081）。
- **部分成功補述**：部分 staging、部分 rejected → `_partial_failure_note`（:1077）把未解決項的拒絕原因附加在回覆尾端（:2036-2045）；server 端回收 step（`_recovered_catalog_item_titles` :841）→ 回覆改寫為固定「我已把…整理成提案」文案（:2030-2034）。
- **套用交接**：前端 `PATCH .../judge/files/{fid}/analysis` → `update_file_analysis`（file_service.py:169-197）→ `analysis_revision += 1`。此後所有帶舊 revision 的 chat 被 409 擋下 — **提案與 chat 的並發交接閘**。

### 階段 5 — 再編輯（edit / regenerate / revision）

- **message 不可編輯**：只有 GET list 與 DELETE（清空，routes:435 → `_reset_summary_state`）；修改語意 = 重送新訊息重走 pipeline。
- **revision 衝突三閘**：
  1. chat 請求 revision 預檢（routes:464-476）；
  2. **LLM 回傳後 revalidation**（routes:592-611）：refresh session → `ensure_active` → 比對 file id + `analysis_revision`，不符 → 409 `teacher_judge_context_changed`（防模型等待期間換來源/套用提案，舊回覆不落庫）；
  3. 套用提案 `expected_revision`（file_service.py:182-190）。
- **id 防偽**：create 提案的 id 一律由 `_mint_proposal_item_id`（service.py:626）server 端配發；edit 必須先讀表取得真實 id — 模型無法以任何 id 冒充既有項目（service.py:1543-1557）。
- **fork**：`fork_session_data`（session_service.py:486）只複製設定與 cloned file；標題「（副本 N）」（`_fork_title` :467）；fork 是唯一合法來源複製邊界（`ensure_selected_file_available` docstring，:284-288）。
- **附件**：綁 `message_id` 後不可移除（attachment_service.py:149-151）；再編輯需清訊息或換附件。

### 階段 6 — 全表潤飾（is_refine）與附件批次 itemwise

**(A) 全表潤飾 `is_refine=True`**：
- user/assistant 訊息都標 `ui_hidden=True`（routes:484, 565）— 前端不顯示；`bounded_history` 排除歷史 ui_hidden 訊息但保留當輪最新一則（session_service.py:644-653）。
- prompt：`SITUATION_REFINE`（prompt.py:216-275，審核一致性→可驗證性→補空白→保守語氣）+ `DIRECT_RUBRIC_UPDATE_INSTRUCTION`（prompt.py:212-214）。
- **強制讀表**：`_run_proposal_tool_loop(require_rubric=is_refine)` — 有 rubric 時 `tool_choice` 直接強制 `list_checklist`（service.py:1684-1688）；「全表潤飾是明確執行動作；即使內容不需修改，也要完成 `list_checklist` 後輸出 reply 總結」（prompt.py:274）。
- 差異點：`ready_only=False`（refine 允許非 auto 候選進提案，`_proposal_candidate_rejection` :713-716 直接放行）；`updated_items == []` 也算完成（SITUATION_REFINE 契約：只 `list_checklist` 無需修改 → reply 總結）。
- **保護**：沒讀表就想 `edit_checklist_item` → 工具回錯誤要求先讀（service.py:1551-1557）；`checked` 保守原則（prompt.py:242-246）。

**(B) 附件批次 itemwise**：`analyze_attachments_itemwise`（service.py:2311-2398）兩階段：
1. **Phase A** `extract_attachment_requirements`（:2143）— 只拆「來源檢查項目」不做判斷（`ATTACHMENT_EXTRACTION_SYSTEM_TEMPLATE` :136）；上限 `_ITEMWISE_MAX_ITEMS=50`（:2096），解析失敗整批回錯（`_parse_attachment_extraction` :2100）。
2. **Phase B** `analyze_requirement_item`（:2177）— 每列呼叫 `chat_with_rubric`（可帶 `teacher_message`）；`asyncio.Semaphore(_ITEMWISE_CONCURRENCY=2)` 限流（:2347）+ `asyncio.gather`（:2373）。
- 單列歸類 `_itemwise_result_from_chat`（:2227）：ready / teacher_review（`judgement_mode=teacher`）/ needs_information / unsupported / analysis_error；失敗列 `_itemwise_error_result`（:2273）不拖垮整批；缺口來源 `_itemwise_focus_missing`（:2210，從 conversation_focus 取）。
- 每列提案經同一工具核心：create 由 server 配 id；**edit 帶真實 item id**（前端 diff 保持 update 語意，:2244-2247 註解）。
- 設計意圖（routes:507-509 註解）：「one row's Ready reasoning cannot leak into the other rows」。

### 階段 7 — 狀態機

**Session**（models/teacher_judge_session.py:16-18）：`active ⇄ archived`（archived → active 需 PATCH `status="active"`，無獨立端點；archived 時除 status 外全部欄位變更 409，:244-247；archived 不可 pin :274-277；archive 時 pinned_at 強制清 None :271-272）。

**Message**：`role ∈ {user, assistant}`；`message_type ∈ {chat, rubric_proposal, system_notice}`（`rubric_proposal` 無寫入點；`system_notice` 不進 LLM 歷史 — `bounded_history` 排除，session_service.py:614-618）。assistant `metadata_json` 載 `metrics`，並視模式帶 `conversation_focus`/`item_results`/`tool_calls`/`ui_hidden`（routes:552-565）。

**Proposal**（非 DB 實體）：`none → needs_information → ready →（套用）revision+1`；側支：needs_information → unsupported（無安全取證能力）；ready 未套用即過期。

**附件**：`ready`（解析成功，可附加）／`failed`（不可附加）；綁定後不可移除。

**檔案**：`active ⇄ replaced`（replaced 不可再編輯，file_service.py:178-181）。

**Script**：`draft / review_failed / reviewed / approved / archived`；**Run**：`pending → running → completed | failed`（`cancelled` 已定義但無寫入路徑 — 死碼）。

### 2-A 歷史記錄：AI 提案 item id 解析邏輯（原 working-tree diff 分析，已提交後被取代）

> **狀態（2026-09-14）**：本節描述的 `_resolve_add_candidates`/`_reassign_new_add_ids` 等 working-tree 變更已於 `b0f145d8`..`855e536c` 提交進主線；隨後 `f2b6841f`「提案改以 server 端工具呼叫為唯一來源」把整條 JSON-parsing 提案路徑移除，**下列子節中與提案形成相關的條目（2-A-1/2-A-2/2-A-3/2-A-4/2-A-5/2-A-9）已不再是現行架構**，僅 2-A-7 的 itemwise 隔離設計與 2-A-6 的攔截診斷精神仍延續（後者改由 tool outcome 記錄取代）。保留原文以供追溯；現行架構見 §2-B。原文驗證紀錄：104 passed（backend/，2026-09-13）。

核心意圖：**把「add 候選的 id/標題碰撞」從「重試修復」降級為「伺服器端確定性解析」** — LLM 不再需要猜既有 id；猜錯的 id 不會冒充成 update；附件逐項模式連 rubric-read gate 都繞過。

#### 2-A-1 `_resolve_add_candidates`（service.py:570-641，新函式）— 一般模式伺服器端解析

輸入 `raw_items` + `rubric_context`，回傳 `(rewritten_items, add_only)`。決策表（對每個 add 候選）：

| 候選狀態 | 條件 | 伺服器改寫 | 依據 |
|---|---|---|---|
| operation 是 update/delete/remove | — | 原樣保留、`add_only=False` → 走 rubric-read gate | :609-612 |
| id 撞既有項目 **且** title 為空或 casefold 相同 | `current` 由 id 優先、title casefold 次之（:615-617） | 改寫為 `operation="update"` + **正式 id**（重述既有項目） | :618-629 |
| id 撞既有項目 **但 title 不同** | — | **不當 update**：視為「猜錯 id 的全新項目」→ 配新 id `item-new-N` + `operation="add"` | :619-622 註解明言 "must not masquerade as an update of it" |
| 無 id 或 id 已被占用 | `item_id in used_ids` | 配新 id `item-new-N`（`while` 跳號避撞，:630-638） | :630-638 |
| id 可用且未撞 | — | 保留原 id + add | :639-640 |

碰撞判定順序：`by_id`（:590-594）→ `by_title`（title.strip().casefold()，:595-599）；`used_ids` 從既有 id 集合起步（:600），配到的 fresh id 立即加入防重複。

#### 2-A-2 `_reassign_new_add_ids`（service.py:644-682，新函式）— 附件 itemwise 模式

- `coerce_to_add=True`：**所有候選強制 `operation="add"`**（含模型誤輸出的 update/delete）— 附件逐項列都是全新來源，不容許狀態操作（:669-670）。
- id 為空 **或** 撞 `current_ids` → 配新 `item-new-N`（:671-681）；其餘保留。

#### 2-A-3 rubric-read gate 的修改（`_proposal_requires_loaded_rubric`，:525-567）

- 簽名新增 `add_is_new: bool = False`（:528-529）。`add_is_new=True` 時 **跳過 id/title 碰撞檢查**（:558-562）— 註解：「附件逐項候選是全新來源列，模型不可能知道既有 id；add 的 id/title 撞名不是修改意圖」。
- 呼叫端新增 closure `_requires_loaded_rubric()`（:1604-1613）：加上 `not add_only_resolved` 條件 — **純 add 提案永不觸發 rubric-read gate**（`needs_rubric_read` :1615-1619 與修復後重算 :1758-1765 都改用它）。
- 純 add 批次因此**一次 LLM 呼叫完成**，不再進修復迴圈（回歸測試 `test_add_with_colliding_title_resolves_server_side_in_single_call` 斷言 `len(calls) == 1`）。

#### 2-A-4 修復路徑：快照注入取代強制 tool call（`_rubric_snapshot_for_repair`，:193-213）

- `_CURRENT_RUBRIC_SNAPSHOT_INSTRUCTION`（:184-190）：新修復指令，明言「系統已在下方訊息直接提供目前檢查表」。
- `_rubric_snapshot_for_repair`（:193-213）：把 `rubric_context` 壓縮成最多 80 筆 `{id, title, description}`（:198-206），空清單回 None。
- 修復訊息組裝（:1660-1698）：`snapshot_message` 以 system 訊息**附加在修復 prompt 尾端**；`require_rubric` 改為 `needs_rubric_read and not snapshot_message`（:1752-1756）— 已注入快照就不再強制 `tool_choice`；並明言 `repair_loaded = True`（:1758-1760）「不依賴模型發 tool call」。
- 效果：原本「修改既有項目但沒讀表」需要 3 次 LLM 呼叫（初答→強制 tool→重答）縮短為 **2 次**（初答→快照注入重答）；測試 `test_existing_item_proposal_without_tool_is_repaired_with_server_snapshot` 改名自舊 `..._retried_with_forced_read`，斷言 `len(calls) == 2`、payload 含「【目前檢查表】」。

#### 2-A-5 ready 宣稱失敗後的狀態重推導（:1835-1852）

模型宣稱 ready 但驗證後無有效提案時，新增依存活候選分類：

| 存活候選狀態 | 重推導結果 |
|---|---|
| 任一 `detectable=="partial"` | `needs_information` |
| `invalid_titles` 或有 **模型明確宣告** manual（`raw_detectability == "manual"`） | `unsupported` |
| manual 但未宣告（模型沒填 detectable 的草稿） | `needs_information`（老師可補位置/範圍） |

#### 2-A-6 攔截診斷（`_log_ai_intercept`，:517-522 + `_describe_raw_candidates`，:479-514）

- `_log_ai_intercept(event, **fields)`：統一格式 `Teacher Judge AI 攔截 <event> key=value...`（每值 400 字截斷）。
- 新事件：`stateful_proposal_without_rubric_read`（:1768-1775）、`ready_claim_without_valid_proposal`（:1814-1828）、`attachment_extraction_failed`（:1952-1957）。
- `_describe_raw_candidates`（:479-514）：診斷用摘要 — 每候選最多 10 筆，標記 `宣告修改或刪除`／`id 撞既有項目`／`標題撞既有項目`／`detectable=...`。

#### 2-A-7 itemwise 變更鏈

| 變更 | 位置 | 內容 |
|---|---|---|
| `teacher_message` 移除 | `analyze_requirement_item`（:1976 參數刪除）、`analyze_attachments_itemwise`（:2198）、route（teacher_judge_sessions.py:503-505 -1 行） | 逐列 prompt 只含該列 source title/description/evidence_hint — 老師原句不再混入每列，隔離更嚴 |
| `source_title` 回補 | `chat_with_rubric` 新參數（:1414-1415）；parse 後 `_UNNAMED_ITEM_TITLE` → `model_copy(update={"title": source_title})`（:1565-1570）；常數 `_UNNAMED_ITEM_TITLE="未命名項目"`（:345） | 模型退化沒回填 title 時，提案與 fallback 不再顯示「未命名項目」 |
| 缺口 fallback | `_itemwise_item_missing`（:2010-2020）：focus 缺失時改從 `result.normalized_items` 的 `missing_information` 取；「客觀成功條件」改寫成「通過方式（怎樣檢測才算正確）」（`_TEACHER_INTERNAL_GAP_REWRITES` :2000-2003） | needs_information 列的缺口來源雙保險（:2066-2067） |
| gap 渲染 | `_itemwise_gap_summary`（:2091-2135）：從 detail regex 抽「還缺少…」、合併「檢查位置」/「通過方式」旗標、殘餘項去重、「必要檢查資訊」兜底、2 項用「及」連接 | 老師可讀缺口文（測試斷言「缺少檢查位置」「請補充後再送出此項」） |
| reply 文案 | `_itemwise_reply`（:2138-2170）：ready→「可自動檢查，已列入提案」、unsupported detail 截 160 字、失敗列「分析失敗」且不把內部錯誤（AI 回覆失敗）直接曝光 | 測試 `test_attachment_itemwise_single_item_failure_keeps_other_proposals` 斷言 `assert "AI 回覆失敗" not in result.reply` |
| 逐列警告 | `analyze_attachments_itemwise` 尾端對非 ready 列 log warning（:2232-2241） | 可觀測性 |

#### 2-A-8 `bounded_history` focus 注入過濾（session_service.py:689-712）

- 注入前過濾：只保留 `status not in {"ready","none"}` 的 requirement（:694-705 註解：「ready 的已變成提案；none/空的無需行動；重新注入會讓模型重複提案前一輪」）。
- 全部已解 → **整個 focus 訊息不注入**（:706-708）。
- 測試：`test_bounded_history_skips_resolved_requirements_in_focus`（ready 項不見、needs_information 項保留）、`test_bounded_history_skips_focus_when_all_requirements_resolved`。

#### 2-A-9 其他觸發條件調整

- `_manual_candidates_needing_capability_review`（:813-828）：判定改為 `raw_detectability.get(item.id, "") != "auto"`（原 `== "manual"`）— 模型沒填 detectable 的 manual 草稿也進 capability review；抽共用 helper `_raw_detectability_by_id`（:759-770，含 `item-{index+1}` fallback id 對齊正規化邏輯）。
- `_normalize_rubric_items` title fallback 改用 `_UNNAMED_ITEM_TITLE` 常數（:363-366）。
- `TeacherJudgeChatResult` 新欄位 `normalized_items: tuple[...]`（:55）；chat_with_rubric 回傳時帶入（:1863）— itemwise 缺口 fallback 的資料來源。
- `allow_add_without_rubric` 模式下 system prompt 附加「# 本次新增邊界」段落（:1462-1468）：「只能用 operation: add、不要 update/delete、不要呼叫 get_current_checklist」。

#### 2-A-10 與既有章節的對應更新

| 既有章節 | 由此變更修改的行為 |
|---|---|
| §階段 2 表「add 候選 id 重寫」 | `_resolve_add_candidates` 取代原「id 撞既有 → 改寫 update」單向規則：新增「id 撞但 title 不同 → 仍為 add + 新 id」與「無 id → 伺服器配 id」，且純 add 不再觸發 rubric-read |
| §階段 2 表 `rubric_read` 修復 | 從「強制 tool_choice 重跑」改為「有快照 → 快照注入 + repair_loaded=True」（呼叫次數 3→2） |
| §階段 4 狀態重推導 | 由單純 needs_information/unsupported 二分改為三分（partial→needs_information、invalid/declared→unsupported、未宣告 manual→needs_information） |
| §階段 6B itemwise | `coerce_to_add=True` 強制 add；`teacher_message` 從逐列 prompt 移除 |

> （2-A-10 的對應關係於 tools-first 重構後再度失效：itemwise 已可對既有項目發 edit 提案，`teacher_message` 也重新併入逐列 prompt — 見 §階段 6B。）

### 2-B 現行架構：工具呼叫唯一提案通道（f2b6841f…855e536c）

commit 序：`f2b6841f`（tools-first 核心）→ `6938ff0c`（修復 json_object 封鎖工具呼叫）→ `86196d33`（移除 description 欄位、強化工具解析）→ `855e536c`（收斂 focus 規模、逾時 coercion、參數缺口提示）。

#### 2-B-1 四個提案工具（service.py:184-344）

| 工具 | 類型 | 關鍵規則 |
|---|---|---|
| `list_checklist` | 唯讀 | 回全表 `{id, title, detectable, judgement_mode}`（不含欄位內容）；帶參數即錯；把回傳 id 全數記入 `read_ids` |
| `get_checklist_item` | 唯讀 | 以 id 查單一項目完整內容；找不到 → 錯誤導向 `list_checklist`；記入 `read_ids` |
| `create_checklist_item` | 提案 | 只填 title 與已知欄位（`_PROPOSAL_FILL_PROPERTIES` :228-262）；id 由 server 配發；標題撞 snapshot/本輪 staged → `duplicate` 拒絕並導向 edit |
| `edit_checklist_item` | 提案 | 必帶既有 id 且**必須先讀過**（`read_ids` 閘）；只填有變動欄位；無變更 → `no_change` outcome |

工具 schema 由 `apply_thinking_control` 之外的 `base_request["tools"]/["tool_choice"]` 注入（:1682-1692）：有 rubric 時帶 4 工具並 pop `response_format`（json_object 與 native tool_calls 互斥，`6938ff0c` 的修復點）；無 rubric 時移除全部工具（`SESSION_NO_RUBRIC_INSTRUCTION` 對應）。

#### 2-B-2 工具迴圈（`_run_proposal_tool_loop`，service.py:1650-1841）

- 上限 `VLLM_CHAT_MAX_TOOL_ROUNDS`（預設 6，config.py:59-63）；每輪：呼叫 → `_extract_fenced_tool_calls`（:1245）把模型寫在 content 裡的 ```json/`<|tool_call|>` 工具呼叫還原成結構化 call（Qwen 家族相容，mirror pve_log.chat）→ 逐個 `_execute_checklist_tool` 分派 → tool result 以 `{"role":"tool"}` 訊息回灌 → 續跑下一輪。
- 沒有工具呼叫即視為最終回覆輪；若此輪宣稱 ready/已建提案且無 staging → 走 reminder（最多 2 次，第二次改 forced `tool_choice`）；預算耗盡仍不停呼叫工具 → 強制一輪無工具純文字回覆（:1819-1839），保證老師一定收到答案。
- 回傳 `(final_content, metrics, staged_ops, rejected_ops, tool_outcomes)`。

#### 2-B-3 拒絕原因與老師可讀化（部分失敗補述）

- `_proposal_candidate_rejection`（:704-757）三類拒絕文案（見 §階段 2 表）；rejected 的候選不進提案，但完整記錄在 `rejected_ops` 與 `tool_calls` outcome。
- 最終 fallback（service.py:2047-2081）：宣稱 ready 但無 staging → 有 rejected_ops 就用 `_proposal_unavailable_reply` 逐項說明原因；否則無 rubric 時改 `_NO_RUBRIC_READY_REPLY`，有 rubric 給通用「未建立提案」回覆 — 機器欄位不可信時保守降級，與 §4-11 AI reviewer 哲學一致。
- `_partial_failure_note`（:1077-1093）：部分 staging + 部分 rejected → 回覆尾端附加未解決項的具體缺口說明。

#### 2-B-4 itemwise 與既有項目的互動

itemwise 不再強制全部 add：模型可對 snapshot 既有項目呼叫 `edit_checklist_item`（同樣受 read_ids 閘約束），提案 op 帶真實 item id，前端 diff 保持 `update` 語意避免重複新增（service.py:2244-2247 註解）；`teacher_message`（老師原句）重新併入逐列 prompt 尾端（service.py:2194-2195、routes:511）— 與 2-A-7 當時「移除」的決定相反，以 commit `855e536c` 後的程式碼為準。

#### 2-B-5 測試現況（backend/，HEAD 855e536c）

`uv run python -m pytest tests/test_rubric_template_commands.py tests/test_teacher_judge_sessions.py -q` → **106 passed / 2 failed**。失敗的兩個測試（`test_complete_manual_system_info_candidate_reselects_generic_capability`、`test_invalid_step_then_manual_uses_distinct_capability_repair`）與程式碼 fixture 不同步：測試斷言回覆含「整理成提案」，但 fixture 的第二個 `create_checklist_item` 候選標 `judgement_mode=ai` 卻沒給 `success_criteria` — 依 `automation_support.missing_step_information`（:60-67）會缺「客觀成功條件」、被 `_proposal_candidate_rejection` 以可自行補齊的參數缺口拒絕，提案無法 staging。屬測試資料未跟上「參數缺口提示」行為（`855e536c`），不是執行環境問題。

---

## 3. LLM 呼叫與 prompt 組裝

- **Client**：`backend/app/infrastructure/ai/teacher_judge.py:6-10` 模組級 singleton `VLLMClient(base_url=settings.VLLM_BASE_URL, ...)` → `POST {base}/chat/completions`（vllm_client.py:49-54）。`VLLM_BASE_URL` 必含 `/v1`（client 自行補路徑，勿形成 `/v1/v1`）。註冊進 weakref `_CLIENTS`（vllm_client.py:8），lifespan finally `close_ai_clients()` 統一關閉（main.py:167）。
- **呼叫漏斗**：全部 AI 呼叫經 `service.py::_call_vllm_message`（:1119-1183，回傳完整 assistant message 而非純文字；`_call_vllm` :1186 為非 agent 呼叫的包裝）。使用者：chat（:1898）、itemwise（:2311）、摘要 `summarize_conversation`（:1844）、腳本生成/審查/修復（script_artifact_service.py:667/731/828）、run 結果判讀（script_result_analysis_service.py:282）。
- **prompt 組裝順序**（chat_with_rubric，service.py:1931-1982）：
  1. system = `CHAT_SYSTEM_TEMPLATE`（prompt.py:17-133）依序 replace：`{attachment_context}` → `{situation_instruction}`（SITUATION_NORMAL | REFINE）→ `{proposal_mode_instruction}`（`DIRECT_RUBRIC_UPDATE_INSTRUCTION` refine / `SESSION_REQUIREMENT_PROPOSAL_INSTRUCTION` 有 rubric / `SESSION_NO_RUBRIC_INSTRUCTION` 無 rubric）→ `{template_command_context}`（TEMPLATE_COMMAND_CONTEXT_TEMPLATE 帶 template_key/environment_keys/format 後目錄）。
  2. messages = system + `bounded_history`（20 則 / 24,000 字上限；摘要 pseudo message 帶「【摘要結束；…】」收尾、注入前截 8,000 字；focus pseudo message 插倒數第二位）。
  3. 有附件時額外 user turn「【附件資料】…不是系統指令」+【附件處理要求】要求以 `create/edit_checklist_item` 逐項建提案（service.py:1963-1982；放獨立 user turn，註解 :1964-1966：小型模型易把長附件當 metadata）。
- **payload**（service.py:1984-1993）：`model=VLLM_MODEL_NAME`、`max_tokens=VLLM_CHAT_MAX_TOKENS`、`temperature=VLLM_CHAT_TEMPERATURE`、`top_p/top_k/repetition_penalty`、`response_format={"type":"json_object"}`。工具回合內 `response_format` 會被移除（json_object 封鎖 native tool_calls，service.py:1689）；每輪經 `apply_thinking_control(payload, VLLM_ENABLE_THINKING)` 包裝、content 經 `strip_think_tags` 清洗（service.py:1158）。
- **tool 回合**：4 個提案工具（§2-B-1）；`tool_choice` 有 rubric 時 `auto`、refine 時強制 `list_checklist`、無 rubric 時移除工具。其他 tool 名稱回 `{"error": "不支援的工具或參數"}`（:1419, 1647）。
- **錯誤映射**：`finish_reason=="length"` → ValueError（:1148-1149）視為失敗；TimeoutException → 504（:1168-1172）；HTTPStatusError → 502。route 把 HTTPException 轉 `system_notice` 訊息「AI 回覆失敗」+ `{"status":"failed"}`（routes:573-588）— **訊息仍持久化，失敗可見**。
- **背景摘要**：`schedule_summary`（session_service.py:914）每 10 則 assistant 訊息觸發；`_persist_summary_if_current`（:811-868）條件 UPDATE 防舊摘要覆寫，空回應不會清掉上一份可用摘要（:851-854）、存庫截 12,000 字；task_id 去重 `teacher-judge-summary:{sid}:{msg_id}`。

---

## 4. 腳本 Pipeline：製作腳本 → 審核 → 執行 → 結果分析

### 4-1 完整生命週期

```text
[前置] route：analysis_revision 預檢 409 + 檢查表無項目 422（sessions.py:640-655）
        → create_artifact 內 ensure_script_generation_supported（automation_support.py:170，
          script_artifact_service.py:1460/1560）→ blockers（缺項/needs_review/manual/timeout）422
    ▼
[生成] create_artifact（script_artifact_service.py:1440）
   └ build_reviewed_script（:895）生成迴圈：
        ① generate_script_content（:667）→ system prompt 內嵌
           SCRIPT_GENERATION_CONTRACT_PROMPT（script_generation_contract.py:17）
           溫度 0.1、json_object → 輸出 {script_content, coverage}
        ② 靜態閘：check_script_policy（script_policy.py:357）
                   + check_script_quality（script_quality_validator.py:390）
        ③ 失敗 → _failure_signature（:491）簽章統計 → fix_script_content（:828）
           行區間 patch（patch 失敗 fallback 整份重生成 :1070-1076）
        ④ Coverage 閘：validate_coverage（script_coverage_validator.py:86）；
           patch 過先 realign_coverage_to_script（:151）
        ⑤ AI 審查 review_script_with_ai（:731）；不過 → hint → fix patch
        ⑥ 重試上限 4 次 / 同簽章 2 次（script_generation_contract.py:10-11）
           stop_reason：passed | same_failure_limit | total_retry_limit | unrecoverable_error
        ⑦ _resolve_status（:398）：policy+AI 雙過 → approved；否則 review_failed
[核准] approve_artifact（:1663）— 現行流程雙閘自動 approved，人工 approve 僅 reviewed 補救路徑
[執行] create_script_run（script_run_service.py:224）→ status=pending
   → submit(execute_script_run(run.id), task_id="teacher_judge_script_run:{run.id}")
     （in-process BackgroundTaskRunner，infrastructure/worker/background_tasks.py — 非 ARQ）
   → execute_script_run（script_executor_service.py:708）→ asyncio.to_thread(_execute_targets)
   → ThreadPoolExecutor ≤5 併發（:582-584）paramiko SSH root@target_ip
   → _execute_target_script（:234）：mkdir /tmp/campus-cloud-judge/{run_id}/{vmid}
     → SFTP 上傳 script.py → `python3 script.py > result.json 2> stderr.log`（60s timeout）
     → SFTP 讀回 → finally 遠端 cleanup（:280-292）
[驗證] _target_result（:329）：exit==0 + validate_managed_script_output（script_policy.py:337，
        teacher_judge_result.v1 Pydantic）→ completed；否則 python_missing/execution_nonzero/invalid_json
        （raw > 256KB → result_too_large）
[判讀] analyze_target_results（script_result_analysis_service.py:391）→ _analyze_one_target（:327）
   → _call_ai_judgement（:282）threading.BoundedSemaphore(10)（:22）經 _acquire_ai_slot（:275）、
     temp 0.0 → _validate_ai_judgement（:122）
   → _normalize_ai_judgement（:213）→ teacher_judge_ai_judgement.v1
[寫回] _save_analyzed_results（script_executor_service.py:640）：run.status=completed
   + result_summary_json + _touch_judge_session（:462，僅更新 last_activity_at）
[讀取] 前端輪詢 GET runs/{rid}（純 DB 輪詢，無 WebSocket）
[下游] ai_assignment_service._check_to_student（services/course/ai_assignment_service.py:88）
```

### 4-2 生成契約重點（script_generation_contract.py）

- 只允許單一 JSON：`{"script_content": "...", "coverage": [{"check_id", "rubric_item_ids"}]}`。
- 受管「只讀資料收集腳本」：必備 helper `truncate_output`（limit 4000，`RAW_OUTPUT_CHAR_LIMIT` :6）、`record_check`；外部命令需 `command_available`（`shutil.which`）+ `run_command`（`subprocess.run([...], timeout=...)`）。
- 狀態語意：`pass/fail` 需明確驗證；工具缺失/timeout/解析失敗 → `unknown`；不適用 → `skipped`；「stdout 非空 ≠ pass」。
- 輸出 JSON：`schema_version="teacher_judge_result.v1"`、metadata 含 timestamp+platform、`json.dumps(..., ensure_ascii=False)`；check id 必須語意化（如 `runtime.python_version`，禁 `check-1`）。
- `errors` 記錄規則（:45-58）：頂層 `errors: list[str] = []`；timeout/FileNotFound/Permission/HTTP 失敗/解析失敗/未預期例外**必須** `errors.append("check_id: 說明")` 且對應 check 不可 `pass`；errors 是給老師看的收集品質紀錄，不觸發腳本修正。

### 4-3 Coverage 與品質驗證

- `validate_coverage`（script_coverage_validator.py:86-148）純機械三查：①check_id 必須真實存在（AST 收集 `record_check` literal id，script_quality_validator.py:567）②rubric_item_ids 必須存在於 snapshot ③每個 rubric item 至少被一個 check 覆蓋（uncovered → fix_hint `cover_rubric_items`）。
- `check_script_quality`（script_quality_validator.py:390-564）AST 檢查 12 類（詳見 5-2 節）。

### 4-4 Template Command 機制（template_command_service.py）

**兩個 key 的角色**（對照 §階段 2 開頭的說明）：
- `command_key` = DB 目錄登錄的受控執行能力（引用目錄為優先）；`system.run_command` 的 parameters.argv 可**超出登錄目錄**（模型提出、伺服器驗證）。
- `template_key` = 系統資訊種類（平台環境：linux/python/n8n/postgresql），決定檢查針對哪個 stack。

- `TeacherJudgeTemplateCommand` model（models/teacher_judge_template_command.py:10-41）：`template_key/command_key/command_label/category/command_template/description/risk_level/requires_confirmation/enabled`；unique `(template_key, command_key)`。
- `SUPPORTED_TEMPLATE_KEYS = {"linux", "python", "n8n", "postgresql"}`（:12）— route 用來 normalize，不在集合直接 400（teacher_judge_scripts.py:60-66）。
- `GENERAL_COMMAND`（:61-75）：`system.run_command`、risk_level="executes_command"、requires_confirmation=True — 任何 template 都有的最低限度受控執行手段（`include_cross_template=True` 時保證存在，:99-103）。
- 嵌入方式：rubric 階段 `format_template_commands_for_prompt`（:115-146）注入 prompt；生成階段目錄快照進 `rubric_snapshot_json["template_commands"]`，並要求 `python.run_entrypoint`/`system.run_command` 只能用 check_steps.parameters 已驗證值，不得自行補值（script_artifact_service.py:149-156）。

### 4-5 結果分析（script_result_analysis_service.py）

- `_validate_ai_judgement`（:122-186）拒絕引用無法支持判定的輸出：item_id 屬於 rubric、不得重複、`evidence_refs` 必須是本次真實 check id、pass/fail 至少一個非 unknown/skipped ref、`judgement_mode=teacher` 強制 unknown、每個 rubric id 都必須被評。
- 輸出 `teacher_judge_ai_judgement.v1`（`_normalize_ai_judgement` :213-241）：含 `requires_teacher_review` / `teacher_review_item_ids`（來自 rubric item 的 `judgement_mode=teacher` 清單）。
- LLM 例外 → `_failed_judgement`（:254）；驗證未過/缺 parsed result → `_skipped_judgement`（:244）；執行前排隊先寫 `pending_judgement`（:265，`_with_pending_ai_judgement` executor :427）— 不拖垮 run。
- **run 結果不回寫 rubric item 或訊息流**，僅存 `target_results_json` 供查詢與下游投影；AI 判讀的 metrics 另經 `_record_result_ai_usage`（executor :476）記用量。

### 4-6 生成迴圈深挖：階段對應索引（§4-1 流程 ↔ 詳細小節）

| §4-1 流程步驟 | 對應深挖小節 | 一句話重點 |
|---|---|---|
| 生成迴圈（:895-1339） | §4-7 | 雙層 while + 四階段閘門 + 5 個狀態變數 |
| fix_script_content（:828-892） | §4-8 | 行區間 patch 協定：帶行號餵 LLM、六道驗證防線 |
| _failure_signature（:491-521） | §4-9 | 同簽章 2 次即停；不同失敗共用 4 次全域預算 |
| coverage 閘（:1089-1177） | §4-10 | 全有全無解析、realign 對帳、patch 不能補 coverage |
| AI 審查（:1180-1312） | §4-11 | 格式壞 → 一律 high；內圈重試共用預算 |

### 4-7 迴圈結構與重試預算（build_reviewed_script，script_artifact_service.py:895-1339）

外層 `while True`（:953）每圈走：**生成（或延用 patch 版）→ 靜態閘 → coverage 閘 → AI 審查 → 決定 patch 或通過**。內層 `while True`（:1180）只處理 AI 審查呼叫重試。

關鍵狀態變數（:915-952）：

| 變數 | 語意 | 邊界語意 |
|---|---|---|
| `script_content: str \| None` | None = 需全新生成；非 None = 延用（patch 版或過閘候選） | `script_content = None; continue` 是「重新生成」慣用手法（:1075-1079, :1176, :1304） |
| `last_gated_content` | 最近一次**進過靜態閘**的候選（:1009） | 終局 fallback（:1334-1336）— 即使流程失敗，artifact 仍保存最後進閘腳本供人查看 |
| `coverage` / `coverage_needs_realign` / `coverage_state` | patch 後映射不可信，需以實際 `record_check` id 對帳（:949-952 註解）；`coverage_state` 保存最後一次 coverage 驗證結果（approved/mappings/uncovered_items），最終併入 `gate_result["coverage"]`（:1324-1325）供 gate result 檢視 | 全新生成後 realign 重置 False（:1007）；驗證成功後也重置（:1128） |
| `retry_count` | **外層與內層共用同一預算**（4 次） | 生成/靜態/coverage/AI 呼叫/AI 內容五種失敗都消耗同一計數 |
| `failure_counts[signature]` | 同簽章計數（上限 2 次，:918, :491-521） | 見 §4-9 |

重試預算常數（script_generation_contract.py:10-15）：`SCRIPT_GENERATION_MAX_RETRIES=4`（總重試）、`SCRIPT_GENERATION_SAME_FAILURE_MAX_RETRIES=2`（同簽章）、`SCRIPT_GENERATION_MAX_ATTEMPTS=5`（相容舊名）。

可重試條件：`MODEL_CALL_RETRYABLE_STATUS_CODES = {502, 504}`（:542）— **503（model 未設定）直接 raise 不重試**（註解 :539-542：「setup problem, not a retry case」）。

### 4-8 fix_script_content patch 協定（:828-892）與 _apply_line_replacements 六道防線（:773-825）

**patch 流程**：
1. 腳本逐行編號 `0001|code`（:838-839）餵 LLM；`repair_instructions` = `_repair_instructions(fix_hints)`（:587-616）把 hint 轉成 `{issue, fix_goal, target, line_range?, snippet?, required_pattern?, suggested_fix?}`（行號來自 validator hint 的 AST `lineno`/`end_lineno`）。
2. LLM 回 `{"line_replacements": [{start_line, end_line, replacement}], "changes_summary"}`（FIX_SCRIPT_SYSTEM_PROMPT :231-258；明令只改指定區間、replacement 不得含行號前綴、except 區塊必須補 `errors.append` 且狀態不可 pass）。
3. `_apply_line_replacements`（:773-825）驗證後套用：

| # | 驗證 | 失敗 |
|---|---|---|
| 1 | replacements 非空 list | `no_line_replacements` |
| 2 | 每筆 dict 且 start/end 是 int（**排除 bool**）、replacement 是 str | `fix_format_invalid` / `fix_line_number_invalid` |
| 3 | `1 ≤ start ≤ end ≤ len(lines)` | `fix_line_out_of_range` |
| 4 | 排序後相鄰區間不得重疊 | `fix_line_overlap` |
| 5 | **reversed 順序套用**（尾端先換，避免位移後面行號，:818-820） | — |
| 6 | 結果 strip 後非空 | `fix_no_result` |

**patch 失敗降級鏈**：任何一道防線 502 → 外層捕 HTTPException → log "falling back to regenerate" → `script_content=None; continue`（靜態閘分支 :1070-1076、AI review 分支 :1299-1305）。`_feedback_snapshot` 已在 patch 嘗試前設定（:1057-1061），patch 失敗只是換修正手段、不浪費該輪 feedback。

### 4-9 失敗簽章機制（_failure_signature，:491-521）

- **hint 指紋**：每個 hint 取 `(type, target, field, function, command)` 五鍵 join → sha256 前 16 hex（`_cached_hint_fingerprint` :475-482，LRU 2048）。
- **issue 指紋**：`" ".join(issue.split()).lower()` 正規化 → 同前（:485-488）。
- 簽章 = `"{phase}:{hint_fp|issue_fp...}[:300]"`（去重；全空 → `unclassified`）；phase ∈ {generation, static, coverage, ai_review_call, ai_review}。
- 效果：同一個失敗第 3 次出現 → `same_failure_limit` 停機；每輪失敗原因不同則可繼續用滿 4 次預算。`_retry_summary`（:524-536）把計數與 stop_reason 寫進 `gate_result["retry_summary"]`。

### 4-10 Coverage 邊界情況（script_coverage_validator.py 全檔 177 行）

**解析（parse_coverage_payload :21-51）**：全有全無 — 非 list / **空 list** / 任一筆格式壞（非 dict、check_id 空白、item_ids 空、含非 str）→ 整體回 None，不保留合法部分；正規化 strip + item_ids 去重保序（:47）。回 None 的下游不是重試而是**合成 coverage 失敗**（「模型未提供 rubric 覆蓋映射」+ fix_hint `provide_coverage_mapping`，build_reviewed_script :1093-1107）→ 重新生成。

**合併（_merge_mappings :70-83）**：同 check_id 多筆映射合併（item_ids 去重聯集）— LLM 為同一 check 分列聲明覆蓋時防重複計數。

**三層機械驗證（validate_coverage :86-148）**：
1. 未知 check_id（映射引用的 id 不在 AST 收集的 literal 集，:104-105）→ issue + `fix_coverage_refs`；該 mapping 完全跳過（continue :106），其 item **不計入** covered。
2. 未知 rubric id（:112-116）→ issue + `fix_coverage_refs`；同時不污染 covered（covered 只在 `item_id in rubric_ids` 時累計，:107-111）。
3. 覆蓋完整性：`uncovered = rubric_ids − covered`（:129-137）→ issue + `cover_rubric_items`；缺口帶 id+title 老師可讀化（「未命名項目」fallback :132）。

**realign_coverage_to_script（:151-170）**：patch 後丟棄 check_id 已不存在的映射、保留其餘（由 `coverage_needs_realign` 控制只在 patch 後執行）。patch 可能改名/移除收集區塊 → item 失去最後映射 → 下一圈回報 uncovered → **重新生成而非 patch**（docstring :155-161 明言此設計）。**patch 不能補 coverage 缺口**：新增收集項目需要新 record_check + 新映射，行區間 patch 不適合 — 這是兩種修復手段的分界線（build_reviewed_script :1175-1176 註解）。

| 邊界情況 | 行為 |
|---|---|
| 生成沒回 coverage 欄位 / 空 list / 某筆格式壞 | 全有全無 → None → 合成失敗 → 重新生成 |
| coverage 引用改名後的 check id | realign 丟棄 → item 失去最後覆蓋 → uncovered → 重新生成 |
| patch 只改錯誤處理、id 不變 | realign 全保留 → 覆蓋不變 → 可過 |
| LLM 虛構映射 | check_id 不在 AST literal 集 → unknown → 重新生成 |
| record_check id 是動態字串拼出 | AST 只收 literal → 判 unknown → 重新生成 |
| item 真的無法取證 | 生成 prompt 明令不得虛構映射，讓驗證回報缺口（SCRIPT_GENERATION_SYSTEM_PROMPT :166） |

### 4-11 AI reviewer 硬失敗語意（_normalize_ai_review，:265-289）

- 回傳非 dict、`approved` 非 bool（`type() is not bool` 排除 bool 子類）、`risk_level` 不在三值、`issues` 非 list → **一律判 `{approved: False, risk_level: "high"}`**（:273-278）。
- **approved 需同時滿足「模型回 true 且 issues 為空」**（:280）— issues 非空會把 approved 壓成 False，issues 並非純記錄。
- 哲學與 chat 層 `proposal_status` 白名單過濾一致：機器欄位不可信時保守降級，絕不採信無法解析的審查結果。
- approved=True 時直接 `stop_reason="passed"` break（:1236-1238）。

### 4-12 重試時序圖（含 patch fallback）與邊界情況清單

```text
圈1: generate → 靜態閘 ✗（簽章S1×1）→ fix patch ✓ → realign 標記
圈2: patch 版靜態閘 ✓ → coverage ✗（uncovered: item-X）→ 重新生成
圈3: generate ✓ → 靜態 ✓ → coverage ✓ → AI review call 504 → 內圈重試（共用預算）
圈3: AI review 回 approved=false（issues）→ fix patch ✓
圈4: patch 版 → 靜態 ✓（realign 後 coverage ✓）→ AI review ✓ → passed → approved
```

若圈4 靜態閘同簽章第 3 次 → `same_failure_limit`；或 retry_count=4 → `total_retry_limit`。兩者都把 `last_gated_content` 存為最終腳本（:1334-1336），**失敗也不留空 artifact**。

邊界情況 / 潛在問題（本次深挖新發現）：

1. **retry 預算跨 phase 共享**：五種失敗共用 4 次全域計數，單類小故障也可能提前耗盡；一次請求最壞 ~8-12 次 LLM 呼叫（每次 30s timeout）。
2. **同步 HTTP 內的長迴圈**：整個迴圈在 `create_artifact` await 內同步執行，最壞數分鐘 request、無進度回報 — 前端需容忍。
3. **patch 精度依賴行號**：多 hint 同輪 patch 時，LLM 替換區間與 AST 行號有位移會被重疊檢查擋下 → fallback 重生成（安全但浪費一輪）。
4. **`last_gated_content` 的 coverage 語意**：coverage 失敗停機時存檔腳本是「靜態安全但覆蓋不完整」版本（執行被狀態機擋住，僅供審視）。
5. **`_resolve_status` 不直接看 coverage**：coverage 失敗不會走到 AI review，此時 `last_ai_review` 維持初始值 `{approved: False}`（:934-939）兜底使狀態仍為 review_failed — 邏輯正確但依賴初始值，追蹤時易誤判。
6. **順序鎖定**：AI review 只在 coverage 之後執行、coverage 在靜態閘之後 — 不存在「AI 過但靜態閘未過」的路徑。

---

## 5. 安全界線：攔截點全表

### 5-1 安全界線總表（33 條）

| # | 界線 | 位置（檔案：函式） | 時機 | 方式 | 失敗行為 |
|---|------|-------------------|------|------|---------|
| 1 | 身分認證 | deps/auth.py `get_current_user` | 入口 | JWT+type+jti 黑名單+token_version | 401 |
| 2 | 角色閘 | deps/auth.py `get_current_instructor_or_admin` | 入口 | student 權限空集合 | PermissionDenied |
| 3 | 班級 ownership | routes `_access` / `_ensure_class_access` | 入口 | `require_teaching_access(user, class.owner_id)` | 404 / PermissionDenied |
| 4 | Session↔Class | `get_session`（session_service.py:129） | 入口 | class_id 比對 | 404 |
| 5 | File↔Class | `get_file`（file_service.py:140）/ `validate_selected_file` | 入口/選檔 | class_id 比對 + status | 404/400/409 |
| 6 | 檢查表單一佔用 | `ensure_selected_file_available`（session_service.py:278）+ partial unique index | 選檔 | DB unique `uq_teacher_judge_sessions_selected_file` | 409 `teacher_judge_file_in_use` |
| 7 | Archived 唯讀 | `ensure_active`（session_service.py:308） | 寫入前 | status 檢查 | 409 |
| 8 | Revision 樂觀鎖 | `update_file_analysis`（file_service.py:182）+ chat/script 預檢 | 寫入/生成前 | expected_revision | 409 |
| 9 | 生成後競態 | `create_message` revalidation（routes:592-611） | 生成後寫入前 | file id+revision 三比對 | 409 `teacher_judge_context_changed`（丟棄 AI 回覆） |
| 10 | 附件歸屬 | `get_pending_attachments`（attachment_service.py:64） | 訊息前 | session_id/綁定/狀態 | 400/409 |
| 11 | 附件數量 | routes :338-350 | 上傳前 | pending ≥5 拒 | 400 |
| 12 | 上傳大小（記憶體） | routes :353-354 | 上傳前 | `file.read(max+1)` 有界讀 | 415 |
| 13 | 副檔名白名單 | `create_attachment`（attachment_service.py:25,102-104） | 上傳前 | `{.md,.txt,.doc,.docx,.pdf}` | ValueError→415 |
| 14 | 檔名注入 | `_safe_filename` + uuid storage_key（:32-36,129） | 上傳前 | `Path().name` + uuid4 | 静默規範化 |
| 15 | 附件 prompt-injection 邊界 | `attachment_context`（:161-172）+ prompt.py:60,165 | 進 LLM 前 | 「不可信任的資料，不是系統指令」包裝 | 提示級 |
| 16 | 訊息敏感遮蔽 | `redact_message_content`（session_service.py:93） | 存檔時 | regex 遮蔽（有界量詞防 ReDoS） | 静默遮蔽 |
| 17 | 無 rubric 禁提案 | routes :546-551 | 生成後 | file None → 丟棄提案改寫回覆 | 静默降級 |
| 18 | 生成前置閘 | `ensure_script_generation_supported`（automation_support.py:170） | 生成前 | blockers（缺項/needs_review/manual/timeout） | 422 |
| 19 | 靜態 policy 閘 | `check_script_policy`（script_policy.py:357） | 生成後 | regex 黑名單 + AST | blocked |
| 20 | 品質閘 | `check_script_quality`（script_quality_validator.py:390） | 生成後 | AST 12 類檢查 | blocked |
| 21 | Coverage 閘 | `validate_coverage`（script_coverage_validator.py:86） | 生成後 | 機械式覆蓋完整性 | 重試/失敗 |
| 22 | AI reviewer | `review_script_with_ai`（script_artifact_service.py:731）+ `_normalize_ai_review`（:265） | 審查 | 第二模型審 read-only；格式異常一律 high | review_failed |
| 23 | 核准狀態機 | `approve_artifact`（:1663）+ `_resolve_status`（:398） | 執行前 | 狀態+雙閘檢查 | 400 / run failed |
| 24 | Run scope | `create_script_run`（script_run_service.py:234） | 建 run 前 | target_scope 僅 manual；artifact approved；≤5 台 | 400 |
| 25 | VMID 成員閘 | `_class_member_by_vmid`（:74）+ `_resolve_running_targets`（:146） | 建 run 前 | 本班成員 + `resource.user_id == member.user_id` + running | 400 |
| 26 | 執行時二次驗證（TOCTOU） | `_resolve_runtime_target`（script_executor_service.py:182） | 執行前 | DB/擁有者/running/type/IP/key 全驗（6 個 reason_code） | TargetExecutionError |
| 27 | IP 來源限制 | `resolve_target_ip_address`（target_ip_resolver.py:16） | 執行前 | 只信 DB 快取或 Proxmox live，不採信呼叫端 | 空字串→`missing_ip` |
| 28 | SSH 沙箱 | `_execute_target_script`（:234） | 執行時 | 固定 REMOTE_ROOT + `shlex.quote`、無 sudo、無 shell 拼接、60s timeout、finally 清理 | 失敗記錄 |
| 29 | 輸出 schema 閘 | `validate_managed_script_output`（script_policy.py:337） | 輸出後 | Pydantic 欄位上限 | valid=False → failed |
| 30 | 輸出大小 | `_target_result`（script_executor_service.py:338-354） | 輸出後 | 256KB 上限、stdout/stderr 16KB | failed |
| 31 | AI 判讀節流 | `_AI_ANALYSIS_SLOTS` threading.BoundedSemaphore(10)（script_result_analysis_service.py:22，經 `_acquire_ai_slot` :275 非阻塞輪詢） | 判讀前 | semaphore + 截斷 4000 字 | 排隊 |
| 32 | 歷史長度 | bounded_history（session_service.py:55-58） | 組 prompt 前 | 20 則/24,000 字、摘要注入 8,000 字（存庫 12,000 字） | 截斷 |
| 33 | 生成重試上限 | `_failure_signature`（script_artifact_service.py:491） | 生成流程 | 失敗簽章去重 + 上限 | stop_reason |

### 5-2 script_policy.py 逐條規則（backend/app/ai/teacher_judge/script_policy.py）

**B-1 文字 regex 黑名單 `DENY_PATTERNS`（:55-70，`re.IGNORECASE|re.DOTALL`，搜尋前 `lower()`）**：

| regex | 拒絕訊息 |
|---|---|
| `\brm\s+-rf\b` | 禁止使用 rm -rf 刪除檔案 |
| `\bdel\s+/s\b` | 禁止使用 del /s |
| `\bremove-item\b.*\b-recurse\b` | 禁止 Remove-Item -Recurse |
| `\bfind\b.*\b-delete\b` | 禁止 find -delete |
| `\bdrop\s+database\b` | 禁止刪除資料庫 |
| `\btruncate\s+table\b` | 禁止清空資料表 |
| `\bdelete\s+from\b(?![^;\n]+\bwhere\b)` | 禁止無條件 delete from |
| `\bshutdown\b` / `\breboot\b` | 禁止關機/重啟 |
| `\bapt(?:-get)?\s+install\b`、`\bpip\s+install\b`、`\bnpm\s+install\b` | 禁止安裝套件 |
| `\bchmod\b\|\bchown\b\|\bsystemctl\s+(enable\|disable\|restart\|stop\|start)\b` | 禁止改系統設定 |
| `\breset\b\|\bcleanup\b\|\bclean\s+up\b\|\bfix\b\|\brepair\b` | 禁止修復/清理/重設類操作 |

**B-2 token 級檢查 `_dangerous_command_issue`（:313-334）**：shell launcher 黑名單（`SHELL_LAUNCHERS` :82-93：bash/sh/zsh/powershell/pwsh/cmd/dash/fish…）；git write 子命令黑名單（`GIT_WRITE_SUBCOMMANDS` :94-124：commit/push/reset/…）；`rm -r/-rf/-fr`；`find -delete`；`shutdown`/`reboot` 首token；`apt/pip/npm install`。

**B-3 AST 呼叫黑名單 `DENY_AST_CALLS`（:126-151，import alias 解析 :183-203）**：
`os.system`/`os.popen`（禁 shell）；`os.remove`/`os.unlink`/`os.rmdir`/`pathlib.Path.unlink`/`rmdir`（禁刪除）；`Path.write_text/write_bytes/rename/replace/chmod`（禁寫入/移動/改權限）；`shutil.rmtree/move/copy/copy2/copyfile`；`socket.socket`/`socket.create_connection`；`requests.Session`/`httpx.Client`/`httpx.AsyncClient`（禁重用 session）；`subprocess.call`（要求改 run+timeout）；`subprocess.Popen`。

**B-4 檔案開啟（:408-414）**：`open`/`io.open`/`Path.open` mode 含 `w|a|x|+` → 禁寫入模式。

**B-5 subprocess 強制（:415-432）**：`shell=True`（僅攔字面 True，:222-227）→ 禁；字面 argv 過 `_dangerous_command_issue`；無 `timeout` keyword → 強制要求。

**B-5' 網路白名單（NETWORK_CALLS :154-170 + WRITE_NETWORK_CALLS :171-180，`_network_issues` :298-310）**：`.request` 只允許 GET/HEAD；`WRITE_NETWORK_CALLS`（requests/httpx post/put/patch/delete）與 method 推導為 POST/PUT/PATCH/DELETE 一律禁；必須有 timeout；URL 必須 literal `localhost/127.0.0.1/::1`。

**B-7 無限迴圈（:433-437）**：`while` test 為字面 `True` → 禁。

**B-8 輸出契約（:368-388）**：必含 `teacher_judge_result.v1`、`print(`、`checks`、`errors`、`metadata`、`timestamp`、`platform`；`ManagedScriptResult` Pydantic（:40-52）欄位上限 id≤120/title≤240/evidence,raw≤4000/summary≤2000/status 五值。

**script_quality_validator 12 類檢查（check_script_quality :390-564）**：語法；必備 helper（`truncate_output`/`record_check` 必需，`command_available`/`run_command` 僅在真用外部命令時）；`record_check` 內必經 `truncate_output`；`command_available` 內必用 `shutil.which`；`run_command` 內必提 `returncode`；必呼叫 `record_check`；必 `json.dumps(ensure_ascii=False)` + metadata timestamp/platform；每個 literal `subprocess.run` 命令都要有對應 `shutil.which`；工具缺失分支 status 不可 `warning`（應 unknown）；timeout/FileNotFound/Permission 例外不可標 warning；except 不可吞成 pass；bare/Exception except 必須 `errors.append`（唯一豁免：run_command helper 內回傳 dict 的 handler，`_allows_helper_scoped_generic_except` :324-331）。

### 5-3 pve_log 對照（backend/app/ai/pve_log/ssh_guard.py + command_policy）

**ssh_guard `_BLACKLIST_RULES`（:18-31）**：`rm -rf` 任意旗標排列、`mkfs`、`dd of=/dev/`、`shutdown|reboot|halt|poweroff|init 0|init 6`、`passwd`、fork bomb `:(){:|:&};:`、全域 chmod 777、`> /dev/sda`、`curl|wget | sh`（下載執行）、`systemctl stop/disable sshd/network`、`iptables -F|ufw reset|nft flush`。

**執行三層（ssh_exec.py:528-585）**：層一黑名單（:543-554）→ 層二 `allowed_vmids`（:556-564）→ 層三 `require_confirm` pending token（:566-583）。token 綁定 requester/scope/allowed_vmids（:617-632）、一次性消費（:633-641）、TTL 300s、覆寫指令再過黑名單（:657-675）。

**pve_template 白名單（command_policy.py）**：`is_known_read_command`（:34-52）fullmatch 唯讀指令自動核准（cat os-release、df -h、ss -lntp、python3 --version…）；清單外一律 pending 人工確認。固定 probe（guest_diagnostics.py:73-124）由後端產生，模型不可控。

### 5-4 兩模組設計對照

| 面向 | teacher_judge | pve_log / pve_template |
|---|---|---|
| 威脅模型 | LLM **生成的程式** 會以 root 放到學生 VM → 防生成碼越權 | 單條 SSH 指令 → 防指令越權 |
| 攔截哲學 | 多層閘門（deny-regex + AST + AI reviewer + approve 狀態機），**生成即審查**，通過後可重複執行 | 黑名單 regex + **每條執行的人工確認閘**（pending token），單次有效 |
| 目標授權 | 班級成員表 + owner 比對 + 執行時二次驗證（TOCTOU） | resource ownership 或 allowed_vmids frozenset 綁 token |
| 確認流程 | 無 per-run 確認（approve 一次授權多次執行） | TTL 5 分鐘、一次性、scope 綁定 |
| 輸出紅線 | teacher_judge_result.v1 schema + 256KB，**stdout/stderr 原樣不遮蔽** | 自由文字 + **祕密遮蔽**（password/PEM）+ 16KB |
| 租戶隔離 | `teaching_class_id` 全鏈路 FK | AdminUser 全域 / pve_template per-VM ownership |

### 5-5 權限矩陣

`permissions.py:30-40`：student = 空集合；teacher = `{VM_REQUEST_USE_IMMEDIATE_MODE, TEMPLATE_MANAGE, CLASSROOM_MONITOR}`；admin = 全部 + `TEACHING_OWNERSHIP_BYPASS`。

| 操作 | Student | Teacher（班主） | Admin |
|---|---|---|---|
| 列/讀 sessions、files、scripts、runs、messages | ❌ | ✅ 本班 | ✅ bypass |
| 上傳附件 / 送訊息 / fork / 匯出 | ❌ | ✅ | ✅ |
| 生成 / regenerate / approve / 建 run | ❌ | ✅ | ✅ |
| 刪除 session/file/script/attachment | ❌ | ✅ | ✅ |
| pve_log chat/ssh/confirm | ❌ | ❌ | ✅ |
| pve_template chat | ❌ | ✅ per-VM | ✅ |

### 5-6 潛在薄弱點（安全觀察，非現行 bug）

1. **靜態 policy 是 best-effort 黑名單**：`_literal_command_text`（:236-246）只查字面 argv，變數拼接 argv 繞過；`getattr/os.execv/eval/exec/ctypes` 不在 DENY_AST_CALLS；`shell=1` 可繞 `shell=True` 檢查 — 由 AI reviewer 補，但 reviewer 本身是提示詞強度防線。
2. **輸出不遮蔽祕密**：AI_REVIEWER_SYSTEM_PROMPT 明言 stdout/stderr 不遮蔽（script_artifact_service.py:211-213）— 腳本可讀 root 可讀檔放進 evidence/raw；與 pve_log 的 redact 層不對稱（限於學生自己 VM，per-resource SSH key 隔離）。
3. **`/tmp/campus-cloud-judge` symlink 競態**：學生 VM 上預置 result.json symlink 可污染證據（影響侷限本人 VM）。
4. **Excel 公式注入**：`export_to_excel`（export.py:32-106）直接寫入 `=` 開頭字串；rubric.py 端點僅 `items min_length=1`，無長度驗證（輸入會先過 `normalize_items_for_export`，rubric.py:32）。
5. **approve 非必經人工**：`create_artifact` 雙閘通過即自動 `approved`（:1493-1500）— 執行授權可無人工介入，屬有意取捨但需在威脅模型明示。
6. **附件不做祕密遮蔽**：`extracted_text` 只截 12000 字，未套 `redact_message_content` — 與訊息本體不一致。
7. **`while True` 禁令可繞**（`while 1:` 可過）；實際由 60s SSH timeout 兜底。
8. pve_log pending token 存記憶體 dict（ssh_exec.py:70-71 自承 dev-grade），多行程部署失效 — teacher_judge 不受影響（run 狀態落 DB）。

### 5-7 資料隔離

- `teaching_class_id` 是全鏈路外鍵（sessions/files/artifacts/runs/submissions 全部 FK CASCADE → teaching_classes）；查詢端一律帶 class 過濾（file_service.py:132-135、script_artifact_service.py:1349-1351、sessions.py:676-682 雙重過濾）。
- 跨班存取在每層都落到 404（class 比對先於內容回傳）。
- fork 是唯一合法複製邊界；刪除時 `other_session` guard 防誤刪（session_service.py:155-167）。

---

## 6. 資料模型與設定

### 6-1 Models（backend/app/models/）

| Model | 表 | 關鍵欄位 / 約束 |
|---|---|---|
| `TeacherJudgeSession` | teacher_judge_sessions | `status(active/archived)`、`selected_file_id` FK、`summary` 三欄摘要游標；**partial unique `uq_..._selected_file`**（:49-55） |
| `TeacherJudgeSessionMessage` | teacher_judge_session_messages | `role`、`message_type(chat/rubric_proposal/system_notice)`、`metadata_json` |
| `TeacherJudgeSessionAttachment` | teacher_judge_session_attachments | `storage_key` unique、`status(ready/failed)`、`message_id`（pending 未綁） |
| `TeacherJudgeFile` | teacher_judge_files | `analysis_revision`（樂觀鎖，default 1）、`analysis_json`、`status(active/replaced)`；partial unique active 檔名（:36-43） |
| `TeacherJudgeScriptArtifact` | teacher_judge_script_artifacts | `rubric_snapshot_json`（含 template_commands 快照）、`script_content`、`source(ai_generated/regenerated)`、`version`、`status(draft/review_failed/reviewed/approved/archived)`、`policy_check_result_json`、`ai_review_result_json` |
| `TeacherJudgeScriptRun` | teacher_judge_script_runs | `target_scope(all_with_vm/running_only/manual)`、`status(pending/running/completed/failed/cancelled)`、`progress_json`、`target_results_json` |
| `TeacherJudgeTemplateCommand` | teacher_judge_template_commands | `command_label/category/description`；unique `(template_key, command_key)`；`requires_confirmation` 預設 True；`risk_level` 預設 read_only |
| `TeacherJudgeStudentSubmission` | teacher_judge_student_submissions | unique `(artifact_id, student_id)`；docstring「never starts an AI run」— 學生提交僅狀態標記 |

### 6-2 設定（backend/app/ai/teacher_judge/config.py → ai/system_config.py）

| 設定 | 預設 | 用途 |
|---|---|---|
| `VLLM_BASE_URL` | `http://localhost:8000/v1` | **必含 /v1**；client 自補 /chat/completions |
| `VLLM_API_KEY` | （示例值） | Bearer token |
| `VLLM_MODEL_NAME` | `""` | 空字串 = 未設定 → chat/生成/判讀 503（service.py:1917） |
| `VLLM_TIMEOUT` | 30s | 每次模型呼叫 |
| `VLLM_TEMPERATURE` | 0.6 | chat 無專設時退回 |
| `VLLM_CHAT_TEMPERATURE` | None（→ 0.6） | chat 專用溫度 |
| `VLLM_TOP_P` / `VLLM_TOP_K` | 0.95 / 20 | 取樣 |
| `VLLM_REPETITION_PENALTY` | 1.0 | 重複懲罰 |
| `VLLM_MAX_TOKENS` | 1600 | chat 無專設時退回 |
| `VLLM_CHAT_MAX_TOKENS` | None（→ 1600） | chat 專用上限（摘要取 min(·,768)、AI 判讀取 min(·,2048)） |
| `VLLM_CHAT_MAX_TOOL_ROUNDS` | None（→ 6） | 提案工具迴圈輪數上限（service.py:1703） |
| `VLLM_ENABLE_THINKING` | False | 是否保留 thinking 訊息（apply_thinking_control） |
| `VLLM_MAX_UPLOAD_SIZE_MB` | 10 | 附件/檢查表上傳上限 |

程式內常數：`SUPPORTED_TEMPLATE_KEYS`（4 模板）、`ALLOWED_SUFFIXES`（5 格式）、`MAX_ATTACHMENT_COUNT=5`、`SUMMARY_TURN_INTERVAL=10`、`SUMMARY_CONTEXT_CHARACTER_LIMIT=8000`（摘要注入上限；存庫截 12,000）、itemwise 併發 2 / 上限 50 列、AI 判讀併發 10、run targets ≤5、SSH 併發 ≤5、SSH timeout 60s、訊息 20,000 字、歷史 24,000 字、生成重試 4/同簽章 2。

### 6-2' 背景任務（重要：非 ARQ）

teacher_judge **不用 arq queue**（ARQ worker 只註冊 template 任務，infrastructure/queue/worker.py:19-33）。兩種背景工作都走 in-process `BackgroundTaskRunner`（infrastructure/worker/background_tasks.py，全域 semaphore 併發 8；runner 本身另支援 `submit_sync`/`submit_factory` 的 retry/backoff、task 取消與 shutdown 等待 — teacher_judge 兩個任務都用不帶 retry 的 `submit()`）：

| task_id | coroutine | 觸發 |
|---|---|---|
| `teacher_judge_script_run:{run_id}` | `execute_script_run`（script_executor_service.py:708） | scripts.py:199-203 / sessions.py:778-782 |
| `teacher-judge-summary:{sid}:{msg_id}` | `run_summary_job`（session_service.py:871） | 每 10 則 assistant 訊息（session_service.py:914） |

---

## 7. 全流程 × 安全界線對照矩陣

| Pipeline 階段 | 主要函式（file:line） | 該階段的安全界線 | 失敗產物 |
|---|---|---|---|
| chat 傳送需求 | `create_message`（sessions.py:452） | #2 角色閘、#3 班級、#4 session 綁定、#7 archived、#8 revision 預檢、#10-11 附件、#16 敏感遮蔽、#32 歷史上限 | 401/403/404/409/422 |
| LLM 呼叫 | `chat_with_rubric`（service.py:1898） | #15 注入邊界、#16 遮蔽、#32 bounded_history、無 rubric 不提供工具 | 502/504 |
| 指令審核 | `_execute_checklist_tool` staging（service.py:1390）+ `validate_check_steps_with_issues`（template_command_service.py:165） | 白名單目錄、timeout 1–300 夾擠/coercion、unknown_command 丟棄、id server 配發、edit 讀表閘 | 拒絕回覆 + tool outcome rejected |
| 提案形成 | staging ops（service.py:2013-2018）+ `_proposal_candidate_rejection`（:704） | ready_only 過濾、參數缺口可回收提示、標題防重、ready 宣稱 fallback（:2047-2081） | 提案丟棄 + 老師可讀拒絕說明 |
| 追問細節 | `_conversation_focus_from_content`（service.py:74） | focus 修剪上限、focus 以資料身分注入（非指令） | — |
| 提案套用 | `update_file_analysis`（file_service.py:169） | #8 expected_revision 樂觀鎖、#5 file status | 409 |
| 全表潤飾 | `is_refine` 分支（routes:484, service.py:1684-1688 require_rubric） | 強制 `list_checklist` tool_choice、無 rubric 禁提案（routes:546-551） | 工具錯誤導向讀表 |
| 生成前置 | `ensure_script_generation_supported`（automation_support.py:170） | #18 blockers | 422 |
| 腳本生成 | `build_reviewed_script`（script_artifact_service.py:895） | #19 policy、#20 quality、#21 coverage、#22 AI reviewer、#33 重試上限 | review_failed + fix_hints |
| 核准 | `_resolve_status`（:398）/ `approve_artifact`（:1663） | #23 狀態機 | review_failed |
| 建 run | `create_script_run`（script_run_service.py:224） | #24 scope/approved/≤5、#25 成員閘 | 400 |
| 執行 | `execute_script_run`（script_executor_service.py:708） | #26 二次驗證、#27 IP 來源、#28 SSH 沙箱、60s timeout | TargetExecutionError |
| 輸出驗證 | `_target_result`（:329） | #29 schema 閘、#30 大小限制 | target failed + reason_code |
| AI 判讀 | `analyze_target_results`（script_result_analysis_service.py:391） | #31 節流、_validate_ai_judgement（:122） | skipped/failed_judgement |
| 匯出 | `export_to_excel`（export.py:32） | items ≥1（rubric.py:35-36）；⚠ 公式注入見 5-6 | 400 |

---

## 8. 觀察到的非對稱 / 值得留意點

1. `rubric_proposal` message_type 已遷移但**無寫入點** — 提案僅活在 response，套用依賴前端主動 PATCH（有意契約，但頁面刷新即失）。
2. `reviewed` artifact 狀態在現行自動流程**沒有生產者**（`_resolve_status` 只回 approved/review_failed）；`approve_artifact` 人工路徑實際不可達，僅 legacy 分支（:1492-1500/:1601）相容。
3. `cancelled` run 狀態是**死碼**（enum 有定義、無取消 API、無寫入路徑）。
4. `target_scope` schema 允許 `all_with_vm`/`running_only`，service 強制只收 `manual`（script_run_service.py:234-235）— 保留值；`create_script_run` 另有 `requested_item_id` 可選參數（:232）。
5. `archived → active` 可經 PATCH `status="active"` 解封，但無獨立 unarchive 端點（與 `archive_session` 不對稱）。
6. 腳本生成是**同步 HTTP 請求**（佔用 request 直到生成迴圈結束），執行才是背景 — 前端需容忍長 request。
7. 生成/審查/AI 判讀/chat 共用同一 `VLLM_MODEL_NAME` 單模型；模型名以目標 `/v1/models` id 為準。
8. **chat 提案不再提供 delete operation**（工具只有 create/edit）— 刪除檢查項目只能由老師在檢查表 UI 手動進行；舊文件與前端若仍假設 `delete` op 需同步。
9. 有工具回合時 chat 不送 `response_format=json_object`（互斥，service.py:1689）；結構化 reply 靠 `_reply_payload_object` 對 fenced/裸 JSON 的容錯解析，模型仍可能退化為純文字（此時 proposal_status 為 None）。
10. 測試 `test_rubric_template_commands.py` 有 2 個 fixture 落後於「參數缺口提示」行為（§2-B-5）— HEAD `855e536c` 上 106 passed / 2 failed。

---

## 9. 驗證紀錄

- 4 個 subagent 完成：chat pipeline（service.py 全讀）、腳本 pipeline（script_artifact_service.py 1,750 行全讀）、安全界線（policy 對照 pve_log）、API+模型地圖。
- **2026-09-14 修訂驗證（HEAD `855e536c`）**：全文件行號與行為以 `rg` + 實讀重驗。吻合項（抽驗行號）：`chat_with_rubric` service.py:1898、`_run_proposal_tool_loop` :1650、`_execute_checklist_tool` :1390、`_normalize_rubric_items` :500、`_normalize_check_steps` :371、`_mint_proposal_item_id` :626、`_duplicate_title_owner` :639、`_proposal_candidate_rejection` :704、`_PROPOSAL_COMPARE_FIELDS` :760、`_proposal_unavailable_reply` :1022、`_conversation_focus_from_content` :74、`_reply_payload_object` :1319、`summarize_conversation` :1844、`analyze_attachments_itemwise` :2311、`bounded_history` session_service.py:605、`schedule_summary` :914、`_persist_summary_if_current` :811；`create_artifact` :1440、`_resolve_status` :398、`_failure_signature` :491、`generate_script_content` :667、`review_script_with_ai` :731、`fix_script_content` :828、`build_reviewed_script` :895、`regenerate_artifact` :1533、`approve_artifact` :1663、`archive_artifact` :1694、`delete_artifact` :1738、`ensure_script_generation_supported` 呼叫點 :1460/:1560；`redact_message_content` :93、`get_session` :129、`ensure_selected_file_available` :278、`ensure_active` :308、`fork_session_data` :486；script_policy 的 `DENY_PATTERNS` :55、`SHELL_LAUNCHERS` :82、`GIT_WRITE_SUBCOMMANDS` :94、`DENY_AST_CALLS` :126、`NETWORK_CALLS` :154、`validate_managed_script_output` :337、`check_script_policy` :357；executor 的 `MAX_RUN_TARGETS=5` :43、`MAX_SSH_CONCURRENCY=5` :44、`SSH_TIMEOUT_SECONDS=60` :48、`REMOTE_ROOT` :49、`_resolve_runtime_target` :182、`_execute_target_script` :234、`_target_result` :329、`_save_analyzed_results` :640、`execute_script_run` :708；run service 的 `create_script_run` :224、`_resolve_running_targets` :146、`_class_member_by_vmid` :74；routes 的 `create_message` :452、`upload_session_attachment` :328、`create_session_script` :629、`create_session_run` :744、兩個 409 code（:468-476/:602-611）；template_command_service 的 `SUPPORTED_TEMPLATE_KEYS` :12、`GENERAL_COMMAND` :61、`get_enabled_template_commands` :78、`validate_check_steps_with_issues` :165；pve_log 的 `_BLACKLIST_RULES` ssh_guard.py:18、pending token 存記憶體 ssh_exec.py:70、`ssh_exec` :528。
- 未驗證：runtime 行為（無 GPU/vLLM 環境）、前端實際呼叫順序 — 本文件僅依 backend 靜態程式碼。
- 生成迴圈深挖（§4-6 ~ §4-12）追加驗證：`fix_script_content` :828、`_apply_line_replacements` :773、`_repair_instructions` :587、`_failure_signature` :491、`_feedback_snapshot` :551、`_merge_gate_results` :407、`_normalize_ai_review` :265、`MODEL_CALL_RETRYABLE_STATUS_CODES` :542、`SCRIPT_GENERATION_SYSTEM_PROMPT` :125、`AI_REVIEWER_SYSTEM_PROMPT` :206、`FIX_SCRIPT_SYSTEM_PROMPT` :231、coverage validator（`parse_coverage_payload` :21、`_merge_mappings` :70、`validate_coverage` :86、`realign_coverage_to_script` :151）、`collect_record_check_ids` script_quality_validator.py:567 — 2026-09-14 實讀吻合。
- 本文件為合併版：原獨立檔 `2026-09-13-teacher-judge-generation-loop-deep-dive.md` 已併入 §4-6 ~ §4-12（深挖）。
- §2-A（item id 新邏輯）為 2026-09-13 working-tree 驗證紀錄：focused tests 104 passed（backend/.venv，2.96s）；該批變更已提交並被 §2-B 架構取代。
- §2-B（工具呼叫架構）2026-09-14 驗證：focused tests `uv run python -m pytest tests/test_rubric_template_commands.py tests/test_teacher_judge_sessions.py -q` → **106 passed / 2 failed**（backend/，失敗兩項為 fixture 落後，見 §2-B-5）。
