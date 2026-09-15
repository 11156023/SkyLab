# Teacher Judge「儲存並製作」重新核對結果進 Chat 與延續記憶計畫

> 產生日期：2026-09-15。性質：現況追查與實作計畫，尚未修改 production code。
> 基準 checkout：`018e4267`（`main`）。本計畫只處理「儲存並製作」的重新核對、
> 缺少資訊、處理失敗與 Chat 延續；不改動學生機器執行、安全閘或評分語意。

## 0. 結論先行

目前「儲存並製作」已經依序做：儲存檢查表 → AI 全表重新核對 → 套用候選結果 →
建立腳本。但負面結果主要停留在頁面橫幅或 toast，沒有形成一筆**同時可顯示、可保存、
可被下一輪 Chat 使用**的對話結果。

根因不是缺少新的聊天系統，而是同一個結果在三個位置被切斷：

1. 重新核對使用 `is_refine=true`，後端把 user 與 assistant 訊息都標成
   `metadata_json.ui_hidden=true`。
2. `handleSaveAndCreate()` 直接呼叫 `sendSessionMessage()`，沒有把回傳的 assistant message
   加進目前 `messages`；缺少資訊又由前端 `getScriptCreationBlocker()` 轉成橫幅／toast。
3. 腳本建立的 422、上游錯誤與 `review_failed` 沒有寫成可進 Chat history 的訊息；既有
   `system_notice` 也被 `bounded_history()` 排除，AI 下一輪看不到。

建議採用的最短路徑是：

- 保留內部重新核對 prompt 隱藏，但將**核對結果 assistant message 改為可見的普通 chat**。
- 後端以既有 `TeacherJudgeSessionMessage.metadata_json` 保存結構化
  `conversation_focus`；不新增資料表、欄位或 Migration。
- 缺少資訊、無法安全取證、分析失敗與腳本審查失敗，都由後端同一組 formatter 產生
  自然繁體中文與 reason code；前端不再另外猜一套原因。
- 下一輪 `bounded_history()` 讀取同一筆可見訊息與結構化焦點，讓導師可以直接回答
  「第二項用 8080 Port」之類的補充，不需重新描述整份檢查表。
- 「儲存並製作」仍是唯一腳本建立入口；Proposal Apply、`analysis_revision`、command
  validation、policy／quality／coverage／AI review 與 approval 邊界全部保留。

## 1. 本次目標與非目標

### 1-1 目標

1. 點下「儲存並製作」後，重新核對若發現缺少資訊，Chat 出現逐項說明。
2. 同一筆說明保存到 session messages；重整頁面後仍存在。
3. 導師直接在 Chat 補充時，AI 能取得前一筆缺口、項目身分、來源與 revision context。
4. AI 格式錯誤、逾時、驗證失敗、腳本安全／覆蓋／AI 複核失敗也進 Chat，但明確標示為
   系統處理階段失敗，不把責任寫成「導師缺少資訊」。
5. 頁面橫幅、toast 與 Chat 不產生互相矛盾的文案。
6. 保留目前檢查表與失敗 artifact，任何負面結果都不偷偷套用不完整 AI 輸出。

### 1-2 非目標

- 不重新加入「在 Chat 內要求製作腳本」的 Tool Call 或第二個腳本入口。
- 不新增 Requirement／Workflow 資料表、持久化 proposal、背景 job 或 workflow state machine。
- 不改 `auto + ai`、`auto + teacher`、`partial`、`manual` 的現行安全語意。
- 不讓 Chat 自動存取學生 VM、SSH、PVE 或實際作業答案。
- 不放寬 `argv`、no-shell、timeout、catalog canonicalization、script policy、quality、coverage、
  AI reviewer、approved-before-run 等閘門。
- 不在本次解決長時間腳本生成的背景恢復／跨頁續跑；仍維持目前七分鐘前端 request budget。

## 2. 現行實際資料流

### 2-1 「儲存並製作」前端鏈

`frontend/src/pages/course-operations/class-workspace/AiJudgePanel.jsx:1655-1757`：

