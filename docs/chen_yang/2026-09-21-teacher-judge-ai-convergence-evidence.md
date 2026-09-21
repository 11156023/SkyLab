# Teacher Judge AI 收斂：現況證據、資料邊界與優化計畫

日期：2026-09-21
分析基準：`多機器處理`，`HEAD=a670eb53`
範圍：以現行 checkout 的靜態追查完成 P0 契約對齊，並落地最小 backend 錯誤投影修補；本文件不代表 live
vLLM、瀏覽器、資料庫、Proxmox、VM/SSH 或完整 E2E 已驗證。

## 1. 目的與判斷原則

本次目標是收斂 Teacher Judge 中：

1. 已沒有現行呼叫者的死碼。
2. 可以由 backend deterministic contract 判斷、不需要再交給 AI 的狀態。
3. 會造成 request timeout、流程等待或重複工作的路徑。
4. 會重複注入 rubric、附件、歷史訊息而佔用模型上下文的資料。

保留以下界線：

- AI 只負責自然語言／附件內容轉成候選 typed proposal。
- backend 擁有 ID、revision、duplicate、machine identity、readiness、編譯、政策、coverage、執行與結果投影。
- `check_steps` 是唯一腳本來源；`detection_method` 只供語意／顯示，不可直接產生腳本。
- `target_node_key` 是執行與結果身份；`peer_node_key` 只作觀察 metadata。
- 未經教師確認，proposal 不得寫入正式 rubric；未通過 policy、quality、coverage、ownership、VM/SSH、output 與 approved-before-run gate 不得執行。

## 2. 目前真實資料流

### 2.1 Chat／proposal

```text
AiJudgePanel
  -> POST /teaching-classes/{class_id}/judge/sessions/{session_id}/messages
  -> teacher_judge_sessions.create_message()
  -> bounded_history() + machine_context_entries()
  -> chat_with_rubric()
  -> _run_proposal_tool_loop()
  -> _execute_checklist_tool()
  -> server-side normalized/staged proposal
  -> assistant message + metadata_json + conversation_focus
```

證據：

