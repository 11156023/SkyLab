# Teacher Judge 上傳評分表逐項核查修正計畫

日期：2026-09-13

性質：根因分析與待實作計畫。

範圍：只修正「附件評分表一次交給模型，導致整批直接判定通過」；獨立聊天新增單一
檢查項目的既有行為維持不變。

## 1. 結論

問題不在 Proposal Apply、`analysis_revision` 或前端勾選，而在附件分析仍是單次模型任務：

```text
整份附件文字
  -> 一次 chat_with_rubric()
  -> 模型同時拆項、理解、選指令、判斷 Ready
  -> 一次回傳整批 updated_items
```

Prompt 雖寫著「逐條核查」，後端沒有先建立項目邊界，也沒有讓每條需求獨立經過已正常
運作的單項核查流程。因此模型可以把整份文件視為一個整體任務，使用相同理由讓所有列
一次通過。

修正採兩階段、單一公開入口：

```text
附件
  -> 階段 A：只拆出來源項目，不判斷 Ready、不產生指令
  -> 階段 B：每個來源項目各自呼叫同一個單項核查核心
       ├─ ready             -> 可套用 Proposal operation
       ├─ needs_information -> 黃色狀態與具體缺口
       ├─ teacher_review    -> 可執行取證、結果由導師核對
       ├─ unsupported       -> 無法安全取證
       └─ analysis_error    -> 此項分析失敗，不影響其他項目
  -> 後端依來源順序聚合一次 response
  -> 前端逐項預覽與 Apply
```

不新增上傳分析 API、資料表、Proposal 持久化或另一套 rubric schema。

## 2. 已確認的現況與根因

### 2.1 實際資料流

1. `upload_session_attachment()` 保存檔案及解析文字。
2. `create_message()` 以 `attachment_context(attachments)` 把所有附件內容組成一段文字。
3. 整段內容只傳入一次 `chat_with_rubric()`。
4. `chat_with_rubric()` 再把附件全文追加成一則 `【附件資料】` user message。
5. 同一個模型回應同時負責 `updated_items`、`proposal_status`、`conversation_focus` 與回覆。
6. `_normalize_rubric_items()`、`_proposal_changes()` 只能驗證模型已回傳的候選，無法證明
   每一個來源列真的被獨立分析。

### 2.2 現有測試缺口

`test_chat_prompt_treats_attachment_as_concrete_rubric_content` 目前只用一列附件，並斷言：

- prompt 包含「逐條核查」；
- 附件內容有送給模型；
- 模型回傳了非空 Proposal。

它沒有用多列資料驗證：項目數、來源順序、每列獨立狀態、單列失敗隔離及重複標題。
所以「Prompt 說要逐條」通過測試，不代表 runtime 真的逐條執行。

### 2.3 為何不只改 Prompt

再加強「請逐條處理」「不要全部通過」仍把拆解與判定交給同一次生成，無法形成可驗證的
程式邊界。既然聊天獨立新增單一項目已正常，最短路徑是抽出該單項核查核心，讓附件項目
逐一重用，而不是另寫第二套判定規則。

## 3. 目標契約

### 3.1 階段 A：附件只負責拆項

新增 backend internal function，例如 `extract_attachment_requirements()`。輸入是本輪附件
解析文字；輸出只包含來源資料：

```json
{
  "items": [
    {
      "source_index": 1,
      "source_label": "第 1 列",
      "title": "確認 Python 版本",
      "description": "確認學生環境中的 Python 版本",
      "evidence_hint": "python --version"
    }
  ]
}
```

限制如下：

- 不輸出 `detectable`、`judgement_mode`、`detection_method`、`check_steps` 或 Proposal。
- 表格每個資料列、編號條目或明確評分項目各形成一筆，不合併相似標題。
- 保留原始順序與 `source_index`；重複標題仍是不同項目。
- 空白列、欄位標題、分數合計與純說明文字不建立項目。
- 無法可靠拆解時回傳明確 extraction error，不把整份文件當成單一 Ready 項目。