```text
handleSaveAndCreate()
  1. autosave.flush()
  2. 讀 analysisRevisionsRef[sourceFileId]
  3. sendSessionMessage(RUBRIC_POLISH_PROMPT, isRefine=true)
  4. buildProposalDiff(current items, response.rubric_proposal)
  5. applyProposalOperations() 建 candidateAnalysis
  6. getScriptCreationBlocker(candidateAnalysis)
     ├─ 有 blocker：只 setScriptGenerationNotice + toast，停止
     └─ 無 blocker：applyAnalysis() 正式保存
  7. createSessionScript(savedRevision)
     ├─ approved：顯示完成
     ├─ review_failed：顯示錯誤橫幅／toast
     └─ request error：catch 顯示錯誤橫幅／toast
```

這段有兩個 source of truth：後端 AI 回覆一份、前端 blocker 又重建一份。Chat 沒有接收
`handleSaveAndCreate()` 的 response，也沒有在 script request 後重新載入 messages。

### 2-2 重新核對訊息鏈

`backend/app/api/routes/teacher_judge_sessions.py:451-624`：

```text
POST /teaching-classes/{class}/judge/sessions/{session}/messages
  -> 驗 class/session/active/source/analysis_revision
  -> 先保存 user message
       is_refine=true => ui_hidden=true
  -> chat_with_rubric(... is_refine=true)
       require_rubric=true
       ready_only=false
       回傳 server-validated proposal operations
  -> 保存 assistant message
       is_refine=true => ui_hidden=true
  -> source/revision 再驗
  -> response: user_message + assistant_message + rubric_proposal + base_revision
```

`frontend/src/services/aiJudge.js:41-47` 的 `shouldDisplayChatMessage()` 會排除
`ui_hidden=true`。因此兩筆資料雖然進 DB，導師看不到；下一次普通訊息進來後，
`bounded_history()` 也會刪掉較舊 hidden 訊息，無法當成穩定記憶。

### 2-3 一般 Chat 與附件逐項分析已有的能力

目前一般 Chat 已有正確的部分基礎：

- `chat_with_rubric()` 回傳 `TeacherJudgeChatResult.conversation_focus`。
- `create_message()` 把 focus 加上 `source_file_id` 後寫入 assistant `metadata_json`。
- `bounded_history()` 會把同來源最新 unresolved focus 以資料區塊注入下一輪。
- 附件 itemwise 已將 `ready / teacher_review / needs_information / unsupported /
  analysis_error` 存入 `metadata_json.item_results`，且回覆會逐列說明。

尚缺的是：附件 `item_results` 沒有轉成 `conversation_focus`；全表重新核對也沒有以
server-derived blockers 形成完整 focus。因此「畫面有逐項狀態」不等於「下一輪模型有
相同結構化記憶」。

### 2-4 腳本建立鏈

`POST .../sessions/{session_id}/scripts` 在
`backend/app/api/routes/teacher_judge_sessions.py:627-660`：

```text
create_session_script()
  -> 驗 class/session/active/source/analysis_revision/nonempty items
  -> create_artifact()
       -> ensure_script_generation_supported()
       -> build_reviewed_script()
            generation -> policy/quality -> coverage -> AI review -> bounded retry
       -> 保存 approved 或 review_failed artifact
```

`automation_support.py:101-193` 已能回傳逐項 blocker：`item_id`、`title`、`status`、
`missing_information`、`reason_code`。但 route 沒有把這份權威資料保存到 Chat。

`review_failed` artifact 也已有 `policy_check_result_json`、`ai_review_result_json`、
`review_attempts`、`retry_summary` 等資料；前端目前只顯示固定錯誤句，下一輪 Chat 不知道
失敗落在 generation、policy／quality、coverage 或 AI review。

## 3. 根因與需要修正的語意

| 現象 | 真正原因 | 不應採用的修法 | 本計畫修法 |
|---|---|---|---|
| 重新核對缺資料只在橫幅 | assistant 被 `ui_hidden`，frontend 未 append response | 前端自行新增一筆假 assistant message | 顯示並保存後端 assistant message |
| 下一輪 Chat 不記得 | hidden／`system_notice` 不進 `bounded_history`，itemwise 只有 `item_results` | 把 toast 文字塞進 prompt | 保存同源 `conversation_focus` |
| 422 只有錯誤 | backend blocker 沒有轉成 session message | 前端解析中文 error string | 後端以 `detail.items` 產生可讀 message + metadata |
| `review_failed` 只說重試 | artifact failure evidence 沒投影到對話 | 將完整 script／stack trace 放進 Chat | 只投影安全的階段、摘要、artifact id |
| 失敗被說成缺資訊 | 所有負面狀態共用「再試／補資料」文案 | 要導師猜 command／JSON | 分開 needs-information 與 processing-failure |
| 舊缺口可能重新出現 | `bounded_history()` 遇最新 resolved focus 仍繼續找更舊 unresolved focus | 清除整段 Chat | 同來源最新 focus 為權威；resolved 就停止回溯 |

