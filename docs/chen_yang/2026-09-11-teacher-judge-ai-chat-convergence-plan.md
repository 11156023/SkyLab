# Teacher Judge AI Chat 生產線收斂計劃

日期：2026-09-11
性質：詳細分析、實作計劃與完成紀錄。

實作狀態：2026-09-11 已完成程式收斂；保留本文件的分階段內容作為變更依據與驗收清單。

## 1. 結論先行

本次不再建立新的 AI workflow、intent router、Proposal API、草稿資料表或狀態機。
現行 session chat 已經能同時接收文字與文件、讀取歷史、判斷一般問答或檢查需求、
拆出 Ready 提案，並以 `analysis_revision` 保護 Apply。正確方向是把它定為唯一的
Teacher Judge 編寫入口，再刪除仍殘留的「評分表上傳後立即分析並正式寫入」生產線。

目標只保留一條路徑：

```text
新增檢查
  -> 建立 session + 專屬空白 TeacherJudgeFile
  -> AI Chat（文字可空、附件可選）
  -> chat_with_rubric 統一判讀
       ├─ 一般詢問：只回覆引導，不產生 Proposal
       ├─ 單一需求：只核查該需求
       ├─ 多條需求／文件：逐條拆解
       │    ├─ Ready -> 個別 Proposal operation
       │    ├─ 缺少資訊 -> 只在 reply 追問
       │    └─ 不支援 -> 只在 reply 說明
       └─ 全表核對：只供「儲存並製作」既有流程使用
  -> 老師逐項勾選「同意套用」
  -> PATCH 正式 analysis_json（expected_revision）
  -> 既有腳本生成／審查／執行流程
```

這是「合併輸入與理解生產線」，不是把 Proposal、正式檢查表與腳本生成混成同一狀態。
AI 仍只能提出候選；只有老師 Apply 後的 `analysis_json` 才是正式資料。

## 2. 已核對的現況

分析基準是目前工作樹，不只依賴既有文件。相關程式正有尚未提交的用語整理修改；
後續實作必須保留它們，不可用舊版本覆蓋。

### 2.1 目前仍並存的兩條 AI 生產線

| 生產線 | 入口 | AI 函式 | 結果與副作用 |
| --- | --- | --- | --- |
| A：上傳即分析 | `POST /teaching-classes/{class_id}/judge/files/`；legacy `POST /rubric/upload` | `analyze_rubric()` + `ANALYZE_SYSTEM_PROMPT` | 一次萃取完整文件，立即建立或覆蓋 `TeacherJudgeFile`，直接把結果寫成正式 `analysis_json` |
| B：Session Chat | `POST /sessions/{session_id}/attachments` + `POST /sessions/{session_id}/messages` | `chat_with_rubric()` + `CHAT_SYSTEM_TEMPLATE` | 文件與文字都作為對話輸入；Ready 內容只回傳 Proposal，老師 Apply 後才更新正式檢查表 |

兩條線都會做文件解析、command catalog 注入、item normalization 與可自動檢查判斷，
但寫入語意不同：A 直接正式化整份 AI 結果；B 先讓老師審查差異。這是目前最大的產品
衝突，也是 prompt 與後端驗證重複的主要來源。

### 2.2 Session Chat 已具備的能力

不需要重做下列功能：

- `CreateCheckDialog -> createBlankSession()` 已先建立 session 與專屬空白檢查表；正常 UI
  不必等待文件才有正式 revision 容器。
- `ChatPanel` 已有同一輸入框旁的「＋」，可上傳 `.md`、`.txt`、`.doc`、`.docx`、`.pdf`；
  也允許只有附件、沒有文字就送出。
- `upload_session_attachment()` 已做班級／session 授權、狀態確認、大小上限、格式驗證、
  文件解析、SHA-256 與檔案保存。
- `bounded_history()` 會把已送出的附件解析內容重新帶回近期對話；超過 20 則或
  24,000 字才依既有邊界裁切，summary 仍可保留語意背景。
