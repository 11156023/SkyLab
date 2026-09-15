# Teacher Judge 死碼、無用功能佔用與安全邊界收斂追查

> 產生日期：2026-09-15。性質：只讀追查、風險分類與零行為變更的收斂方案。
> 本文件承接 `2026-09-13-teacher-judge-full-pipeline-and-safety-boundaries.md`，
> 基準 checkout 為 `d04d39b6`（`AI-PVE-tools`）。本次只新增文件，不修改
> backend、frontend、資料庫 schema、migration、API 或測試；工作樹原有的刪除／修改
> 仍完整保留。

## 0. 結論先行

本次追查把「沒有看到呼叫」和「可以安全刪除」分開。現況不是整個 Teacher Judge
需要重寫，而是少數真正冗餘、數個暫停使用但仍屬相容契約的面，以及一組不能在
清理時順手放寬的安全閘門。

| 分類 | 目前證據 | 零行為變更處置 |
|---|---|---|
| 已確認死碼（高信心） | `backend/app/ai/teacher_judge/script_artifact_service.py:321-326` 的私有 `_rubric_snapshot` 只有定義；`rg` 沒有 app/test 呼叫者。建立與重新生成已改用 `analysis_dump` + `_with_template_command_catalog`。 | 只列為後續獨立 cleanup patch；本文件不刪。先跑 artifact focused tests、Ruff、diff 檢查，再移除。 |
| 歷史方案已被取代 | `chat_workflow.py`、`rubric_normalization.py` 以及舊 parse/repair 名稱在 tools-first 提交後不存在；`service.py` 的工具呼叫迴圈是現行來源。 | 不恢復歷史模組，不為了「整齊」新增第二套 workflow/state machine。 |
| 休眠／相容契約 | `reviewed`、`draft`、`cancelled`、`all_with_vm`、`running_only`、`shell`、`bat`、`rubric_proposal` 等仍出現在 model/schema/migration/讀取端、前端或測試中。 | 先盤點 DB 實際資料與外部消費者；未有 migration、資料轉換與版本策略前不刪 enum／欄位／端點。 |
| 跨邊界未對齊 | `StudentHomePage.jsx:553,571` 呼叫 `CoursesService.startAiCheck`；實際 `frontend/src/services/courses.js:9-72` 沒有此方法，`courses.py:232-272` 也只有 completion PUT 與 check GET。 | 另開功能缺陷決策；本次不刪 UI、不臆補學生啟動權限，也不把它算成 Teacher Judge 後端死碼。 |
| 過硬但有意義的邊界 | `ensure_script_generation_supported` 對 `detectable=manual` 整表擋下、coverage 要求每個 rubric item 有 check、AI 判讀要求每個 item 都有合法引用。 | 這些是行為／安全變更，不是死碼清理；維持現狀，另以 RFC 定義「導師複核」是否能產生證據後才改。 |

**目前可安全合併的最小範圍只有文件與日後單一私有 helper 移除。** 不應把
相容 enum、前端未對齊、人工複核政策或執行限制混成一次大清理。

## 1. 追查方法與判定規則

### 1-1 來源優先順序

依定案文件的實際資料流，先從 codebase-memory graph 查 symbol、呼叫與路徑，再以
現行 checkout 的 `rg`、實檔與測試交叉驗證。graph project 為
`C-Users-Desktop-Campus-Cloud`；索引可用，但本次遇到兩個 stale edge：

- graph 顯示 `startAiCheck`，其 snippet 卻是 `updateAssignmentCompletion`；現行
  `courses.js` 實檔沒有 `startAiCheck`。因此採實檔與 `rg` 結果，不把 stale graph
  當成已實作證據。
- graph 把 `pending_judgement` 顯示成無 caller；實檔仍有
  `script_executor_service.py:22,433` 的 import 與呼叫。這確認 graph 是定位工具，
  不是刪碼 oracle；遇到衝突以 checkout 為準，必要時再重建索引。

### 1-2 四種分類

1. **真死碼**：私有或非公開 symbol，同時滿足 graph／文字搜尋無生產呼叫、無 route／
   frontend／migration／資料讀取用途，且可由現有測試證明移除不改契約。
2. **休眠／相容**：現行流程不新產生，卻可能被舊 DB row、讀取 API、前端狀態、
   migration enum、測試 double 或下游投影消費。它不是「沒用」，而是「不能現在刪」。