## 4. 方案比較與選擇

### 方案 A：只在前端把錯誤 append 到 Chat

- 優點：修改最快。
- 缺點：重整即消失；後端歷史、摘要與下一輪 AI 都看不到；也可能把 client timeout 誤寫成
  server 已失敗。
- 判定：不符合「Chat 同時取得同樣記憶」，排除。

### 方案 B：重用 session message + `metadata_json`（建議）

- 將後端工作結果保存成 `message_type=chat` 的 assistant message。
- 內部 prompt 仍 `ui_hidden=true`；對外結果不 hidden。
- 缺口沿用 `conversation_focus`；詳細逐項畫面沿用 `item_results`。
- script route 用既有 `TeacherJudgeSessionMessage` 保存 terminal outcome。
- frontend 成功時 append response，script terminal outcome 後 reload messages。

優點是沒有 schema／migration／新 API，且 reload、history、summary 與來源隔離都能重用。
代價是一個 script request 完成後需要一次 messages refresh，並需補 route-level formatter。

### 方案 C：新增 persisted workflow／attempt 資料表

可精準管理每次長任務與重試，但目前沒有跨頁恢復、取消、排程或 audit 查詢的明確需求；
會同時增加 migration、狀態機、API 與清理策略。判定：本次過度工程化，不採用。

## 5. 目標行為矩陣

| 階段 | 類型 | Chat 顯示 | Chat 記憶 | 是否可製作腳本 |
|---|---|---|---|---|
| 重新核對 | 全部 Ready／teacher review | 說明已核對並繼續製作 | 最新 focus 標 resolved，不回灌舊缺口 | 是 |
| 重新核對 | 缺少資訊 | 逐項列名稱與可直接回答的缺口 | `needs_information` + item id/title/gaps | 否 |
| 重新核對 | 無法安全取證／manual | 說明目前限制與可選調整方向 | `unsupported`，不得偽裝成缺資料 | 否 |
| 重新核對 | AI timeout／格式／驗證失敗 | 說明失敗階段、檢查表已保留、腳本未開始 | `analysis_error`，無 teacher gap | 否，可重試 |
| 保存 candidate | revision conflict／保存失敗 | 說明沒有覆蓋新版檢查表 | scope/revision context；不得沿用舊 proposal | 否 |
| 腳本前置閘 | `teacher_judge_script_not_ready` | 使用 backend blocker 逐項說明 | 同一 blockers 轉 focus | 否 |
| 腳本審查 | `review_failed` | 說明未通過的階段、已保留 artifact、未開放執行 | `analysis_error` + artifact id + safe stage | 否，可查詳情／重試 |
| 腳本完成 | `approved` | 簡短完成訊息；腳本詳情仍在腳本區 | resolved outcome，壓過同 revision 舊失敗 | 是 |
| 網路／DB 完全不可用 | 無法確認 server 是否收到 | 保留頁面橫幅，明說狀態待確認 | 無法保證寫入；恢復連線後 refresh | 不自動重試 |

其中「導師檢查」指 `auto + teacher`：腳本能收集證據，只是結果由導師判讀，不應被列成
缺少資訊。只有 `partial`、缺參數、缺成功條件等才是 teacher-answerable gap。

## 6. 沿用的 metadata 契約

不新增 public Pydantic schema 或 DB 欄位；沿用 `metadata_json` 內兩個現有概念：

```json
{
  "status": "needs_information",
  "stage": "reanalysis",
  "source_file_id": "...",
  "analysis_revision": 12,
  "item_results": [
    {
      "item_id": "service-port",
      "title": "確認 API 服務",
      "status": "needs_information",
      "missing_information": ["服務名稱", "實際 Port"],
      "reason_code": "automatic_detection_information_missing"
    }
  ],
  "conversation_focus": {
    "turn_kind": "follow_up",
    "source_file_id": "...",
    "analysis_revision": 12,
    "requirements": [
      {
        "focus_key": "service-port",
        "target_item_id": "service-port",
        "status": "needs_information",
        "known_information": ["要確認 API 服務可用"],
        "missing_information": ["服務名稱", "實際 Port"],
        "reason_code": "automatic_detection_information_missing"
      }
    ]
  }
}
```