- `create_message()` 會把目前 `analysis_json`、`environment_keys`、command catalog、
  對話歷史與本輪附件一起交給 `chat_with_rubric()`。
- `CHAT_SYSTEM_TEMPLATE` 已明定一般詢問不產生提案、附件逐條解讀、單條聚焦、多條拆解，
  並區分 Ready／缺少資訊／不支援。
- `_ready_proposal_changes()` 已只保留通過 normalization 與 command/check-step 驗證的
  `auto` 變更及明確刪除；前端 `buildProposalDiff()` 再拆成 `add/update/delete`。
- `ProposalPanel` 已逐項顯示、預設逐項可選，只有「同意套用」才呼叫
  `updateFileAnalysis(expected_revision)`；保存失敗時不接受半成品。
- 「儲存並製作」仍只讀已保存的正式 revision，沒有聊天室 tool-call 捷徑。

### 2.3 已確認的殘留與重複

1. `AiJudgePanel.handleUpload()` 已沒有正常 UI 觸發點；它只剩同一段死流程內的
   conflict dialog 回呼。`pendingConflictFile`、`selectedTemplateKey`、
   `analysisTemplateKey`、`uploadedFileName` 也因此成為殘留狀態。
2. `AiJudgeService.uploadFile()` 除上述死流程與測試外沒有正式 frontend consumer。
3. `AiJudgePanel.handleSendMessage()` 仍保留無 session 時呼叫 legacy
   `AiJudgeService.chat()` 的分支，但現行 `RubricsTab` 只在已建立的 active session 內渲染。
4. `POST /rubric/upload` 仍被 `vllm-service/tools/campus_ai_integration_test.py` 使用；這是
   legacy 測試 consumer，不是目前 Teacher Judge UI。若移除路由，應刪除該孤立 smoke case，
   不為它增加 class/session CLI 參數。
5. `ANALYZE_SYSTEM_PROMPT` 與 `CHAT_SYSTEM_TEMPLATE` 重複維護可偵測性、catalog、argv、
   cwd、timeout、success criteria 與輸出契約；保留兩者只會繼續漂移。
6. `attachment_service` 與 direct-upload `file_service` 都做 filename、suffix、hash、
   `parse_document()`、檔案落盤與清理。停止 direct upload 後，新增文件只需 attachment
   lifecycle；不需要再抽一層共用 upload framework。
7. Session assistant message 目前同時在 API response 與 `metadata_json.rubric_proposal`
   保存候選，但前端 reload 並不還原它；這與「未 Apply Proposal 是頁面暫存」的既定邊界
   不一致，也形成沒有 consumer 的重複資料。
8. `RubricSourceRail` 現在只顯示 session 已綁定的單一來源，不再提供真正的來源選擇；
   對新建的 `source_type=created` 檢查而言，它只是第二份狀態摘要與刪除入口。
9. `app/services/rubric_service.py`、`app/infrastructure/ai/rubric.py`、
   `app/schemas/rubric.py` 是舊路徑 re-export；實際 production import 已轉到
   `app.ai.teacher_judge` / `app.infrastructure.ai.teacher_judge`，只剩 compatibility test。

## 3. 目標契約

### 3.1 唯一使用者入口

Teacher Judge 的需求輸入只存在於 session chat。每輪請求沿用現有兩個 API：

1. 有文件時，先用 `POST .../sessions/{session_id}/attachments` 解析並取得 attachment ID。
2. 用 `POST .../sessions/{session_id}/messages` 一次送出文字、attachment IDs 與目前
   `analysis_revision`。

不新增「分析附件」「辨識意圖」「建立 Proposal」等額外 endpoint。附件 upload 只表示
「準備本輪聊天資料」，不表示同意修改正式檢查表。

### 3.2 單一 AI 回應語意

沿用目前模型 JSON 與 public response，不新增 schema：

