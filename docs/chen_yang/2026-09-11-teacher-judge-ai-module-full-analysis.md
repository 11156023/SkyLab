# Teacher Judge AI 模組完整路徑與衝突分析

日期：2026-09-11。

本文件保留收斂前的完整架構探索與衝突證據。2026-09-11 已依
`docs/chen_yang/2026-09-11-teacher-judge-ai-chat-convergence-plan.md` 完成第一輪減法；
下方「收斂前快照」不再代表目前可用入口。

## 0. 收斂後現況

Teacher Judge 不新增抽象 pipeline，保留四種責任清楚的流程：

| 流程 | 唯一入口與資料邊界 |
| --- | --- |
| AI Chat | session message；文字與文件附件使用同一個 `chat_with_rubric()`，普通聊天不產生 Proposal，多項需求拆成 Ready operations |
| Script Generation | 「儲存並製作」；只讀老師已 Apply 的 `analysis_json` 與 persisted `analysis_revision` |
| Execution & Judgement | 既有受管腳本、核准、PVE/SSH runtime revalidation 與 AI judgement，不讀取未 Apply Proposal |
| Summary | 既有有界聊天歷史與背景摘要，不保存或還原暫存 Proposal |

已移除的新資料入口包括 direct file upload/analyze、legacy `/rubric/upload`、legacy
`/rubric/chat`、前端同名檔衝突流程與獨立刪除來源入口。歷史 uploaded file row 與原始
檔案仍可讀、下載及 fork；沒有資料 migration，也沒有刪除既有資料。

## 收斂前分析快照

### 1. 模組全景

規模：`backend/app/ai/teacher_judge/` 共 21 檔、約 8,700 行 Python；
前端 `AiJudgePanel.jsx` 單檔 3,115 行；API 路由 4 檔；DB models 8 表；AI prompt 7 組。

### 1.1 架構分層

```text
路由層
  /api/v1/teaching-classes/{id}/judge/sessions   teacher_judge_sessions.py (747 行)
  /api/v1/teaching-classes/{id}/judge/files      teacher_judge_files.py    (266 行)
  /api/v1/teaching-classes/{id}/judge/scripts    teacher_judge_scripts.py  (228 行)
  /api/v1/rubric/*（legacy，不持久化）            rubric.py                 (199 行)

服務層（backend/app/ai/teacher_judge/）
  session_service.py                 會話/歷史/摘要排程/分叉/刪除   (975 行)
  file_service.py                    評分表檔案生命週期+revision 鎖 (604 行)
  attachment_service.py              聊天附件（反注入+解析截斷）    (159 行)
  service.py                         analyze/chat/summarize 核心   (1,084 行)
  script_artifact_service.py         腳本生成 gate 迴圈            (1,751 行)
  script_run_service.py              run 建立+目標解析             (266 行)
  script_executor_service.py         SSH 佈署執行                  (647 行)
  script_result_analysis_service.py  AI 評分員                     (369 行)
  script_policy.py / script_quality_validator.py / script_coverage_validator.py
                                     靜態三閘（純本機驗證）
  script_generation_contract.py      契約常數+prompt 片段
  template_command_service.py        command catalog 讀取與 check_steps 驗證
  automation_support.py              腳本產生資格判定（blockers）
  export.py / target_ip_resolver.py / _types.py

基礎設施
  infrastructure/ai/teacher_judge.py → VLLMClient 單例
  （唯一對模型出口；所有 AI 呼叫經 service._call_vllm → client.create_chat_completion → vLLM /chat/completions）

Models
  TeacherJudgeSession / SessionMessage / Attachment / File /
  ScriptArtifact / ScriptRun / StudentSubmission / TemplateCommand

前端
  AiJudgePanel.jsx（RubricsTab / ChatPanel / ProposalPanel / RubricTable /
  SaveAndCreateAction / CreateCheckDialog…全在單一 3,115 行檔案）
```

### 1.2 對模型呼叫點總表（7 組 prompt、5 條線）