規則：

1. `content` 是導師可讀文字；metadata 是 history 與 UI 的穩定資料，不從 content 反解析。
2. `item_results` 保留逐項顯示；`conversation_focus` 只保留下一輪需要延續的最小事實。
3. `ready`、`teacher_review`、`none` 視為 resolved；`needs_information`、`unsupported`、
   `analysis_error` 才注入下一輪。
4. `reason_code` 供程式判斷，不直接顯示 catalog key、JSON 欄位或 exception class 給導師。
5. 每筆都帶 `source_file_id` 與 `analysis_revision`；來源切換或 revision 已過期時不得注入。
6. 字串、項目數與總 JSON 長度必須有上限；不保存 chain-of-thought、完整 prompt、stack trace、
   script body、stdout/stderr、credential 或未遮蔽的模型原文。

## 7. 後端修改計畫

### Phase 1：建立唯一的教師可讀結果 formatter

位置：優先放在 `backend/app/ai/teacher_judge/session_service.py`，或建立一個單一、聚焦的
`workflow_message.py`；不要把它做成通用 workflow framework。

提供三個小 helper：

1. `conversation_focus_from_item_results(...)`
   - 將 attachment／reanalysis 的 unresolved rows 轉成既有 focus shape。
   - `teacher_review` 視為可製作，不放入 unresolved。
2. `script_blocker_message(blockers, ...)`
   - 從 `AutomationSupportBlocker` 組出自然中文與相同 metadata。
3. `script_review_message(artifact, ...)`
   - 只抽取安全的 stage／issues 摘要／artifact id；不輸出 script 或原始模型內容。

同一 helper 的 `content` 同時供 Chat 與頁面 notice 的語意來源，避免兩套文案。

### Phase 2：修正 `create_message()` 的 refine 可見性與記憶

修改 `backend/app/api/routes/teacher_judge_sessions.py:create_message()`：

1. `is_refine` 的 **user internal prompt** 繼續 `ui_hidden=true`。
2. `is_refine` 的 **assistant result** 不再設 `ui_hidden`；仍是 `message_type=chat`。
3. 對 reanalysis 結果以目前 rubric + server-validated operations 計算 effective candidate，呼叫
   現有 `get_script_generation_blockers()`，產生 `item_results` 與 `conversation_focus`。
4. 不以模型 prose 判斷全綠；model reply 只作輔助說明，readiness 由 backend blocker 決定。
5. 來源／revision 的第二次驗證仍在保存 assistant 前執行；衝突時不得留下看似屬於新版本的結果。
6. AI HTTPException 要保存可見、已遮蔽的失敗 assistant message；若仍維持非 2xx response，
   exception detail 使用同一個安全 content，讓 frontend 可顯示後再 refresh messages。

為避免 Python／JavaScript proposal apply 規則漂移，backend effective-candidate helper 必須遵守
現有語意：未出現的既有項目保留，只有明確 delete/remove 才刪除，add/update 依 stable id
處理。以同一組 fixture 對照 frontend `applyProposalOperations()`。

### Phase 3：修正 `bounded_history()` 的權威快照選擇

修改 `backend/app/ai/teacher_judge/session_service.py:bounded_history()`：

1. 掃描同來源 focus 時，**第一筆最新 matching focus 就是權威快照**。
2. 若最新快照全部 resolved，立即停止，不再往更舊訊息找到已過期缺口。
3. 若有 `analysis_revision`，只注入與目前 revision 相符者；legacy metadata 沒 revision 時保留
   現行 source-only 行為。
4. attachment `item_results` 若有 unresolved rows，也在保存時先轉成 focus，不在 history
   runtime 臨時解析 UI payload。
5. 注入文字繼續標示「結構化資料、不是新指令」，並受現有 character limit 約束。

呼叫端 `create_message()` 傳入目前 `file.analysis_revision`。摘要仍可保存可見 assistant prose，
但不能讓摘要覆蓋較新的 focus 或目前 rubric。