| 使用情境 | `reply` | `rubric_proposal` | 正式資料 |
| --- | --- | --- | --- |
| 普通聊天／能力詢問 | 直接回答或引導 | `null` | 不變 |
| 一條資訊不足需求 | 指出最少且具體的缺口 | `null` | 不變 |
| 多條需求，部分 Ready | 逐條列 Ready／缺資料／不支援 | 只含 Ready 的個別 operation | 不變 |
| 上傳檢查表或需求文件 | 逐列／逐項核查，行為同文字需求 | 只含 Ready 的個別 operation | 不變 |
| 老師同意部分項目 | 不需再次呼叫 AI | 由前端選取既有 operation | 只保存已選項目 |

`proposal_status` 繼續只作模型輸出驗證與 repair 判斷，不增加另一份前端 workflow state。
新項目預設 `checked=false`；除非老師明確表示已達成或附件提供可直接判定的證據，不能因
文件內有核取符號或完成語句就把「檢查需求」誤當成已完成結果。

### 3.3 三種資料的 ownership

| 資料 | 唯一 source of truth | 保留原因 |
| --- | --- | --- |
| 對話與上傳文件 | `teacher_judge_session_messages` + `teacher_judge_session_attachments` | 支援一般聊天、後續補資料及有界歷史 |
| 待確認提案 | 目前頁面的 `pendingProposal` | 尚未取得老師同意，不應成為持久化工作流狀態 |
| 正式檢查項目 | session 綁定 file 的 `analysis_json` + `analysis_revision` | autosave、腳本 snapshot 與 revision gate 的唯一依據 |

附件不是正式檢查表，Proposal 也不是正式檢查表。腳本生成不得讀附件或未 Apply Proposal
繞過 `analysis_json`。

## 4. 減法清單

### 4.1 必須刪除

| 範圍 | 刪除內容 | 收斂後替代 |
| --- | --- | --- |
| Frontend UI | `handleUpload()`、同名檔 conflict modal、`pendingConflictFile`、`selectedTemplateKey`、`analysisTemplateKey`、`uploadedFileName` | `handleAddAttachment()` + `handleSendMessage()` |
| Frontend API | `uploadFile()`、legacy `chat()`、未使用的 `createBlankFile()`、`updateFileMetadata()`、`deleteFile()`；同步刪除只服務這些方法的測試 | session attachment/message、`createBlankSession()`、analysis autosave；歷史檔只保留下載 |
| Frontend branch | `handleSendMessage()` 的無 session legacy direct-update 分支 | active session 是工作區前置條件；舊無來源 session 仍由後端安全回覆一般聊天 |
| Class file route | `POST /judge/files/`、`POST /judge/files/blank`、未使用的 metadata PATCH 與單獨 DELETE | session create 建立空白 file；chat attachment 提供文件；整個檢查刪除負責 transaction cleanup |
| Legacy rubric route | `POST /rubric/upload`、`POST /rubric/chat` | session chat；`/rubric/download-excel` 是否保留依其獨立 consumer 決定，不綁入本次 AI 合併 |
| AI service | `analyze_rubric()`、`ANALYZE_SYSTEM_PROMPT`、無 consumer 的 `close_http_client()`、只供 direct upload 使用的 imports/exports | `chat_with_rubric()` + `CHAT_SYSTEM_TEMPLATE`；client lifecycle 仍由 `close_ai_clients()` 管理 |
| Upload service | `prepare_file_payload()`、`save_analyzed_file()`、`update_file_metadata()`、public `delete_file()` wrapper、direct-upload name conflict／overwrite helpers 與 `ConflictStrategy` | `attachment_service.create_attachment()`；正式項目只走 `update_file_analysis()`；session delete 繼續使用 stage/finalize/restore helpers |
| Schemas | direct upload response、legacy direct chat request/response alias；`TeacherJudgeRubricAnalysis.raw_text` | session chat response；附件內容保存在 attachment，不複製進 analysis JSON |
| Compatibility shims | 無 production consumer 的 rubric service／schema／AI-client re-export 與只驗證 re-export 的測試 | 直接 import 維護中的 Teacher Judge 模組 |
| Integration CLI | `run_rubric_upload`、`--rubric-file`、`--skip-rubric` 與預設 rubric 檔 | 不把 class-scoped產品流程硬塞進通用 AI smoke；session 行為由 focused API tests 驗證 |
| Proposal persistence | 新 message 不再寫 `metadata_json.rubric_proposal` 與重複的 `base_revision` | 本輪 response 回傳 Proposal；正式保存仍由 Apply 完成 |