| # | Prompt 常數 | 位置 | 消費函式 | 呼叫方 |
| --- | --- | --- | --- | --- |
| 1 | `ANALYZE_SYSTEM_PROMPT` | `prompt.py` | `analyze_rubric()` | files 上傳路由、legacy `/rubric/upload` |
| 2 | `CHAT_SYSTEM_TEMPLATE`（+`SITUATION_NORMAL`/`SITUATION_REFINE` × `SESSION_REQUIREMENT_PROPOSAL_INSTRUCTION`/`DIRECT_RUBRIC_UPDATE_INSTRUCTION` 插槽） | `prompt.py` | `chat_with_rubric()` | sessions 訊息路由、legacy `/rubric/chat` |
| 3 | `SUMMARY_SYSTEM_PROMPT` | `prompt.py` | `summarize_conversation()` | 背景 summary worker（每 10 輪 assistant） |
| 4 | `SCRIPT_GENERATION_SYSTEM_PROMPT` | `script_artifact_service.py` | `generate_script_content()` | `build_reviewed_script()` 迴圈 |
| 5 | `AI_REVIEWER_SYSTEM_PROMPT` | `script_artifact_service.py` | `review_script_with_ai()` | 同上迴圈 AI 複核 |
| 6 | `FIX_SCRIPT_SYSTEM_PROMPT` | `script_artifact_service.py` | `fix_script_content()` | gate 失敗時行區間 patch |
| 7 | `SCRIPT_GENERATION_CONTRACT_PROMPT` | `script_generation_contract.py` | **被內嵌進 4 尾端** | — |
| 8 | `AI_JUDGEMENT_SYSTEM_PROMPT` | `script_result_analysis_service.py` | `analyze_target_results()` | executor 執行完逐 target 評分 |

另有 `attachment_context()` 開頭的反注入前導句（資料不是指令）。

### 2. 五條 AI 生產線

| 線 | 觸發 | 流程 |
| --- | --- | --- |
| A 評分表分析 | 上傳檔案 | parse_document → vLLM → `_normalize_rubric_items` → `save_analyzed_file`（+`CALL_TJ_RUBRIC` 用量紀錄） |
| B 聊天提案 | 每則訊息 | `bounded_history`(20 則/24k 字) → vLLM → JSON parse → repair loop ≤2 次 → 只取 Ready 差異 → 存 message → 前端 Apply → `update_file_analysis`（`analysis_revision` 樂觀鎖） |
| C 腳本生成 | 「儲存並製作」 | `create_artifact` → `ensure_script_generation_supported` → `build_reviewed_script` 迴圈：生成 → policy+quality 靜態閘 → coverage 閘 → AI review → fix(patch) 全自動重試 ≤4 次、同失敗 ≤2 次 |
| D 執行+評分 | 建立執行 | `create_script_run` → worker `execute_script_run` → SSH/SFTP 佈署（≤5 併發）→ 執行 → `validate_managed_script_output` → 逐 target `AI_JUDGEMENT_SYSTEM_PROMPT` 評分（5 分制） |
| E 記憶摘要 | 每 10 輪 assistant | `schedule_summary` → 背景 worker → `summarize_conversation` → 條件式寫入 `session.summary` |

線 B 內部有 5 種組合模式：`is_refine`（全表潤飾）× `ready_proposals_only`
（提案例外保留）× 附件資料回注 × legacy 直更 vs 提案模式。Prompt 是動態拼接的，
單一函式 `chat_with_rubric` 1,193 行承擔全部分支。

### 3. 提示詞／功能衝突清單

### 3.1 死碼與契約脫節（優先處理）