- route 在 AI 呼叫前記錄 user message，並帶入 selected file、analysis revision、machine context：[teacher_judge_sessions.py](../../backend/app/api/routes/teacher_judge_sessions.py#L531)。
- proposal 不取自模型 reply 內的 `updated_items`；`chat_with_rubric()` 只採用 server tool staging 的結果：[service.py](../../backend/app/ai/teacher_judge/service.py#L2513)。
- `_execute_checklist_tool()` 負責正式 ID、duplicate、machine fields、typed check steps 與候選 rejection：[service.py](../../backend/app/ai/teacher_judge/service.py#L1842)。
- refine 結果會再經 `apply_proposal_operations_to_analysis()` 計算 readiness，不以模型文案判斷能否製作腳本：[teacher_judge_sessions.py](../../backend/app/api/routes/teacher_judge_sessions.py#L720)。

### 2.2 Save/Create／腳本

目前正式 UI 走以下路徑：

```text
Save/Create
  -> RUBRIC_POLISH_PROMPT (AI Finalizer)
  -> proposal diff / apply analysis
  -> POST .../script-sets
  -> _session_rubric_for_script_set()
  -> create_artifact_set()
  -> partition by target_node_key
  -> canonicalize_check_plan()
  -> deterministic compile
  -> policy + quality + coverage
  -> approved child artifacts
```

證據：

- 前端 Save/Create 先呼叫 refine，再儲存候選 rubric，最後才呼叫 `createSessionScriptSet()`：[AiJudgePanel.jsx](../../frontend/src/pages/course-operations/class-workspace/AiJudgePanel.jsx#L1813)。
- 正式腳本產生使用 `createSessionScriptSet()`：[AiJudgePanel.jsx](../../frontend/src/pages/course-operations/class-workspace/AiJudgePanel.jsx#L1921)。
- script-set 依 `target_node_key` 分割，每一個 child artifact 由 deterministic compiler 產生：[script_artifact_service.py](../../backend/app/ai/teacher_judge/script_artifact_service.py#L2023)。
- `compile_check_plan()` 直接執行 `check_script_policy()` 與 `check_script_quality()`，並建立固定 coverage/review metadata：[deterministic_compiler.py](../../backend/app/ai/teacher_judge/deterministic_compiler.py#L451)。

### 2.3 執行／結果

```text
approved child artifacts
  -> script set run / run batch
  -> server resolves class/student/resource/SSH/peer context
  -> managed script executes
  -> teacher_judge_result.v1
  -> deterministic checks / teacher-review evidence
  -> frontend student/node/item/check projection
```

目前沒有證據顯示 runtime 需要再次以 AI 重新解讀 stdout/stderr；結果判定由 typed assertion 或 teacher-review evidence contract 負責。

## 3. 死碼與休眠功能判定

### 3.1 高信心可清理

| 項目 | 現況證據 | 建議 |
|---|---|---|
| `script_artifact_service._rubric_snapshot()` | 現行 production、route 與 tests 沒有呼叫者；只剩私有 definition。 | 獨立小 patch 移除。 |
| `session_service.maybe_summarize()` | 現行 request handler 使用 `schedule_summary()`；此函式只被舊測試呼叫，且未在 package `__all__` export。 | 先改 focused tests 使用正式 summary path，再移除 compatibility helper。 |
| `AiJudgeService.createSessionScript()` | 正式 UI 呼叫 `createSessionScriptSet()`；此 writer 只剩 service tests 與舊 panel spy。 | 前端 service 與對應測試清理。 |
| `AiJudgeService.createScript()`、`regenerateScript()` | 正式頁面沒有呼叫者；腳本總覽仍使用 list/approve/rename/delete/run。 | 先保留 backend route，前端 writer 先清理。 |

### 3.2 需要先停寫、再退役

以下不能只因目前 UI 沒呼叫就直接刪除：

- `POST /sessions/{session_id}/scripts` 仍會進入舊 `create_artifact()`。
- `POST /judge/scripts/` 與 `POST /judge/scripts/{script_id}/regenerate` 仍會進入 legacy AI pipeline。
- `GET /judge/scripts/`、approve、rename、archive、delete、run 仍可能讀取歷史 artifacts。
- 舊 artifact 可能仍有 `draft`、`reviewed`、`review_failed`、`shell`、`bat`、`ai_generated`、`regenerated` 等歷史資料。

退役前必須先查現有 access log 或 API consumer；repo-local search 只能證明目前 checkout 沒有前端呼叫者，不能證明沒有外部 client。

### 3.3 不應誤刪的項目

- `script_policy.py` 與 `script_quality_validator.py`：deterministic compiler 仍實際使用。
- `rubric_proposal`、`conversation_focus`、`item_results`：前端 proposal/apply、Chat history 與 unresolved focus 仍使用。
- revision conflict、server-minted ID、duplicate-title、read-before-edit：是資料一致性與授權契約。
- manual／teacher-review、coverage、approved-before-run、VM/SSH/output/evidence-reference gate：是安全與產品行為，不是過硬死碼。
- `target_node_key`／`peer_node_key`：前者決定執行身份，後者只作觀察 metadata，不可合併。

## 3.3 P0 契約盤點結果（已完成）

P0 的問題不是「哪一段文案顯示錯」，而是同一個 Save/Create request 在不同入口有沒有相同的
source、授權、revision、阻塞、錯誤持久化與 UI 投影。盤點結果如下：

| 契約 | 真正 owner／輸入 | 失敗投影 | 結論 |
|---|---|---|---|
| 班級與 session 授權 | session routes 先 `_access`，再以 class-scoped `get_session()` 取 session；legacy class routes 使用 `_ensure_class_access`。 | 授權失敗沒有 session context，不寫 Chat。 | 保持 fail-closed；不得為了顯示錯誤繞過授權。 |
| rubric source／revision | 前端送 `analysis_revision`；backend 從 `selected_file_id` 重新載入檔案並比對，不信任前端 rubric snapshot。 | `/script-sets` revision conflict 現在寫 `stage=persistence`、`reason_code=analysis_revision_conflict` 的 assistant message，再回原本 409。 | source identity 與 revision 仍由 backend 擁有。 |
| readiness／blocker | `get_script_generation_blockers()` 與 `ensure_script_generation_supported()`；typed Check Plan 仍須再經 compiler 驗證。 | `/script-sets` 的空表、缺資料、manual／unsupported、typed preflight blocker 寫入 `item_results`、`conversation_focus` 與 `stage=script_preflight`，再回原本 422。 | 不把 blocker 轉成 AI 文案，也不放寬 gate。 |
| deterministic compile／policy | `create_artifact_set()` 依 `target_node_key` 分割，呼叫 `canonicalize_check_plan()`、`compile_check_plan()`、peer policy 與 static gates。 | `/script-sets` 的 422 contract error、502 node compile error 及未預期例外先 rollback，再以 bounded workflow error 寫入 `metadata_json`，不留下半套 artifact。 | compile／policy 判定仍是 backend deterministic contract。 |
| script-set source mismatch／regenerate | regenerate 先比對既有 set 的 `source_file_id`，再重建 child artifacts。 | source mismatch 也寫入 session message；原本 409 response 不變。 | 不讓錯誤只存在 toast。 |
| 成功流程 | `create_artifact_set()` commit child artifacts，route 更新 session activity 後回 set + children。 | 成功 response 與既有 status／artifact shape 不變；成功訊息仍由頁面 notice 顯示，不新增重複 Chat 訊息。 | P0 只補錯誤投影，不改成功資料流。 |

落地位置與證據：

- 共用 `_save_script_set_failure()` 將 blocker、revision、compile 與一般例外轉成既有
  `script_blocker_workflow_message()`／`workflow_error_message()`，寫入
  `TeacherJudgeSessionMessage.metadata_json`：[teacher_judge_sessions.py](../../backend/app/api/routes/teacher_judge_sessions.py#L189)。
- `/script-sets` 入口在 preflight、建立與 regenerate 的例外路徑都先保存 workflow message；資料庫 rollback
  後才保存錯誤投影，避免把未完成的 artifact 併入同一交易：[teacher_judge_sessions.py](../../backend/app/api/routes/teacher_judge_sessions.py#L1075)、[teacher_judge_sessions.py](../../backend/app/api/routes/teacher_judge_sessions.py#L1165)、[teacher_judge_sessions.py](../../backend/app/api/routes/teacher_judge_sessions.py#L1271)。
- workflow message 的 bounded 欄位、`item_results`、`conversation_focus`、`reason_code` 與 teacher-facing
  內容由既有 formatter 統一產生：[session_service.py](../../backend/app/ai/teacher_judge/session_service.py#L514)、[session_service.py](../../backend/app/ai/teacher_judge/session_service.py#L561)。
- compiler 的 per-node error source 仍在 artifact service，route 只負責把錯誤投影到 session，不改 compiler
  判定：[script_artifact_service.py](../../backend/app/ai/teacher_judge/script_artifact_service.py#L2023)。
- 三條 regression tests 覆蓋 script-set revision conflict、preflight blocker、compile contract error；既有
  legacy `/scripts` message tests 仍保留：[test_sessions_messages.py](../../backend/tests/ai/teacher_judge/test_sessions_messages.py#L510)。

### 3.4 舊 `/scripts` consumer 盤點與尚缺外部證據

目前 checkout 可證明的只有「repo 內 call site」：

- 正式 `AiJudgePanel.handleSaveAndCreate()` 使用 `createSessionScriptSet()` →
  `POST .../sessions/{session_id}/script-sets`：[AiJudgePanel.jsx](../../frontend/src/pages/course-operations/class-workspace/AiJudgePanel.jsx#L1813)、[aiJudge.js](../../frontend/src/services/aiJudge.js#L196)。
- `createSessionScript()`、class-level `createScript()`、`regenerateScript()` 仍在 service／backend route
  保留；目前沒有 `AiJudgePanel` 對它們的 call site，service 仍有對舊 session writer 的測試。這只能證明目前
  checkout 的前端主流程已切換，不能證明沒有外部 client。
- repo 內沒有這些 endpoint 的部署 access log、reverse-proxy request archive 或 API consumer registry；因此
  無法由靜態搜尋宣稱舊 POST 已零使用量。

所以 P0 的結論分成兩層：

1. **已完成的程式契約對齊：**多機 `/script-sets` 的 blocker、revision、compile／例外現在與 Chat／頁面
   notice 共用同一份持久化 workflow projection；schema、execution gate、成功 response 均未改動。
2. **仍需部署端證據的退役前置條件：**刪除或停止舊 `POST /sessions/{id}/scripts`、`POST /judge/scripts/`
   與 regenerate 前，需從 reverse proxy／API gateway／application access log 查詢至少一個完整 retention
   window，按 route、method、status、actor／client 與時間範圍確認外部 consumer。查不到 log 時只能標記
   `unverified`，不可當成零流量。

## 4. 可以交給 backend、移除 AI 額外處理的判定

### 4.1 已經應由 backend 擁有

- rubric 是否有項目。
- `detectable`、`judgement_mode`、typed collector/assertion 是否符合 schema。
- `target_node_key`、`peer_node_key` 是否屬於目前班級且彼此一致。
- template command 是否存在、參數是否完整、timeout 是否在允許範圍。
- 是否可以進入 script creation。
- 每個 item 是否被 coverage 覆蓋。
- deterministic script 的 policy／quality／compile 結果。
- artifact status、version、archive、revision、source file snapshot。
- execution result 的 `pass`／`fail`／`unknown`／`peer_unavailable` 投影。

這些目前大多已有 backend implementation，但仍被 Finalizer prompt、前端 blocker 與 script-set route 重複判斷。

### 4.2 AI 應保留的最小職責

- 將教師自然語言轉成 candidate item 或 typed `check_steps`。
- 從附件文字抽取來源檢查列。
- 對無法純規則判斷的語意內容提出候選 collector/assertion。
- 長對話的可讀摘要（若證明 deterministic focus 不足以取代它）。

AI 不應再負責：

- 產生自由格式 Python 腳本。
- 對已由 policy/quality/compiler 判斷的腳本再次做必經 reviewer。
- 用 reply 文案宣稱 proposal、ready 或 script ready。
- 產生 backend 已固定的錯誤、阻塞、status 與 workflow 文案。
- 反覆呼叫 `list_checklist` 讀取 backend 已擁有的 rubric。

## 5. 目前流程阻塞與成本

### 5.1 Finalizer request budget 不一致

- 前端一般 Teacher Judge request timeout 是 120 秒：[aiJudge.js](../../frontend/src/services/aiJudge.js#L12)。
- backend tool loop 預設最多 6 輪；耗盡後還會再發一輪移除 tools 的純回覆 request：[service.py](../../backend/app/ai/teacher_judge/service.py#L2185)。
- Finalizer 又強制先 `list_checklist`，而 refine route 最終會用 backend workflow reply 覆蓋 AI 文字：[prompt.py](../../backend/app/ai/teacher_judge/prompt.py#L284)、[teacher_judge_sessions.py](../../backend/app/api/routes/teacher_judge_sessions.py#L751)。

結果是：AI 最終文案可能是必定被丟棄的 request，且多輪成功延遲可能超過前端 120 秒。

### 5.2 附件逐項 N+1

- 一次附件先抽取來源項目，再以 `_ITEMWISE_CONCURRENCY = 2` 平行處理各項：[service.py](../../backend/app/ai/teacher_judge/service.py#L2602)。
- 上限為 50 個來源項目，理論上是 1 次 extraction 加最多 50 個獨立 chat core；每個 chat core 仍可進入 tool loop。
- 附件單份抽取上限 12,000 字、最多 5 份：[attachment_service.py](../../backend/app/ai/teacher_judge/attachment_service.py#L26)。

這種設計保護了「一項失敗不阻塞其他項目」，但 request 數量與延遲不受前端 120 秒 budget 控制。

### 5.3 歷史與摘要重複

- `bounded_history()` 取最多 20 則及 24,000 字，再額外加入最多 8,000 字 summary：[session_service.py](../../backend/app/ai/teacher_judge/session_service.py#L1288)。
- summary 有 `summary_through_message_id`，但 history 目前沒有以該 boundary 排除已被摘要的舊訊息。
- 因此一般對話可能同時攜帶「摘要」與「摘要涵蓋的最近訊息」，造成 token 重複。

## 6. 建議落地順序

### Phase 0：契約盤點與錯誤投影對齊（程式部分已完成）

1. 保留本文件作為 baseline，並完成 UI → route → service → persistence → notice 的實際資料流盤點。
2. 已補齊 `/script-sets` 的 blocker、compile error、revision conflict、source mismatch 與未預期例外的
   session message／`metadata_json` 投影；原 HTTP status/detail 保持不變。
3. 已確認 Save/Create 的正式入口是 `/script-sets`；舊 `/scripts` route 與 service writer 仍保留相容讀取／
   外部 consumer 可能性。
4. 不修改 DB schema，不修改 execution gate，不以 repo search 取代部署 access-log 證據。

驗收：錯誤可由 Chat history 與頁面 notice 同時追蹤；既有成功流程輸出不變。舊 POST route 的外部使用量仍
是 `unverified`，在取得部署 log 前不得退役。

### Phase 1：低風險死碼清理

1. 移除 `_rubric_snapshot()`。
2. 移除 `maybe_summarize()` compatibility helper。
3. 移除前端未使用的三個 legacy writer 與測試 spy。
4. 清掉由本 phase 產生的 imports、常數與註解。

驗證：Teacher Judge artifact/session/template focused tests、Ruff、`py_compile`、frontend service/panel tests、`git diff --check`。

### Phase 2：Ready fast path 與 Finalizer 短路

1. Save/Create 先以 backend preflight 判斷目前 typed plan 是否可直接編譯。
2. Ready 且 revision 正確時直接建立 script-set，AI 呼叫數為 0。
3. 只有 legacy/missing/semantic ambiguity 才呼叫 Finalizer。
4. Finalizer 直接收到必要的 compact rubric projection，不再強制 `list_checklist`。
5. tool calls 完成後由 backend 生成 workflow reply，不再發送會被覆蓋的 AI final reply。

驗證：Ready、partial、manual、teacher-review、mixed result、revision conflict、invalid node、typed contract failure、no-change 等 regression tests。

### Phase 3：附件與上下文收斂

1. extraction 後建立 server-owned `source_item_id`。
2. 以固定 chunk 將多個來源項目送給同一次 AI request。
3. backend 逐項驗證與投影，維持一項失敗不影響其他項目。
4. 後續 history 不自動重新注入已完成 extraction 的原始附件全文。
5. summary 只保留 boundary 之後的新訊息，並只在即將超過 history budget 時排程。

驗證：50 項附件、重複標題、部分失敗、全失敗、附件超長、摘要失敗保留舊摘要、source/revision mismatch。

### Phase 4：Legacy AI script pipeline 退役

1. 停止新請求進入 `create_artifact()`／`regenerate_artifact()`。
2. 保留歷史 artifact 的 read/approve/archive/delete/run 相容能力。
3. 確認 access log 無外部 consumer 後，移除舊 POST routes。
4. 再刪除 `generate_script_content()`、`review_script_with_ai()`、`fix_script_content()`、`build_reviewed_script()`、retry prompt、coverage repair 與相關 monitoring writer。
5. `script_policy.py`、`script_quality_validator.py`、deterministic compiler、executor、result projection 不在此 phase 刪除。

驗證：歷史 artifact 讀取、腳本總覽、approve/archive/delete、script-set regenerate、run batch、學生結果投影，以及 API route registration。

## 7. 不在本次收斂範圍

- 不改 `teacher_judge_result.v1`、artifact/run schema 或 migration。
- 不重新命名 `ai_review_result_json`、`ai_generated` 等歷史欄位；先停止新增不正確語意，另案處理 schema migration。
- 不放寬 shell/no-shell、argv、timeout、localhost、output、SSH、ownership、≤5 targets、coverage 或 approved-before-run gate。
- 不把 `manual` 整表阻擋改成可執行；若產品要允許 mixed runnable/manual，需另案同步 backend、frontend、schema、測試與結果投影。
- 不將 `peer_node_key` 變成第二個 executor，也不因 peer 缺失而阻擋與 peer 無關的 local checks。
- 不恢復目前工作樹中已被刪除的歷史文件。

## 8. 驗證矩陣

| 類別 | 最小驗證 |
|---|---|
| Chat proposal | `backend/tests/ai/teacher_judge/test_template_commands_chat_normalize.py`、`test_sessions_messages.py` |
| P0 script-set error projection | `backend/tests/ai/teacher_judge/test_sessions_messages.py`（revision conflict、preflight blocker、compile contract error） |
| History/summary | `backend/tests/ai/teacher_judge/test_sessions_history.py` |
| Attachment | `backend/tests/test_teacher_judge_attachments.py`、itemwise tests |
| Compiler | `backend/tests/ai/teacher_judge/test_deterministic_compiler.py` |
| Policy/quality | `backend/tests/ai/teacher_judge/test_script_policy.py`、`test_script_quality_validator.py` |
| Artifact compatibility | `backend/tests/ai/teacher_judge/test_script_artifacts.py`、multi-machine execution tests |
| Frontend | `AiJudgePanel.test.jsx`、`aiJudge.test.js` |
| Static | `uv run ruff check app tests`、focused `pytest`、frontend tests/build、`py_compile`、`git diff --check` |
| Runtime boundary | Separate live vLLM, browser, DB, Proxmox, VM/SSH and full E2E checks; focused tests do not replace these checks. |

本次實際驗證：

- backend focused pytest（session messages + multi-machine execution）：**29 passed**。
- `ruff check`、`compileall` 與 `git diff --check` 通過。
- frontend `AiJudgePanel.test.jsx` + `aiJudge.test.js`：**88 passed**。
- 未執行 live vLLM、瀏覽器、DB、Proxmox、VM/SSH 或完整 E2E。

## 9. 本次工作樹狀態

本次補上 `backend/app/api/routes/teacher_judge_sessions.py` 的 script-set workflow error projection，並在
`backend/tests/ai/teacher_judge/test_sessions_messages.py` 加入三條 regression tests；沒有修改 DB schema、
execution gate 或 frontend。文件新增於 `docs/chen_yang/`；工作樹原有的 10 份文件刪除狀態保留不動。