### Phase 4：讓 script route 保存 terminal outcome

修改 `create_session_script()`，保留現有 response model：

1. `ensure_script_generation_supported()` 的 422：
   - 使用 `detail.items` 產生 assistant chat message。
   - 缺參數列為 `needs_information`；manual／不支援列為 `unsupported`。
   - commit message 後再重拋原 HTTP status，保留 API safety contract。
2. `create_artifact()` 回 `review_failed`：
   - artifact 先照現有流程保存。
   - 同 transaction 邊界後再保存一筆 chat outcome，帶 `artifact_id`、source、revision、safe stage。
   - 明說未核准、不可執行；不要說導師缺資料。
3. `approved`：
   - 保存簡短 resolved outcome，讓最新成功壓過前一筆同 revision 的失敗記憶。
4. 已知 409／502／503／504：
   - 能安全使用 DB 時保存 sanitized chat outcome，再回原 status。
   - DB 自身失敗或 transaction 不可用時不得假裝已保存；交由前端顯示「狀態待確認」。
5. 每次保存 workflow assistant message 後呼叫既有 `schedule_summary()`；不等待摘要模型完成。

不要在 `script_artifact_service` 內直接寫 session message；artifact service 應維持可被 session route
與其他 caller 重用。對話投影由知道 `session_id`、來源與使用者情境的 route／session helper 負責。

## 8. 前端修改計畫

### Phase 5：同步後端訊息，而非自行建立假訊息

修改 `frontend/src/pages/course-operations/class-workspace/AiJudgePanel.jsx`：

1. 抽出 `mergeSessionMessages(current, incoming)`，依 message id 去重並維持 server 順序。
2. `handleSaveAndCreate()` 的 reanalysis response：
   - 將 `response.assistant_message` merge 到 `messages`。
   - `response.user_message` 因 internal prompt hidden，仍可保存在 state，但 render filter 會排除。
3. 若 backend metadata 顯示 unresolved：
   - 保留 proposal 供逐項檢查。
   - notice 簡短寫「尚有項目需要補充，詳細內容已列在 AI 聊天室」。
   - 不再由 frontend 自行組出另一套「請確認問題項目」原因。
4. `createSessionScript()` 成功、`review_failed` 或 catch 後都呼叫既有
   `AiJudgeService.listSessionMessages()`；server 已保存的 terminal outcome 會立即出現在 Chat。
5. refresh 失敗時保留橫幅／toast，但文案改為「無法確認 Chat 紀錄是否已同步」，不得 append
   一筆會被誤認為 server truth 的永久假訊息。
6. `ScriptGenerationNotice` 保留作跨欄位流程狀態；Chat 保存可追問內容，兩者責任不同。

`frontend/src/services/aiJudge.js` 不需新增 endpoint。`RUBRIC_POLISH_PROMPT`／
`RUBRIC_REASSESS_PROMPT` 仍由 `shouldDisplayChatMessage()` 隱藏；只有後端 assistant result 可見。

## 9. 目標端到端流程

### 9-1 缺少資訊

```text
導師按「儲存並製作」
  -> flush + revision
  -> POST message(is_refine=true)
  -> backend AI reanalysis + canonical blocker check
  -> DB: hidden user prompt
  -> DB: visible assistant chat
       content: 第 2 項缺 Port、服務名稱
       metadata: item_results + conversation_focus + source/revision
  -> frontend 顯示相同 assistant + proposal + 簡短 notice
  -> 不保存 blocked candidate、不呼叫 script generation

導師在 Chat 回「服務叫 api，Port 8080」
  -> bounded_history 取得 visible reply + structured focus
  -> chat_with_rubric 只補該需求，產生 reviewable proposal
  -> 導師 Apply
  -> 再按「儲存並製作」
```

### 9-2 處理失敗

```text
AI reanalysis / script generation 失敗
  -> backend 分類 stage + reason_code
  -> 保留目前 rubric；若 artifact 已建立則保留 review_failed artifact
  -> DB: visible assistant chat（sanitized，不稱作教師缺資料）
  -> API 保留原 status / artifact response
  -> frontend refresh messages + 顯示 notice

導師追問「剛才是哪一階段失敗？」
  -> bounded_history 取得該 assistant message/context
  -> Chat 可回答目前已知階段與安全下一步
  -> 不猜 stack trace、學生資料或成功答案
```