3. **仍在用**：有 route、service call、背景 worker、frontend call、下游資料投影或
   直接測試契約。低呼叫數不代表死碼。
4. **安全／行為邊界**：主動拒絕或限制輸入、生成、核准、執行、結果的規則。即使看起來
   過硬，也不能在清理時刪除；放寬必須有明確產品語意、權限模型與回歸測試。

### 1-3 不採用的判定方式

- 不從按鈕文字、狀態 badge 或舊文件推論 backend 已經有能力。
- 不因 enum 沒有明顯 writer 就刪欄位；先查 migration、舊 row、序列化與讀取路徑。
- 不以單一 focused test 綠燈宣稱 vLLM、認證瀏覽器、VM／SSH 或 PVE E2E 已驗證。
- 不把歷史計畫中的「預計調整」當成 current behavior；尤其不恢復已被 tools-first
  取代的 workflow module。

## 2. 現行實際資料流與收斂邊界

定案文件的 §0、§1、§2-B、§4、§5 所描述的 current flow，在本 checkout 可整理為：

```text
Instructor
  -> POST /teaching-classes/{class}/judge/sessions/{session}/messages
  -> teacher_judge_sessions.create_message
       -> session/file/owner/active/revision/attachment checks
       -> chat_with_rubric 或 analyze_attachments_itemwise
            -> _run_proposal_tool_loop
                 -> _execute_checklist_tool
                 -> server normalize/id/duplicate/read-before-edit checks
       -> assistant message metadata（focus、item_results、tool_calls）
       -> transient response.rubric_proposal + base_revision
  -> frontend AiJudgePanel 套用所選操作
  -> PATCH .../judge/files/{file}/analysis
       -> analysis_json + analysis_revision（樂觀鎖）
  -> POST .../judge/sessions/{session}/scripts
       -> ensure_script_generation_supported
       -> create_artifact -> build_reviewed_script
            -> policy + quality + coverage + AI reviewer + bounded retry
       -> approved 或 review_failed
  -> POST .../scripts/{artifact}/runs（target_scope=manual）
       -> create_script_run（approved、class member、running VM、≤5）
       -> background execute_script_run
            -> runtime target/owner/IP/SSH/timeout/output schema checks
            -> analyze_target_results / pending_judgement
       -> TeacherJudgeScriptRun.target_results_json
  -> course.ai_assignment_service._check_to_student
       -> student-facing status / evidence / legacy score fields
```

這條鏈上的每一段都有不同 owner：

| 邊界 | source of truth | 不可用另一層替代的原因 |
|---|---|---|
| session、class、selected file、active、revision | `teacher_judge_sessions.create_message`、`session_service`、`file_service` | 防跨班、過期回覆與錯誤來源檔；UI 選取狀態不能取代 DB 驗證。 |
| 提案內容 | `service._execute_checklist_tool` 的 staging operations | 回覆文字的 `updated_items` 只會被忽略；不能讓 prose 直接變成可套用資料。 |
| 腳本來源 | 已套用的 `analysis_json`／`analysis_revision` | 附件文字、chat reply、舊 rubric snapshot 都不是目前要執行的唯一版本。 |
| 取證命令 | rubric item 的 `check_steps`，經 template command／參數驗證 | `detection_method` 是顯示語意，不是可執行腳本規格。 |
| 執行授權 | approved artifact + class membership + VM/SSH checks | 前端按鈕、`target_scope` label 或學生頁不能授予執行權。 |
| 學生投影 | `ai_assignment_service` 讀 run snapshot/result | `requested_item_id` 與既有 score 欄位有下游用途，不能只因 Teacher Judge UI 不顯示就刪。 |

### 2-0 呼叫圖交叉證據

graph trace 以 calls mode 追到下列實際連線；每一條再用 checkout 內的 route／service
與 `rg` 驗證。這裡只列對本次清理判定有影響的 hop，避免把 graph 的跨專案雜訊誤當
成 Teacher Judge contract：