這份拆解結果只存在本輪 request memory，不寫 DB，也不成為正式檢查表。

### 3.2 階段 B：逐項重用單項聊天核查

從 `chat_with_rubric()` 抽出共用 internal function，例如 `analyze_requirement_item()`，讓：

- 一般聊天新增一條需求：呼叫一次；
- 附件有 N 條來源項目：以 N 個互相隔離的輸入呼叫 N 次。

每次輸入只能包含一個來源項目、目前環境、command catalog 與必要的 rubric context；不得
把其他附件項目的判定結果放入同一 prompt。這可避免第一條的命令或 Ready 理由被模型
複製到後續所有項目。

每項仍走現有 `_normalize_rubric_items()`、catalog command validation、
`_proposal_changes()` 與 repair gate。獨立聊天因此不改語意，附件只是多次重用相同核心。

### 3.3 每項結果不可互相污染

每個來源項目固定產生一個結果：

| 狀態 | 條件 | Proposal |
| --- | --- | --- |
| `ready` | 指令、參數及客觀判定完整 | 可套用 `auto + ai` |
| `teacher_review` | 指令可完整執行，但沒有固定答案或門檻 | 可套用 `auto + teacher`，明示導師核對 |
| `needs_information` | 缺少會影響執行位置、對象或範圍的必要資料 | 不產生 operation，列出缺口 |
| `unsupported` | 無安全唯讀方式取得相關證據 | 不產生 operation，列出原因 |
| `analysis_error` | 該項模型輸出或修復仍失敗 | 不產生 operation，顯示稍後重試 |

某一項無效只能降級該項，不能把其他有效項目清空，也不能因整批中有 Ready 就把缺資料
項目改成 Ready。後端最後以 `source_index` 排序聚合，並核對「拆解數 = 結果數」。

### 3.4 缺資料不阻塞整批

附件分析不逐條向老師發問。`needs_information` 直接顯示在該項狀態列，繼續分析下一項；
所有綠色 Proposal 在同一輪產生。老師之後可用正常聊天補充指定項目，屆時仍走既有單項
更新流程。

「不知道正確答案」不等於指令無法執行。若可安全收集證據，建立 `auto + teacher`，而非
要求老師先提供答案或把整項判定成 manual。

### 3.5 檢測方式與腳本計畫

每個單項核查先產生結構化 `check_steps`；`detection_method` 只能由後端根據已驗證的
command label、argv、cwd、收集內容與 `judgement_mode` 產生顯示文字。模型自由撰寫的
`detection_method` 不作為腳本依據。

後續腳本生成仍使用正式 Apply 後的 `analysis_json.check_steps`。附件拆項階段不執行命令，
也不得使用假路徑、placeholder 或其他項目的參數補齊目前項目。

## 4. 聚合、效能與失敗處理

逐項模型呼叫採固定的小型 bounded concurrency，建議同時最多 2 項；結果仍按
`source_index` 排序。不要對 N 項無限制 `gather()`，也不要在同一 prompt 重新合批。

第一版保持同步 session message API，不建立 background job。實作前以 10 項 fixture 與
實際 vLLM smoke 量測總時間；若超過現有 Teacher Judge request timeout，應調整這個既有
request 的 timeout 與進度文案，不先新增 job table、輪詢 API 或永久批次狀態。

整輪共用同一個 `base_revision`：AI 分析開始前檢查一次，所有項目聚合後、保存 assistant
message 前再檢查一次。中途 rubric/source 改變時整輪回 409，不能把舊來源結果接到新版本。

## 5. 前端行為

沿用 `handleSendMessage()`、`pendingProposal`、`ProposalPanel` 與 explicit Apply：

1. 顯示「正在拆解評分表」後，再顯示「已分析 X / N 項」；若 API 仍為單一 response，
   進度只能使用不偽造百分比的階段文案。