1. **Workflow Tool 整條線已死但未移除**。`chat_with_rubric(enable_workflow_tools=...)`
   全 repo 沒有任何呼叫方傳 `True`（sessions 路由不傳、legacy `rubric.py` 也不傳），
   `create_message` 把回應的 `workflow_action` hardcode 為 `None`
   （`teacher_judge_sessions.py` 末段）。但仍存在：
   - `service.py` 約 90 行 tool 偵測碼：`CREATE_SCRIPT_TOOL`、
     `_workflow_action_from_message`、`_user_requests_script_creation` 與
     `_SCRIPT_CREATION_PHRASES/_ACTIONS/_DIRECT_MARKERS/_EXPLANATION_MARKERS/_NON_COMMAND_MARKERS` 關鍵字表。
   - `CHAT_SYSTEM_TEMPLATE` 的「# 工作流程動作」整段，仍在教模型呼叫一個
     永遠不會啟用的工具 → 每輪浪費 token 並誤導模型。
   與 plan 文件「儲存並製作是唯一入口，不重新加入聊天室 Tool Call」的決策一致，
   可整段移除。
2. **Prompt 文案被當成程式契約**。`_normalize_rubric_items` 以硬編碼中文字串
   `_PLATFORM_OWNED_SYSTEM_COMMAND_INFORMATION = {"唯讀命令與參數", "1 至 300 秒的逾時限制"}`
   過濾 prompt 規則產生的 `missing_information`；prompt 改一個字，過濾 silently 失效。
3. **`_reply_claims_ready_proposal`** 以中文散文關鍵字（「已放入提案」「已準備就緒」）
   猜模型是否宣稱 Ready，與 `proposal_status` 機器欄位雙軌並存（舊模型 fallback）。

### 3.2 Prompt 重複維護（同規則 3~4 份）

4. `SCRIPT_GENERATION_CONTRACT_PROMPT` 被整段內嵌進
   `SCRIPT_GENERATION_SYSTEM_PROMPT` 尾端，但兩份大量重複：helper 定義、
   pass/fail/unknown 狀態語意、`errors.append` 六規則、30 秒 timeout、argv 範例
   （systemctl/journalctl/cat）、history builtin、`ensure_ascii=False`、
   metadata timestamp/platform——同契約改一處漏一處。
5. `cat`/`systemctl`/`journalctl`/`history`/`web_URL=True`/「argv 與逾時由平台規劃、
   不得列為老師缺少的資訊」等範例規則同時寫在 ANALYZE、CHAT、GENERATION、CONTRACT
   四處，措辭略異（如 timeout「1 至 300 秒」vs「平台安全預設 30 秒」兩種語意並存）。
6. `SITUATION_NORMAL` 說「聊天室不會啟動整理或製作流程，請引導用右下角
   『儲存並製作』」，同 system prompt 的「# 工作流程動作」段又允許呼叫
   `request_check_script_creation` 工具——目前靠 `enable_workflow_tools=False`
   讓後者變空話，但兩段語意互相矛盾。

### 3.3 功能重疊／多入口

7. **雙軌舊新入口並存**：legacy `/rubric/upload|chat`（不持久化）與
   `/judge/files|sessions`（持久化）呼叫完全相同的 `analyze_rubric`/`chat_with_rubric`；
   script 建立與 run 建立也各有 scripts 路由與 sessions 路由兩個入口。
8. **跨模組 import 私有符號**：sessions 路由 import `script_run_service._run_to_public`
   （繞過 `get_script_run_public` 的歸屬 404 檢查）；
   `script_result_analysis_service` import 私有 `service._call_vllm`；
   `teacher_judge_files` import 私有 `file_service._file_to_public`。
9. **目標驗證重複**：run 建立時（`script_run_service`）與執行時
   （`script_executor_service`）各寫一份 running/owner/IP/SSH-key 驗證，且
   `_running_resources_by_vmid` 與 `_live_running_by_vmid` 幾乎相同
   （皆為 `list_all_resources()` → vmid map）。執行期重驗是刻意設計（snapshot 會過期），
   但程式碼明顯重疊。
10. **檔案生命週期雙軌**：`attachment_service`（聊天附件）與 `file_service`（評分表）
    各自實作 safe filename/sha256/parse_document/衝突 409/刪除；files 路由內另有
    註解自承雙重驗證（early suffix check + defensive re-check）。