| 起點 | 關鍵下游 | 對清理的意義 |
|---|---|---|
| `teacher_judge_sessions.create_message` | `get_session`、`ensure_active`、`selected_file_for_chat`、`redact_message_content`、`chat_with_rubric`／`analyze_attachments_itemwise`、`schedule_summary` | route 不是薄轉發；先做 class/session/file/revision/attachment gate，再持久化訊息與 metadata。刪任何其中一個都會改輸入或保存契約。 |
| `service.chat_with_rubric` | `_run_proposal_tool_loop`、`_execute_checklist_tool`、`_parse_chat_reply_payload`、`_conversation_focus_from_content`、`_proposal_unavailable_reply` | 工具 staging、回覆解析與 teacher-facing fallback 共同形成現行 proposal contract；舊 `parse_chat_update` 不在這條線。 |
| `script_artifact_service.create_artifact` | `ensure_script_generation_supported`、`_with_template_command_catalog`、`_build_reviewed_script_for_artifact`、`build_reviewed_script`、`_artifact_to_public` | `_rubric_snapshot` 不在 create/regenerate 下游；真正的生成與審查仍有多個 caller，不能以低直接呼叫數誤刪。 |
| `script_run_service.create_script_run` | `_resolve_running_targets`、`_run_to_public`、`get_artifact` | run 先確認 approved，再做 running VM、class member、owner、IP、SSH key 與 ≤5 目標檢查；`target_scope` 不是單純 UI 欄位。 |
| `ai_assignment_service.get_student_ai_check` | `get_student_ai_assignment`、`_requested_item_id`、`_check_to_student` | `requested_item_id` 會影響 checkpoint 對應與學生看到的結果；不能從 Teacher Judge route 的未使用參數判定為 dead。 |

### 2-1 歷史 workflow 計畫的現況

被刪除的舊計畫 `2026-09-13-teacher-judge-chat-workflow-convergence-plan.md` 及其
提出的 `chat_workflow.py`、`rubric_normalization.py`，在
`fdc52bcf..f2b6841f` 的 tools-first 重構中已移除；目前 `service.py` 保留
`_normalize_check_steps`、`_normalize_rubric_items`、`_run_proposal_tool_loop`、
`_execute_checklist_tool` 與 `_parse_chat_reply_payload` 等實際核心。

因此本次「功能收斂」的意思是：

- 以現有 service／route／model 邊界做減法與命名說明；
- 不新增第二個流程入口、不新增 persisted workflow state、不恢復批次 parser；
- itemwise 仍採 extraction → 單項 `chat_with_rubric`、有序聚合、單列失敗隔離；
- transient proposal、前端 Apply、`analysis_revision`、腳本三閘與 executor 邊界維持。

## 3. 死碼與無用功能佔用盤點

### 3-1 P0：唯一可直接排入 cleanup 的私有 helper

| symbol | 實檔證據 | 呼叫／消費查證 | 判定與安全處置 |
|---|---|---|---|
| `_rubric_snapshot` | `backend/app/ai/teacher_judge/script_artifact_service.py:321-326`；只把 `analysis.model_dump()` 加上 `template_key`。 | graph `in_degree=0`；`rg -n "rubric_snapshot\\(" backend/app backend/tests` 只有定義。`create_artifact:1462-1472` 與 `regenerate_artifact:1562-1573` 直接使用 `analysis_dump`／`_with_template_command_catalog`。 | 高信心真死碼。日後以獨立 commit 移除；先跑 artifact／session／template tests、Ruff、`git diff --check`。不可與 enum、API、UI 或 safety gate 一起改。 |

### 3-2 P1：沒有現行生產者，但仍是相容面