## 10. 教師可讀文案準則

### 缺少資訊範例

> 重新核對後，「啟動 API 服務」還缺少服務名稱與實際 Port。請直接回覆例如
> 「服務名稱是 api，使用 8080」，我會沿用這個項目整理下一份提案。目前檢查表尚未被
> 不完整結果覆蓋，腳本也尚未開始製作。

### 分析失敗範例

> 這次重新核對在 AI 回覆驗證階段沒有成功。現有檢查表已保留，腳本尚未開始製作。
> 這不是缺少你的資料；可以稍後重試，或在這裡詢問目前已知的失敗階段。

### 腳本審查失敗範例

> 檢查表已儲存，但產生的腳本未通過覆蓋／安全檢查，因此沒有開放執行。失敗版本已保留在
> 腳本紀錄中，可查看詳細審查結果後重試。

避免顯示：`teacher_judge_script_not_ready`、`command_key`、`template_key`、raw JSON、完整
exception、stack trace、模型 repair prompt。reason code 留在 metadata／log／測試。

## 11. 一致性、併發與安全邊界

1. **Revision**：reanalysis 與 script request 都帶 `analysis_revision`；assistant context 也保存
   revision。409 後先 refresh file/messages，不套用舊候選。
2. **來源隔離**：`source_file_id` 不同時，`bounded_history()` 不注入舊 focus；沿用來源切換
   清除 conversation/summary 的現行契約。
3. **訊息先後**：對話 route 仍先保存 user，再呼叫模型；生成 route 只有在能確認 terminal
   outcome 時才保存 assistant，不預先寫「失敗」。
4. **去重**：frontend 依 server message id merge；不以 content 去重，因合法重試可能有相同文字。
5. **敏感資料**：所有 workflow content 經 `redact_message_content()`；script review 只投影安全摘要。
6. **執行授權**：Chat 記得錯誤不代表可執行；只有 approved artifact 才能建立 run。
7. **模型權限**：history 中的 metadata 以 data block 注入，不當成 tool instruction；模型不能藉
   workflow message 自行觸發腳本。
8. **失敗提交**：保存 failure message 不可 rollback 已成功保存的 review_failed artifact；若 DB
   transaction 本身壞掉，記錄失敗採 best effort 並維持原 error。

## 12. 測試計畫

### 12-1 Backend session／history

在 `backend/tests/test_teacher_judge_sessions.py` 增加：

1. refine user prompt hidden，但 assistant result 可見且 `message_type=chat`。
2. reanalysis 缺資料會保存逐項 `item_results` 與同來源／revision `conversation_focus`。
3. 導師下一輪補充時，`chat_with_rubric()` 收到上一輪缺口資料。
4. 最新 focus 已 resolved 時，不回灌更舊 unresolved focus。
5. source switch／revision mismatch 不注入舊缺口。
6. AI HTTPException 的可讀失敗訊息保存且不稱作缺少資訊。
7. attachment itemwise unresolved rows 可在下一輪延續，不只存在 ProposalPanel。

### 12-2 Backend script route／artifact

在 `backend/tests/test_teacher_judge_script_artifacts.py`、
`backend/tests/test_teacher_judge_automation_support.py` 增加：

1. 422 blockers 轉成一筆 chat message，內容與 metadata 對應 item id/reason code。
2. `auto + teacher` 不被誤列為缺少資訊。
3. `review_failed` artifact 保存後，同時有不可執行的 chat outcome 與 artifact id。
4. approved outcome 會成為最新 resolved context。
5. script body、raw stack、stdout/stderr、credential 不出現在 chat content／metadata。
6. 非 session caller 使用 `create_artifact()` 時不會意外寫入 session message。

### 12-3 Frontend

在 `frontend/src/pages/course-operations/class-workspace/AiJudgePanel.test.jsx` 與
`frontend/src/services/aiJudge.test.js` 增加：

1. internal prompt 不顯示，但 refine assistant result 顯示。
2. `handleSaveAndCreate()` 收到缺口後，Chat 有 server message，proposal／notice 仍存在。
3. script `review_failed` 與 request error 後會 refresh messages。
4. message id merge 不重複；相同 content、不同 id 的合法重試仍保留。
5. refresh 失敗只顯示未同步提示，不偽造已保存 Chat message。
6. 成功 flow 仍呼叫 `onScriptCreated`，且按鈕 busy／aria 狀態不回歸。