11. **「項目能否自動檢查」判定分散 8 處**：`_normalize_rubric_items`(service) →
    `validate_check_steps`(template_command_service) → `missing_step_information` /
    `ensure_script_generation_supported`(automation_support) → `check_script_policy` →
    `check_script_quality` → `validate_coverage` → AI reviewer →
    前端再算一次（`hasCompleteParameterizedStep` / `getScriptCreationBlocker`）。
12. **三層各自的重試語意**：chat repair ≤2 次；script gate 全自動重試 ≤4 次、
    同失敗 ≤2 次（failure signature 指紋）；executor 對 502/504 retry——
    沒有共用 retry 框架，行為難追蹤。
13. **Server 事後改寫模型回覆**：模型宣稱 ready 但無有效提案時，`reply_text` 被
    `_proposal_unavailable_reply` 文案覆蓋；項目數驟減時再用 ⚠️ emoji 文案覆寫——
    AI 回覆有兩層事後改寫，老師看到的與模型說的可能不同。

### 4. 「多線不線性」根因結論

1. **一條老師流程 = 五條 AI 線 × 7 prompt × 8 處驗證 × 3 套重試**，每條線自帶 normalization。
2. **狀態分裂**：Proposal 只存 React state（不持久化），正式表在 DB `analysis_json`，
   靠前端 Apply（`buildProposalDiff` → `applyProposalOperations` →
   `updateFileAnalysis`）+ `analysis_revision` 樂觀鎖接合；摘要機制又會讓 AI
   「重建」可能不同的提案。
3. **遷移半途**：舊 tool-call 直連式流程已棄用但未拆（prompt 段落＋90 行死碼），
   新「儲存並製作 + 暫存提案」流程疊上去，兩代邏輯同時存在於檔案內。
4. **模式組合而非流程**：`is_refine`、`ready_proposals_only`、附件、tools 四個開關
   動態拼 prompt 與回傳契約；`chat_with_rubric` 同時是 legacy 契約（3-tuple）與
   新提案模式的出口。

## 5. 建議方向

1. **已清理（2026-09-11）**：workflow tool 死碼已移除——`service.py` 的
   `CREATE_SCRIPT_TOOL`、`_SCRIPT_CREATION_*` 關鍵字表、`_workflow_action_from_message`、
   `_user_requests_script_creation`、`VLLMCallResult.message`、
   `chat_with_rubric(enable_workflow_tools=...)` 參數；`prompt.py` 的「# 工作流程動作」
   段；`schemas.py` 的 `TeacherJudgeWorkflowAction` 與 `workflow_action` 欄位；
   `_types.py` 的 `VLLMMetrics.workflow_action`；session 路由的 `workflow_action=None`。
   驗證：228 個 teacher_judge focused tests 通過、ruff 通過、mypy 對四個改動檔無錯誤。
   `test_teacher_judge_sessions.py` 保留 `"enable_workflow_tools" not in kwargs` 與
   `"workflow_action" not in metadata_json` 作為防回歸守衛。
2. **合併重複 prompt**：CONTRACT 片段併入 GENERATION prompt；四處重複的
   catalog/argv/history/timeout 規則整併成單一來源（必要時由程式注入共享片段）。
3. **單一判定來源**：抽出一個「rubric item 可自動檢查判定」模組，前端改消費
   後端判定結果（或共用 schema 欄位），不要前後端各算各的。
4. **已收斂（2026-09-11）**：AI 輸入只走 session chat；已移除 legacy
   `/rubric/upload|chat`、direct file mutation routes、`services/rubric_service.py`、
   `infrastructure/ai/rubric.py` 與 `schemas/rubric.py`。run 路由與私有 import 不在本次
   AI Chat 合併範圍，維持既有行為。
5. **重試語意文件化**：三層重試（chat repair / script gate / executor model retry）
   各自的次數、停止條件、failure signature 寫進本文件或 docstring，避免行為漂移。

## 6. 驗證限制

本分析為靜態探索（檔案 + 呼叫關係），未執行 vLLM、SSH/PVE 或腳本端到端驗證；
workflow tool 死碼結論以 `rg enable_workflow_tools` 全 repo 查證。