| surface | 實際呼叫鏈／證據 | 為何不是現在可刪 | 建議的後續收斂 |
|---|---|---|---|
| `TeacherJudgeScriptStatus.reviewed` | enum/model `teacher_judge_script_artifact.py:16-21`；`approve_artifact:1663-1691`；route `teacher_judge_scripts.py:144`；frontend `AiJudgePanel.jsx:1894-1917,2224-2234`；測試 double 仍回傳 `reviewed`（`test_teacher_judge_script_artifacts.py:312-342`）。 | `_resolve_status:398-404` 現在只產生 `approved` 或 `review_failed`，但舊 row、approve route、UI 與測試可讀取／轉換 reviewed。 | 先查 DB row 數與最後更新時間；再決定是否標註 legacy read-only、加 deprecation telemetry，最後才設計資料轉換與 API/UI 退場。刪 enum 需 migration，不屬 cleanup。 |
| `TeacherJudgeScriptStatus.draft` | model default `teacher_judge_script_artifact.py:107-115`，schema literal `schemas.py:103-105`。現行 `create_artifact` 會把生成結果寫成 approved/review_failed，沒有直接寫 draft。 | ORM default、舊資料反序列化仍可能遇到 draft；刪除會改變 model 建構與讀取契約。 | 查詢是否存在 draft row；若為零，也要先把 default、schema、migration、前端 fallback 一起收斂，另開變更。 |
| `TeacherJudgeScriptRunStatus.cancelled` | model/schema `teacher_judge_script_run.py:20-26`、`schemas.py:109-110`；學生 schema 也允許 cancelled。executor 取消 worker 後在 `execute_script_run:708-729` 記錄 failed，不寫 cancelled。 | 沒有 Teacher Judge run cancel API／production writer，但既有 serialized status、前端 terminal map 或歷史 row 可能存在；async task `worker.cancelled()` 不是 DB enum writer。 | 先查 DB 與所有讀取端；若產品不需要取消，才一起處理資料轉換、status literal、UI、OpenAPI 與 migration。 |
| `target_scope=all_with_vm/running_only` | model default `teacher_judge_script_run.py:14-18,62-68`；schema literal `schemas.py:106-108,367-379`；migration `tjse01...:43-48,189-192`；前端兩個 create run 方法固定送 manual；service `create_script_run:224-235` 明確拒絕非 manual。 | 這是未啟用的歷史 capability，不是可任意刪的常數；資料庫 enum/default 仍接受，public read 可回傳舊值。 | 目前 canonical request 是 manual，但保留 read compatibility。日後若收斂成 Literal["manual"]，須先處理現有 row、migration default、OpenAPI、前端與回滾。 |
| `rubric_proposal` message type | model enum `teacher_judge_session.py:26-29`、schema `schemas.py:115,236-240`、migration `tjs01...:25-30`；route `create_message:620-625` 只把 proposal 放在 response，assistant 實際寫 `message_type=chat`（:566-571）。frontend 讀 `response.rubric_proposal`。 | 它是明確的 transient response 契約與舊資料 enum；沒有 writer 不等於沒有 consumer。頁面刷新後 proposal 消失是目前設計，不可藉刪欄位「修好」。 | 先保留；若要改成持久化或移除 enum，需另定 proposal lifecycle、Apply/revision、清理舊 row 與前端契約。 |
| `TeacherJudgeScriptLanguage.shell/bat` | model enum `teacher_judge_script_artifact.py:29-32`、schema `schemas.py:101`、migration `tjse01...:21-27`；現行 create/regenerate `:1510,1617` 只寫 python，executor 沒有依 language 分支。 | 可視為休眠格式／歷史能力；artifact public 仍序列化 `script_language`，資料庫可能已有值。 | 查 DB 與匯出／讀取消費後，再決定是否資料轉換成 python 或移除 enum；不以「目前生成器只寫 Python」直接刪。 |
| `script_quality_validator` 的 `_collect_commands_needing_which`、`_collect_which_commands`、`_calls_named_helper` | 定義於 `script_quality_validator.py:109-151`；`check_script_quality:412-419` 現在走 `_scan_calls_once`，但 :414-416 明確說三者保留為 compatibility wrappers。 | 不在 hot path 不等於死碼；外部 import、舊測試或下游工具可能仍依賴名稱。 | 先查 package exports、外部使用與測試；若確定無 consumer，再做 deprecation → 移除，並保留單一 AST scan 的行為。 |
| `_build_result`／`_build_reviewed_script_for_artifact` 的相容包裝 | `script_artifact_service.py:1420-1437`；create/regenerate `:1488-1491,1596-1599` 皆經 wrapper；wrapper 以 `signature(build_reviewed_script)` 判斷 `include_usage`。 | 這是為舊 test double／呼叫者兼容的實際路徑，不是未使用 helper；create/regenerate 另有 reviewed→approved 相容分支 `:1492-1500,1600-1605`。 | 先統一所有 caller／test double 的 signature 與 status，再刪 introspection／相容分支；要保留逐次生成、usage records 與 approved 語意。 |

### 3-3 P2：跨邊界未對齊，不能混入死碼清理

#### `CoursesService.startAiCheck` 的現行證據

- `frontend/src/pages/personal/dashboard/StudentHomePage.jsx:548-564` 的整份任務按鈕，
  以及 `:566-585` 的 checkpoint 按鈕，都呼叫 `CoursesService.startAiCheck(...)`。
- 實際 checkout 的 `frontend/src/services/courses.js:1-72` 匯出物只有 schedule、path、
  AI assignment 讀取、PDF、machine、completion、room；沒有 `startAiCheck` method。