目前 `AiJudgePanel.test.jsx` 多為 static markup／pure helper tests；這次至少要補一個 mounted
interaction regression，實際點擊「儲存並製作」並 mock 兩段 API，否則抓不到「response 已
保存但 state 沒 append」這類 orchestration bug。

### 12-4 建議執行命令

```powershell
cd backend
uv run python -m pytest tests/test_teacher_judge_sessions.py tests/test_teacher_judge_automation_support.py tests/test_teacher_judge_script_artifacts.py tests/test_rubric_template_commands.py -q
uv run ruff check app/ai/teacher_judge app/api/routes/teacher_judge_sessions.py tests/test_teacher_judge_sessions.py tests/test_teacher_judge_automation_support.py tests/test_teacher_judge_script_artifacts.py tests/test_rubric_template_commands.py
uv run mypy app/ai/teacher_judge app/api/routes/teacher_judge_sessions.py

cd ../frontend
bun run test -- src/pages/course-operations/class-workspace/AiJudgePanel.test.jsx src/services/aiJudge.test.js
bun run build

cd ..
git diff --check
```

若 backend pytest 觸發非測試 DB guard，立即停止，不設定 `PYTEST_ALLOW_NON_TEST_DB=1`。

## 13. 實作順序與可回退切片

1. **Backend history correctness**：先修最新 focus 權威／revision scope，加 focused tests。
2. **Refine result projection**：internal user hidden、assistant visible、server-derived blockers/focus。
3. **Frontend reanalysis sync**：append/merge returned message，notice 改引用 Chat 結果。
4. **Script outcome projection**：422／review_failed／approved 保存 chat outcome。
5. **Frontend script refresh**：terminal outcome 後 reload messages，補 mounted regression。
6. **完整 focused validation**：backend tests／Ruff／mypy、frontend Vitest／build、diff check。

每一步可獨立回退；不要把 proposal persistence、background generation 或 manual safety policy
一起帶入同一變更。

## 14. 驗收條件

- [ ] 「儲存並製作」發現缺資料時，導師在 Chat 立即看到逐項缺口，不只看到錯誤橫幅。
- [ ] 重整頁面後訊息仍在。
- [ ] 導師只補充缺口即可得到針對同一項目的新提案。
- [ ] `analysis_error`／`review_failed` 清楚說明處理階段，不寫成導師缺資料。
- [ ] attachment itemwise、full reanalysis、script preflight 使用一致 status／reason semantics。
- [ ] resolved 新結果不會讓舊 unresolved focus 復活。
- [ ] source switch／revision change 不會把舊檢查表記憶帶入新來源。
- [ ] 失敗不覆寫正式 rubric、不自動 Apply、不建立可執行的未核准 artifact。
- [ ] Chat 不暴露 prompt、JSON/catalog 術語、script body、stack、credential 或原始學生輸出。
- [ ] 現有 script generation／policy／quality／coverage／AI review／approval tests 維持通過。

## 15. 風險與驗證邊界

- 只靠 mock 無法證明 live vLLM 能穩定輸出自然、完整的逐項說明；需要另做一個含
  Ready、缺資料、unsupported、故意失敗的 live fixture smoke。
- frontend tests/build 不等於 authenticated browser E2E；至少手動驗證一次：缺 Port → Chat
  補充 → Apply → 再製作 → approved/review_failed。
- 本計畫不承諾網路或 DB 完全不可用時仍能保存 Chat；只能保留本地提示並在恢復後重新同步。
- 本計畫沒有操作 PVE、VM、SSH 或實際學生資料，也不以 HTTP 200 取代腳本語意／安全驗證。

## 16. 本次文件依據

- codebase-memory graph project：`C-Users-Desktop-Campus-Cloud`，狀態 `ready`；symbol 定位後以
  checkout 實檔交叉確認。
- 現行 checkout：`018e4267`，branch `main`。
- 工作樹原有未追蹤路徑 `frontend/Campus-Cloud/`；本計畫不讀取、不修改、不納入範圍。
- 本次只新增本文件；尚未執行 production code 測試、live vLLM、瀏覽器、DB、PVE 或 SSH 驗證。