刪除 route 時，同步更新 OpenAPI route assertion、API client tests、AI monitoring call-type
引用與相關 i18n key，不保留永久 deprecated wrapper。這些是同一次收斂的一部分，不另建
相容層。

### 4.2 必須保留

- `TeacherJudgeFile` 資料表、`selected_file_id`、`analysis_json`、`analysis_revision` 與
  session -> file -> artifact -> run lineage。
- `source_type`、`original_filename`、`file_hash` 與既有 uploaded file bytes，確保歷史
  session、fork、script snapshot 與下載仍可讀；不做 destructive migration。
- `attachment_service` 的反 prompt injection 前導、解析字數上限、五附件限制、pending
  ownership、訊息綁定後不可單獨刪除，以及 session 刪除時的檔案清理。
- `bounded_history()`、summary 邊界與歷史附件重新注入；本次不新增獨立向量檢索或文件庫。
- `_normalize_rubric_items()`、command catalog 驗證、Ready-only filter、repair 上限、
  `analysis_revision` 前後兩次 revalidation。
- 前端逐項 Proposal、部分 Apply、忽略、autosave flush、revision conflict 回復與
  「儲存並製作」單一腳本建立入口。
- 腳本 policy、quality、coverage、AI review、PVE/SSH 執行期重驗與執行結果 AI 判讀；
  生產線 C、D、E 不因 A/B 合併而改寫。

### 4.3 歷史相容但不再擴張

- 新 session 繼續建立 `source_type=created` 的專屬空白 file；不再建立新的
  `source_type=uploaded` file。
- 舊 uploaded source 仍能載入、fork 與下載。`RubricSourceRail` 對新 created source
  不再顯示；若目前 session 是歷史 uploaded source，只保留檔名與下載，不保留切換、
  新增來源或單獨刪除入口。待確認沒有 active uploaded session 後才可整段移除 rail。
- 舊 message 中的 `message_type=rubric_proposal` 或 proposal metadata 仍需可反序列化，
  但新 message 不再寫入。不要為清除舊 JSON 建 migration。
- `TeacherJudgeSessionCreateRequest.creation_mode/existing` 暫時保留為後端相容契約；目前 UI
  只呼叫 blank。先收斂 AI 生產線，不把 session 建立 contract 的破壞性縮減混入同一批。

## 5. 實作階段

### 階段一：先讓唯一 Chat 路徑成為回歸基準

1. 補齊 session chat focused tests：附件-only、文字+附件、普通問答、單一缺資料、
   多條部分 Ready、明確 delete、invalid command、revision conflict。
2. 明確斷言上傳文件只產生 Proposal，不直接改 `analysis_json`；Apply 前後 revision 與
   item 數必須可比較。
3. 明確斷言新 assistant message 不持久化 Proposal candidate；message 只保存可閱讀的
   reply、必要 metrics 與既有訊息資訊。

這一階段先固定保留行為，避免後續刪除 legacy code 時把安全 gate 一起刪掉。

### 階段二：刪除前端雙軌與死狀態

1. `AiJudgePanel` 只保留 `handleAddAttachment -> handleSendMessage -> pendingProposal -> Apply`。
2. 刪除 direct upload/conflict 分支及無 session direct chat fallback。
3. `AiJudgeService` 刪除沒有 production consumer 的 direct upload/chat/blank-file 方法。
4. 清除對應 SCSS、測試與 i18n key；不要留下不可達 dialog 或「上傳並分析後直接套用」文案。
5. 對 created source 隱藏「資料來源」面板；歷史 uploaded source 僅保留唯讀下載資訊。