- `backend/app/api/routes/courses.py:232-252` 只有學生完成狀態 PUT，docstring 明示
  「teachers decide when to run」；`:255-272` 是帶 run id 的結果 GET，沒有學生啟動
  check 的 POST。
- 因而這不是「找到一個可刪的未使用函式」，而是 UI 呼叫與 API 契約失配；執行到按鈕
  時可能在發 HTTP 前就得到 undefined-method error。graph 中同名節點的 stale snippet
  不足以推翻上述實檔證據。

**不在本次修正。** 需要產品／權限決策二選一：

1. 學生不可啟動：移除或隱藏兩個按鈕及其 loading／toast／polling 狀態；或
2. 學生可啟動：新增明確授權的 endpoint、run ownership、item scope、rate limit、
   target snapshot 與結果投影，並同步 service、schema、frontend、測試。

直接補一個 `startAiCheck` 而不定義權限，會突破定案文件的「老師核准、老師啟動」邊界；
直接刪按鈕也會改變現行 UI 行為，兩者都不是 docs-only cleanup。

### 3-4 反向盤點：明確仍在用，禁止誤刪

| symbol／欄位 | 證據 | 收斂結論 |
|---|---|---|
| `pending_judgement` | executor import `script_executor_service.py:20-23`，valid output 後 `:429-435` 呼叫；graph 的 zero in-degree 是索引缺邊。 | 保留，這是執行完成到 AI 判讀之間的明確狀態。 |
| `requested_item_id` | run service `create_script_run:224-268` 寫入 snapshot；`ai_assignment_service._requested_item_id:83-85` 與 `_latest_student_checkpoint_checks:162-189` 消費；`get_student_ai_check:420-446` 依它投影。 | 保留；它是 checkpoint 對應契約，不是 Teacher Judge 頁面上的多餘參數。 |
| `export_to_excel` | `export.py:32`；backend `rubric.py` route 與 frontend `AiJudgeService.downloadExcel:219-222` 使用。 | 保留；現有 formula injection 是安全修補議題，不是刪除匯出功能的理由。 |
| `get_artifact_public`／`get_script_run_public` | scripts route 匯入並回傳；graph 與 `rg` 均有 route caller。 | 保留，public serializer 是權限後的資料邊界。 |
| `archive_artifact`、`rename_artifact`、`delete_artifact` | 各有 scripts route 與 frontend action；archive 還會影響 regenerate guard。 | 保留；不要因 approve path 休眠就刪整個 artifact lifecycle。 |
| itemwise `analyze_attachments_itemwise`、`_itemwise_reply` | route `create_message:506-521`；測試驗證 source order、mixed Ready／needs-information、failure isolation。 | 保留；批次拆解是現行功能，不以 prompt 或舊 batch plan 取代。 |
| `conversation_focus`、`item_results`、transient proposal | route `create_message:552-563,620-625`；frontend `AiJudgePanel.jsx:1541-1547` 讀取並保留 unresolved rows。 | 保留；這些欄位分別是跨輪摘要／結果 metadata／暫存 Apply 契約，不能合併成一個自由文字欄位。 |

## 4. 「過硬安全邊界」與真正的功能收斂

### 4-1 現行限制的資料流位置