2. 聚合完成後按附件原順序列出所有項目及狀態。
3. `ready` 與 `teacher_review` 有勾選框；Ready 預設勾選。
4. `needs_information`、`unsupported`、`analysis_error` 不可勾選，但必須顯示缺口或原因。
5. Apply 只傳選取的合法 operation；未 Apply 前不修改 `analysis_json`。
6. 重新整理可丟失待套用 Proposal，維持現有 transient contract，不新增持久化。

## 6. 實作順序

1. 先新增多列表格 fixture，寫出會重現「一次全部通過」的失敗測試。
2. 建立純拆項 prompt/schema/parser，只驗證來源項目與順序，不混入可偵測性。
3. 從 `chat_with_rubric()` 抽出單項核查核心，先證明既有聊天單項測試完全不變。
4. 加入附件 itemwise orchestrator、bounded concurrency、逐項錯誤隔離與 aggregate contract。
5. 前端改為依 aggregate statuses 顯示完整逐項結果，Proposal Apply 邊界維持不變。
6. 完成 focused tests、build、實際 vLLM 多列 smoke 與 authenticated browser 驗收。

## 7. 預計修改範圍

- `backend/app/ai/teacher_judge/prompt.py`
- `backend/app/ai/teacher_judge/service.py`
- `backend/app/ai/teacher_judge/schemas.py`（只新增 internal typed result；public response 能沿用則不改）
- `backend/app/api/routes/teacher_judge_sessions.py`
- `backend/tests/test_rubric_template_commands.py`
- `backend/tests/test_teacher_judge_sessions.py`
- `backend/tests/test_ai_p1_regressions.py`
- `frontend/src/pages/course-operations/class-workspace/AiJudgePanel.jsx`
- `frontend/src/pages/course-operations/class-workspace/AiJudgePanel.test.jsx`
- 必要的局部 SCSS 與 service tests

明確不修改：DB schema、Alembic、附件保存格式、command catalog、script executor、PVE/SSH
授權、artifact/run schema、非 Teacher Judge frontend。

## 8. 驗收矩陣

| 案例 | 必須結果 |
| --- | --- |
| 10 項全部可自動判定 | 實際進行 10 次獨立單項核查；回傳 10 項且順序一致 |
| 2 Ready、1 缺 Port | 2 筆 Proposal、1 筆黃色缺口；不追問、不把黃色改綠 |
| 第 1 項非法 command | 只有第 1 項降級；其餘有效 Proposal 保留 |
| 兩項同名 | 依 `source_index` 保留兩項，不覆蓋或去重 |
| 沒有預期答案但命令可執行 | `auto + teacher`，不是 partial/manual，也不追問 |
| 單項獨立聊天新增 | response、Proposal、Apply 與既有測試完全一致 |
| 拆項模型輸出不完整／非 JSON | 明確 extraction error；不得回傳整批 Ready |
| 分析期間 revision 改變 | 409，整批結果不保存、不 Apply |
| 只含說明與表頭的附件 | 0 個項目並說明未辨識到評分列，不建立假 Proposal |

## 9. 驗證方式

Backend（從 `backend/`）：

```powershell
uv run python -m pytest tests/test_rubric_template_commands.py tests/test_teacher_judge_sessions.py tests/test_ai_p1_regressions.py -q
uv run ruff check app/ai/teacher_judge app/api/routes/teacher_judge_sessions.py tests/test_rubric_template_commands.py tests/test_teacher_judge_sessions.py tests/test_ai_p1_regressions.py
uv run mypy app/ai/teacher_judge app/api/routes/teacher_judge_sessions.py
```

Frontend（從 `frontend/`）：

```powershell
bun run test -- src/pages/course-operations/class-workspace/AiJudgePanel.test.jsx src/services/aiJudge.test.js
bun run build
```

最後必須使用實際模型上傳至少一份含 Ready、缺資料、答案未知與不支援項目的多列表格，
確認不是只在 mock 中逐項。focused tests 與 build 不等於真實 vLLM 或 authenticated browser
E2E；未完成這兩項時必須如實標註。