### 階段三：刪除後端上傳分析線

1. 移除 class file direct upload route 與 legacy rubric upload/chat route。
2. 移除 `analyze_rubric()` 與 `ANALYZE_SYSTEM_PROMPT`；文件核查規則只在 chat prompt 維護。
3. 移除 direct-upload-only file helpers、response schemas、alias 與 compatibility shims。
4. 保留舊 uploaded rows 所需的 download、clone、session transactional delete helpers。
5. `chat_with_rubric()` 移除只服務 legacy caller 的 `ready_proposals_only` 自由組合：
   一般 session chat 固定 Ready-only；`is_refine=true` 固定回傳完整候選。不要再讓 caller
   任意組合兩個 flag 產生第三種語意。
6. 停止產生新的 `tj_rubric` monitoring event；歷史 call type 顯示仍保留。本次不新增
   telemetry 種類，session message 內既有 metrics 繼續使用。

### 階段四：刪除測試與文件中的假入口

1. 從 `campus_ai_integration_test.py` 移除 legacy rubric case 與兩個 CLI 參數；不新增
   `class-id`、`session-id` 或自動建班級等重型測試介面。
2. 更新 OpenAPI/retirement assertion，確認下列 mutation route 不再存在：
   `/rubric/upload`、`/rubric/chat`、`POST /judge/files/`、`POST /judge/files/blank`、
   metadata PATCH 與單獨 file DELETE；保留 list、analysis PATCH 與歷史檔 download。
3. 搜尋並清除 `analyze_rubric`、`ANALYZE_SYSTEM_PROMPT`、`uploadFile`、direct chat schema、
   conflict state 與舊「上傳後直接分析／套用」文案。
4. 更新 `docs/2026-09-11-teacher-judge-ai-module-full-analysis.md` 的五線描述：A 併入 B，
   Teacher Judge 剩 Chat／Script Generation／Execution & Judgement／Summary 四種不同職責；
   不把四者再抽成抽象 pipeline framework。
5. 將 `docs/teacher-judge-requirement-workflow-plan.md` 標示為已被本收斂計劃承接，保留歷史
   決策，不重寫成第三份現況規格。

## 6. 預計修改範圍

核心檔案：

- `frontend/src/pages/course-operations/class-workspace/AiJudgePanel.jsx`
- `frontend/src/pages/course-operations/class-workspace/AiJudgePanel.module.scss`
- `frontend/src/services/aiJudge.js`
- 對應 frontend tests 與 Teacher Judge i18n keys
- `backend/app/api/routes/teacher_judge_sessions.py`
- `backend/app/api/routes/teacher_judge_files.py`
- `backend/app/api/routes/rubric.py`
- `backend/app/ai/teacher_judge/prompt.py`
- `backend/app/ai/teacher_judge/service.py`
- `backend/app/ai/teacher_judge/file_service.py`
- `backend/app/ai/teacher_judge/schemas.py`
- Teacher Judge package exports、legacy shim 與 focused tests
- `vllm-service/tools/campus_ai_integration_test.py`

明確不修改：

- Teacher Judge、session、file、attachment、artifact、run 的資料庫 schema 與 Alembic。
- `template_command_service`、`automation_support`、script policy/quality/coverage validators。
- PVE/SSH executor、target resolver、VM 範圍、核准與確認 barrier。
- 非 Teacher Judge frontend、班級／週任務資料模型、學生端 checkpoint。

## 7. 驗收矩陣