| 限制 | 實作位置 | 現在的行為 | 清理時的結論 |
|---|---|---|---|
| 無 rubric 不產生可套用提案 | `create_message:544-551` | 無 selected file 時即使 service 回 proposal，route 也改寫回覆並丟 proposal。 | 保留。它防止沒有來源檔／revision 的提案進入 Apply；不是「無用功能」。 |
| tool-only proposal | `service._run_proposal_tool_loop:1650+`、`_execute_checklist_tool:1390+` | server staging 才能成為 proposal；reply 內 `updated_items` 忽略。 | 保留。不要恢復舊 `parse_chat_update` 或從 prose 解析項目。 |
| server id、duplicate、edit read-before-write | `_mint_proposal_item_id:626`、`_duplicate_title_owner:639`、tool read set | create id 由 server 配發；edit 必須先讀現況；重複 title 拒絕。 | 保留。這些是資料完整性與 prompt injection 防線。 |
| catalog／template normalization | `_normalize_check_steps:371`、`validate_check_steps_with_issues`、`coerce_timeout_seconds` | catalog 是受控能力參考；完整 readonly argv 可 recovery 成 `system.run_command`，缺 argv 才是真缺口。 | 不要把 catalog 誤收斂成封閉 proposal whitelist；保留 server argv/no-shell/timeout gate。 |
| `detectable=manual` 整表阻擋 | `automation_support.py:91-167,170-183`；測試 `test_manual_item_blocks_the_whole_script:69-77`；frontend `AiJudgePanel.jsx:239-257` | 任一 manual 會產生 `automatic_detection_unsupported`；UI 要求所有項目顯示可以才製作。 | 這是可能過硬的產品語意，但屬行為變更，不在死碼清理。在 `detection_method` 與 `check_steps` 完整時，`auto + judgement_mode=teacher` 可通過參數缺口檢查，但 teacher 結果仍要求 unknown。 |
| coverage 全項覆蓋 | `script_coverage_validator.validate_coverage:86-148` | `uncovered = rubric_ids - covered`；任何未覆蓋 item 都使 coverage 不 approved。 | 保留。若日後允許證據項／導師複核項分流，必須先定義 artifact 是否可執行、未覆蓋項如何顯示、AI reviewer 如何驗證。 |
| AI judgement 每項合法引用 | `_validate_ai_judgement:122-186` | item id 不可重複、evidence ref 必須存在；pass/fail 需有效 evidence；teacher mode 只能 unknown；rubric id 不得漏。 | 保留。這不是多餘 schema；它防止把缺證據回覆誤當判定。 |
| policy／quality／coverage／AI reviewer 四道生成閘 | `build_reviewed_script:895+`、`script_policy.py:337-388`、`script_quality_validator.py:390+` | 生成失敗可 bounded retry/fix，但最後只有 approved 或 review_failed。 | 保留。移除任何一閘都會把生成碼越權風險帶到 executor。 |
| approved 才能 run | `script_run_service.create_script_run:234-243`、executor `:514-522` | 非 approved 直接 400／failed；run targets 由 class member、running VM、owner、SSH key 驗證。 | 保留。不要以 frontend status、reviewed label 或舊 scope 值繞過。 |
| execution sandbox | `script_executor_service`；settled doc §5 #24-30 | manual targets、≤5、root@固定 remote root、60s timeout、IP/VM/owner 二次驗證、result schema／size。 | 保留。這是核心安全邊界，不與 chat 收斂一起重構。 |

### 4-2 `manual` 全表阻擋是否要放寬：不在本次偷改

現行程式與測試的語意是「腳本只收集可安全自動取證的 item」；`manual` 代表目前沒有
可接受的自動取證能力。把它改成「仍可收集證據、由導師回答」需要先決定至少以下
問題：

1. item 是否改用 `detectable=auto`、`judgement_mode=teacher`，還是新增第三種狀態？
2. 這個 item 是否要進 script coverage？若不進，artifact 的 snapshot／reviewer 如何
   表示它是有意排除而不是漏做？
3. run 完成後，`_validate_ai_judgement` 要如何保留 unknown、teacher_review_item_ids
   與學生投影，且不產生 score 或 pass/fail？
4. mixed rubric 失敗時，Ready item 是否仍能建立同一 artifact，或要拆成多個 artifact？
5. frontend `getScriptCreationBlocker`、提示文字、測試與 API error detail 是否同步？

未回答前，最安全且不影響現行功能的策略是維持整表阻擋，並把這項需求另列為
「證據收集與導師複核」功能 RFC；不能利用清理死碼的名義刪掉 blocker、coverage 或
AI validation。

### 4-3 不應被「功能收斂」誤刪的安全弱點紀錄

定案文件 §5-6 已列出 static policy best-effort、stdout/stderr 未遮蔽、result symlink
競態、Excel formula injection、auto-approve、附件 extracted text 未遮蔽、`while 1`
可繞與 pve_log memory token 等觀察。它們的處理方式不是刪掉安全層：

- policy／quality／AI reviewer 仍保留；需要補強就以單一風險與回歸測試處理。
- pve_log pending token 與 Teacher Judge run DB 狀態是不同邊界，不要因「都有 SSH」
  把兩個模組合併。
- auto-approve 是否需要每次人工核准是威脅模型決策，不可在清理時擅自改成另一個
  approval lifecycle。

## 5. 零行為變更的收斂／合併順序

以下順序讓每一步都可回退，且不需要新增 API、資料表或 workflow framework。

### Phase 0：建立基線（本次已完成，只讀）

- 以定案文件 §0／§2-B／§4／§5 對照 route → service → persistence → executor →
  student projection。
- 用 graph trace 加 `rg` 查呼叫者；遇到 stale graph 以實檔為準。
- 記錄 branch／HEAD／既有工作樹，不把使用者原有 D/M 變更納入本次文件以外的範圍。
- 不執行 DB query、migration、vLLM、瀏覽器、VM 或 SSH 寫入操作。

### Phase 1：單一死碼 patch（未在本文件執行）

只移除 `_rubric_snapshot`，不改其呼叫端邏輯，因為呼叫端目前本來就沒有使用它。

建議驗證：

```powershell
cd backend
uv run python -m pytest tests/test_teacher_judge_script_artifacts.py tests/test_teacher_judge_sessions.py tests/test_rubric_template_commands.py -q
uv run ruff check app/ai/teacher_judge tests/test_teacher_judge_script_artifacts.py tests/test_teacher_judge_sessions.py tests/test_rubric_template_commands.py
uv run python -m py_compile app/ai/teacher_judge/script_artifact_service.py
```

若任一測試或 import 依賴該名稱，立即停止刪除，改回 P1 compatibility inventory；不
修改測試來掩蓋差異。

### Phase 2：相容面資料盤點（只讀）

在確認測試 DB／實際 target 後，分別查詢：

- artifact status：draft、reviewed、review_failed、approved、archived；
- run status：cancelled 及各 scope；
- artifact script_language：shell、bat；
- message_type：rubric_proposal；
- run snapshot：是否有 `requested_item_id`；
- API／frontend／package exports 的外部 caller。

輸出只需固定路徑的最新摘要（數量、最早／最新時間、owner/class scope、是否仍有
讀取端），不建立不必要的 run-id 歷史檔，也不在不明 DB 上設定
`PYTEST_ALLOW_NON_TEST_DB=1`。

### Phase 3：在現有核心內減少相容複雜度（逐項、另案）

只有 Phase 2 證明沒有 row、沒有外部 caller、且有回滾方案後，才可逐項處理：

1. 先把 request canonical value 收斂（例如新 run 一律 manual），保留 public read 與
   舊 row decode。
2. 再將 legacy route／client 改成明確 read-only 或顯示 deprecated，不直接移除。
3. 最後才做 migration、schema literal、frontend fallback 與測試的同步刪除。

不要把 `_build_result` introspection、reviewed status、target scope、message enum
與 `_rubric_snapshot` 放同一個 commit；每項都要能單獨回退。

### Phase 4：安全邊界 RFC（若產品真的要放寬）

針對 manual／teacher-review 的需求，先寫出輸入狀態、artifact coverage、run 結果、
學生投影與權限矩陣，再一次更新：

```text
automation_support
  + script_coverage_validator
  + script_result_analysis_service
  + script_artifact_service / error detail
  + AiJudgePanel blocker / wording
  + backend + frontend regression tests
```

在這之前不刪 blocker、不跳過 coverage、不把 teacher mode 轉成 pass/fail，也不把
缺少資訊填成模型猜測。

### Phase 5：跨邊界 `startAiCheck` 決策（另案）

由課程功能 owner 決定「移除學生按鈕」或「建立具授權的學生啟動流程」。Teacher Judge
cleanup 只需在 release note／issue 連結這項 mismatch，不把課程 route 硬塞進
`teacher_judge` service。

### Phase 6：合併前驗收

每個小 patch 都必須同時通過：

- `git diff --check` 與 `git diff --cached --check`；
- 受影響的 backend／frontend focused tests；
- schema／OpenAPI diff 明確為空，或已取得明確變更授權；
- route、DB enum、frontend consumer、migration、下游 projection 的反向搜尋；
- rollback 可還原 code、資料列與 UI 契約。

focused tests、Ruff、py_compile 仍只證明靜態／局部行為；不宣稱 live vLLM、已認證
瀏覽器、Proxmox、VM/SSH 或完整 E2E。

## 6. 不可移除的契約清單

以下項目是「收斂後仍應只保留一份 source of truth」的清單：