| 案例 | 預期聊天結果 | 預期正式資料 |
| --- | --- | --- |
| 只問「你能檢查什麼？」 | 普通說明，沒有 Proposal | revision 不變 |
| 只上傳一份檢查表 | AI 逐條核查；Ready 項個別顯示 | Apply 前不變 |
| 附件含 3 條：2 Ready、1 缺 Port | reply 列出三條；Proposal 只有兩條 | Apply 選取後只新增所選兩條 |
| 附件含主觀評分項目 | 說明不支援自動檢測，不用空白值硬湊 | 不新增 manual 假 Proposal |
| 老師補「第 3 條 Port 是 8080」 | 只重查第 3 條 | 其他項目不變 |
| 一般聊天中附參考文件但未要求修改 | 只回答問題 | 不產生 Proposal |
| 明確刪除既有項目 | 一個 delete operation | 只有勾選並 Apply 後才刪除 |
| Proposal 出現後重新整理 | 未 Apply Proposal 可消失；歷史 reply/附件仍在 | revision 不變 |
| Apply 前已有其他 autosave | 409，清除過期 Proposal 並要求重建 | 不覆蓋新版本 |
| AI 回傳假 Ready 或非法 command | repair；仍無效則改為可行的補資料說明 | 不保存 |
| 附件解析失敗／超限／不支援格式 | 上傳階段顯示可行錯誤，不能送出該附件 | 不呼叫 AI、不改檢查表 |
| 歷史 uploaded session | 仍能開啟、查看檢查項目與下載原檔 | 不搬移、不刪除舊資料 |
| 全綠後按「儲存並製作」 | 走既有 review/generation 狀態 | 只使用已保存 revision |

## 8. 驗證方式

Backend focused checks（從 `backend/`）：

```powershell
uv run python -m pytest tests/test_teacher_judge_attachments.py tests/test_teacher_judge_sessions.py tests/test_rubric_template_commands.py tests/test_teacher_judge_files.py tests/test_ai_p1_regressions.py tests/test_group_features_retired.py -q
uv run ruff check app/ai/teacher_judge app/api/routes/teacher_judge_sessions.py app/api/routes/teacher_judge_files.py app/api/routes/rubric.py tests/test_teacher_judge_attachments.py tests/test_teacher_judge_sessions.py tests/test_rubric_template_commands.py tests/test_teacher_judge_files.py tests/test_ai_p1_regressions.py
uv run mypy app/ai/teacher_judge app/api/routes/teacher_judge_sessions.py app/api/routes/teacher_judge_files.py app/api/routes/rubric.py
```

Frontend focused checks（從 `frontend/`）：

```powershell
bun run test -- src/pages/course-operations/class-workspace/AiJudgePanel.test.jsx src/services/aiJudge.test.js src/pages/course-operations/class-workspace/rubricAnalysisAutosave.test.js
bun run build
```

靜態收斂檢查（repository root）：

```powershell
rg -n "analyze_rubric|ANALYZE_SYSTEM_PROMPT|uploadFile|pendingConflictFile|/rubric/upload|/rubric/chat" backend frontend vllm-service
git diff --check
```

最後需做 authenticated browser 驗收：附件-only、普通聊天、混合 Ready、部分 Apply、
revision conflict、重新整理、歷史 uploaded session。這些 focused tests 與 build 不等於
真實 vLLM 語意或已登入瀏覽器 E2E；至少要再用一個實際模型完成「文件三條需求拆分」smoke。
本次不需啟動 PVE／SSH／學生 VM，因為腳本與執行生產線沒有修改。

## 9. 完成判準

只有同時符合以下條件才算完成收斂：

1. Teacher Judge UI 只有 session chat 能接收新文件與需求。
2. 上傳文件不會直接建立、覆蓋或切換正式檢查表。
3. 普通聊天、文件拆項、文字需求都走同一個 `chat_with_rubric()` 契約。
4. Ready Proposal 仍逐項可選且未 Apply 不落入正式 `analysis_json`。
5. Direct upload/analyze prompt、legacy chat、死 frontend state、孤立 integration CLI case 與
   compatibility shim 已刪除，沒有以新 wrapper 取代舊 wrapper。
6. 歷史 uploaded source 與舊 message 可讀，無資料 migration、無資料刪除。
7. 腳本生成、核准、PVE/SSH revalidation、執行結果與 summary 邊界維持原行為。