| 契約 | 唯一來源／閘門 | 任何 cleanup 的禁止事項 |
|---|---|---|
| 班級與 owner | `_access`、`require_teaching_access`、每個 service 的 class filter | 不以前端 class id 或 route path 推定權限。 |
| session/file ownership 與 archived | `get_session`、`selected_file_for_chat`、`ensure_active` | 不讓 archived session 送 chat／Apply；不將 selected file 狀態藏在 prompt。 |
| revision | chat 預檢、生成前檢查、生成後 revalidation、PATCH expected revision | 不刪任一層；否則舊模型回覆可能覆蓋新表。 |
| proposal | server tool staging → transient response → frontend explicit Apply | 不從 prose／`updated_items` 自動寫 DB；不新增第二個 proposal store。 |
| id／duplicate／edit read | server mint、title key、read_ids | 不恢復模型指定 id 或未讀直接 edit。 |
| `check_steps` | normalize + enabled command catalog + parameter validation | 不讓自由文字 `detection_method` 成為執行腳本。 |
| timeout／argv／no-shell | template command validator、script policy、executor timeout | 不以 catalog external 為由取消 argv/no-shell/timeout。 |
| script generation gates | policy、quality、coverage、AI reviewer、retry budget | 不為了讓 UI 變綠而刪 gate 或降低 retry 邊界。 |
| status | `_resolve_status`、approve compatibility route、artifact model | 不把 reviewed/draft 直接改名或刪 enum 而不做資料盤點。 |
| run authorization | approved、manual scope、class member、running VM、owner、SSH key | 不把 all_with_vm/running_only 舊值當成可重新啟用能力。 |
| execution | fixed remote root、root user、60s timeout、≤5 targets、background worker | 不與 pve_log 任意 SSH 流程合併，不移除二次驗證。 |
| result | `teacher_judge_result.v1`、Pydantic limits、evidence refs、AI status | 不將未驗證 stdout／stderr 直接映射成 pass/fail。 |
| student projection | `_requested_item_id`、`_check_to_student`、course schemas | 不因 Teacher Judge 目標是 checklist 就刪掉現有 score/max_score 欄位；那是下游相容契約。 |
| attachment itemwise | extraction-only、per-item normal core、order/duplicate/failure isolation | 不退回單一 batch answer，也不新增第二套 rubric schema。 |

## 7. 驗收清單與目前未驗證項目

### 7-1 文件／追查驗收

- [x] 定案文件的現行 tools-first 流程與歷史 §2-A 分開描述。
- [x] 私有 helper 以 graph、`rg`、實際呼叫端三重檢查；只有 `_rubric_snapshot` 進入
  高信心 dead-code 候選。
- [x] `reviewed`、`cancelled`、scope、message type、script language 與下游欄位逐項
  分類，不以「無現行 writer」直接刪除。
- [x] 把 `startAiCheck` mismatch 從 Teacher Judge backend cleanup 分離。
- [x] 把 manual／coverage／AI reference 視為行為邊界，未在文件中放寬。
- [x] 明確寫出不新增 API、schema、migration、workflow module 的合併順序。

### 7-2 本次未執行的驗證

本次是文件整理，沒有修改 production code，因此沒有宣稱 backend／frontend tests 通過。
仍未驗證：

- 實際測試 DB 中各休眠 status／enum 值的數量與年代；
- authenticated browser 中學生頁按鈕的實際互動錯誤；
- live vLLM tool rounds、模型名稱／readiness、附件解析與長回合行為；
- Proxmox、VM、SSH、root remote execution 與多行程 background worker；
- Excel formula injection、result symlink race、stdout/stderr secret leakage 的 runtime
  可利用性。

## 8. 最終判定

1. 目前不需要以「大重構」合併 Teacher Judge；tools-first `service.py` 已是現行
   提案核心，歷史 workflow module 不應恢復。
2. 可獨立清理的高信心死碼只有 `_rubric_snapshot`；其餘候選先走相容資料盤點。
3. `reviewed`／`draft`／`cancelled`／scope 舊值／shell-bat／message enum 是佔用面，
   但也是資料與版本契約；現在刪會改變 public read、migration 或舊 row 行為。
4. `startAiCheck` 是 frontend↔course API 失配，需產品決策，不應透過 Teacher Judge
   cleanup 偷補 endpoint 或刪除 UI。
5. manual 整表阻擋、coverage 全覆蓋、AI judgement reference completeness 與
   approved-before-run 都是安全／語意邊界。若要放寬，必須另案同時更新 backend、
   frontend、schema、測試與結果投影；本次維持現狀即可保證「不影響目前功能」。
