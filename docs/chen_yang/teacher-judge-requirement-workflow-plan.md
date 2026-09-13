# Teacher Judge Requirement 對話核查流程計畫

更新日期：2026-09-11。

狀態：已由 `docs/chen_yang/2026-09-11-teacher-judge-ai-chat-convergence-plan.md` 承接並
完成入口收斂。本文件保留 Requirement 拆分、Proposal 暫存與 Apply 邊界的歷史決策；
目前文件與文字需求皆由 session AI Chat 接收，不再使用 direct rubric upload/chat。

本文件是 AI 檢查聊天室的目前實作計畫，取代舊的「AI 回傳修改後，在聊天室下方顯示
同意更新表單」呈現方式。這一版刻意維持輕量：不保存 Requirement 或草稿、不修改資料庫，
沿用目前評分表、session message、`rubric_proposal` 與 `analysis_revision` 的用法。

## 1. 本次範圍與決策

目標流程：

```text
老師在聊天室描述需求
  -> AI 主動核查需求是否足以建立自動檢查
  -> 一條需求只聚焦該條；多條需求一次拆解並分別判斷
  -> AI 在聊天回覆中指出各條 Ready／缺少資訊／不支援及具體原因
  -> Ready 的部分才形成暫存 Proposal
  -> Proposal 顯示在「檢查項目」預覽區的透明可展開框
  -> 老師可繼續聊天修正，或在 Proposal 預覽選擇同意 Apply
  -> Apply 成功後才寫入目前評分表並正式保留
```

本次採用以下限制：

- 不新增 `requirement_state_json`、`workflow_revision` 或 Requirement 資料表。
- 不建立 Requirement／草稿歷史，不做 reload 或跨分頁還原。
- `pendingProposal` 繼續只存在目前頁面的前端記憶體。
- 未 Apply 的 Proposal 在重新整理、切換 session／來源、清除對話或被新提案取代後可以消失。
- 對話內容仍由既有 session messages 保存，AI 可依歷史語意重新產生 Proposal，但不保證與
  消失前的候選逐欄、逐 ID 完全相同。
- 沿用現有 `add`、`update`、`delete` Proposal 操作；不移除正式檢查項目的刪除功能。
- 不重做腳本生成、政策檢查、品質檢查或執行流程。

## 2. 對話記憶與暫存 Proposal 的邊界

目前 `pendingProposal` 是 React state，不是資料庫狀態。這一版保留此特性：Proposal 是目前
頁面中的暫存預覽，只有 Apply 後才成為正式評分表內容。

對話記憶的用途是讓 AI 理解老師先前提過：

- 想檢查什麼。
- AI 曾指出哪些資訊不足。
- 老師後來補充了哪些資料。
- 老師要求新增、修改或刪除哪一類項目。

它不能保證恢復同一份結構化 Proposal。`bounded_history()` 傳給 AI 的主要是訊息文字，並非
前端 `pendingProposal` 的完整候選資料。因此：

- Proposal 消失後，老師可說「依剛才內容重新整理提案」。
- AI 依目前正式評分表與對話重新建立一份新 Proposal。
- 新 Proposal 必須重新顯示給老師確認，不能因老師先前曾同意另一版本就直接 Apply。
- 若歷史已被摘要、清除或超出範圍，AI 可能需要重新詢問必要資訊。

這是本次接受的取捨：不增加持久化與 migration，代價是未 Apply 草稿沒有精確恢復、跨分頁
一致性或完整歷史。

## 3. AI 核查規則

### 3.1 不再只處理「新增」指令

目前 Prompt 偏向「只有老師明確要求新增、修改或刪除評分項目才回傳 `updated_items`」。
新規則應先判斷老師是否正在描述一個檢查需求；只要有檢查目標，就主動核查完整性，不要求
老師先使用「新增」或其他固定句型。

AI 每條需求至少要確認：

1. 要檢查的對象與操作是什麼。
2. 成功或失敗的客觀條件是什麼。
3. 平台 command catalog 是否有適合的安全取證能力。
4. 執行所需的工作目錄、檔案、服務名稱、Port 或資料範圍是否完整。
5. `check_steps` 是否可以使用現有 command 與結構化 parameters 表達。

### 3.2 狀態語意

AI 對每條需求使用現有三態語意，不新增持久化狀態：

| 顯示 | 對應既有值 | 行為 |
| --- | --- | --- |
| Ready | `auto` | 可以放入本輪 Proposal |
| 缺少資訊 | `partial` | 不放入 Proposal，聊天中列出老師需要補充的具體資料 |
| 不支援自動檢測 | `manual` | 不放入 Proposal，說明主觀性、能力或安全限制 |

只有通過既有 rubric schema、command catalog 與 check-step normalization 的 Ready 內容才可
出現在 `rubric_proposal`。模型不能只宣告 Ready 而略過後端既有驗證。

## 4. 單條與多條需求

### 4.1 單一需求

老師只提出一條需求時，AI 只處理該條：

```text
老師：執行 main.py，確認輸出整數 20。

AI：這項需求目前缺少 main.py 所在工作目錄。請提供例如
    /home/student/project；成功條件「輸出整數 20」已經足夠。
```

老師補充目錄後，AI 再根據完整上下文產生 Ready Proposal，不順便修改其他項目。

### 4.2 多條需求

一則訊息包含多條需求時，AI 一次拆解、逐條回報：

```text
已拆成 3 條需求：
1. main.py 輸出 20：Ready，已放入提案。
2. Web 服務回傳 200：缺少服務 Port。
3. 報告說明是否清楚：不支援自動檢測，需要人工評閱。
```

只有第 1 條進入本輪 Proposal；第 2、3 條不會產生空白或不完整的 Proposal item。老師之後
可以自由補充第 2 條、改寫第 3 條，或要求 AI 重新核查整張評分表。

### 4.3 對話操作範圍

- 「第 2 條 Port 是 8080」：只處理對話中指向的需求。
- 「把資料庫那項改成檢查 PostgreSQL」：只提出該正式項目的 update Proposal。
- 「刪掉第 3 個檢查項目」：保留現有 delete Proposal 行為。
- 「重新核查整張檢查表」：才進行全表檢查。
- 純詢問能力、原因或做法時只回答，不產生 Proposal。

本次不新增 `target_scope` API。AI 依最新訊息、既有對話與目前 rubric context 判斷範圍；
Prompt 必須要求無法確定指向時先問一個最小澄清問題，不得猜測後修改。

## 5. Proposal 呈現與 Apply

### 5.1 取代原本同意更新表單

移除聊天室下方目前的實心 `ProposalPanel`。改在中間「檢查項目」卡片標題與正式 rubric
table 之間呈現透明、可展開的 Proposal 預覽：

```text
檢查項目（5）

┌─ AI 提案 ─────────────── Ready 2  [展開／收合]
│ □ 新增：main.py 輸出檢查
│ □ 修改：資料庫服務健康檢查
│ □ 刪除：舊版 Port 檢查
│                         [忽略] [同意套用]
└────────────────────────────────────

正式檢查項目表格
```

介面規則：

- 使用透明背景與低對比邊框，不建立另一張實心卡片，也不使用 dialog／overlay。
- 新 Proposal 出現時自動展開；收合後仍顯示 Proposal 數量。
- 保留既有逐項選取能力，讓老師只 Apply 部分 Ready 內容。
- 操作文案改為「忽略」與「同意套用」，清楚表示 Apply 才會正式保存。
- Proposal 只顯示 Ready 內容；缺少資訊與不支援原因由 AI 聊天主動說明。
- Apply 成功後，選取內容才進入正式評分表；失敗時維持目前 Proposal，不套用半成品。
- 每個 operation 都顯示新增、修改或刪除，避免老師只看到修改後文字卻不知道影響。
- 使用 `aria-expanded`／`aria-controls`；結果通知保留 `aria-live`，狀態不能只靠顏色。
- 手機版依序顯示 Proposal 預覽、正式檢查項目、AI 聊天室。

### 5.2 沿用現有 Apply 路徑

本次不新增 Proposal API。繼續使用目前前端：

1. `buildProposalDiff()` 產生 `add`／`update`／`delete` 差異。
2. 老師在預覽中選擇並按「同意套用」。
3. `applyProposalOperations()` 將選定差異套到目前分析。
4. `updateFileAnalysis()` 帶 `expected_revision` 保存並遞增 `analysis_revision`。
5. revision 不符時拒絕保存，清除過期 Proposal 並要求 AI 依目前內容重建。

`analysis_json` 仍只包含正式檢查項目；未 Apply Proposal 不寫入資料庫。

## 6. Prompt 與回傳契約

本次沿用現有 chat response：

- `reply`：AI 對本輪需求的核查摘要、Ready／缺資料／不支援結果及具體補充問題。
- `rubric_proposal`：只包含本輪 Ready 內容所形成的完整候選差異。
- `base_revision`：繼續保護 Proposal 所依據的正式評分表版本。

不新增 Requirement public schema。模型底層仍可回傳既有完整 `updated_items`，但必須遵守：

- 保留目前正式評分表中未被指定的項目。
- 只將 Ready 的新增／修改／刪除納入候選。
- 缺資料或不支援的需求不得用猜測值補成 `auto`。
- `reply` 必須逐條指出未進 Proposal 的原因，不能只說「資訊不足」。
- 多條需求中部分 Ready 時仍可回傳 Proposal，不要求整批同時 Ready。
- 一般問答沒有 Proposal 時，前端依現有暫存策略處理，不寫入任何草稿。

## 7. 與「儲存並製作」的關係

「儲存並製作」繼續是唯一腳本建立入口，不重新加入聊天室 Tool Call。

- 目前頁面仍有 `pendingProposal` 時，前端先要求老師同意套用或忽略。
- Proposal 已消失但未 Apply 時，後端無法知道該草稿曾存在；這是本次不持久化所接受的限制。
- 腳本仍只根據已 Apply 的正式 `analysis_json` 建立。
- 正式項目依舊必須全部通過既有 `auto`、完整 `check_steps`、command catalog、policy 與
  quality gate。
- `analysis_revision` 繼續防止使用舊評分表版本製作腳本。

因此這一版不承諾「任何未完成的對話需求都能在後端阻擋腳本」；只有已存在正式評分表或
目前前端 `pendingProposal` 的狀態能參與現有 blocker。

## 8. 實作順序

1. **Prompt 行為**：將「只在新增／修改指令才處理」改成主動核查檢查需求；加入單條聚焦、
   多條拆解、Ready／缺資料／不支援與最小澄清規則。
2. **Proposal 產生**：確保多條需求可只回傳 Ready 子集，未 Ready 內容只留在 `reply`，並
   保留既有 normalization 與 command catalog 驗證。
3. **前端版面**：將 `ProposalPanel` 從聊天室移到檢查項目預覽，改為透明可展開樣式，保留
   選取、add／update／delete 與既有 Apply 函式。
4. **互動收斂**：文案改為「忽略／同意套用」；聊天期間可產生新版 Proposal，舊的
   前端草稿依現有規則被取代或清除。
5. **既有門檻回歸**：確認 Apply revision conflict、評分表 autosave 與「儲存並製作」全綠
   gate 未被破壞。

預計修改範圍：

- `backend/app/ai/teacher_judge/prompt.py`
- `backend/app/ai/teacher_judge/service.py`
- `backend/tests/test_teacher_judge_sessions.py`
- `frontend/src/pages/course-operations/class-workspace/AiJudgePanel.jsx`
- `frontend/src/pages/course-operations/class-workspace/AiJudgePanel.module.scss`
- `frontend/src/pages/course-operations/class-workspace/AiJudgePanel.test.jsx`

不修改：

- `backend/app/models/teacher_judge_session.py`
- Alembic migrations
- `TeacherJudgeSession` 資料表
- `TeacherJudgeRubricAnalysis` 的持久化結構
- 既有 session message／file API 路徑

## 9. 驗收案例

1. 單一需求缺 cwd：AI 主動指出缺少的工作目錄，不產生 Proposal。
2. 老師補 cwd：AI 依對話重新核查並產生 Ready Proposal。
3. 一次輸入三條：兩條 Ready、一條缺 Port；預覽只出現兩條，聊天指出第三條缺什麼。
4. Ready／缺資料混合時不要求全部 Ready 才顯示 Proposal。
5. 純詢問只回答，不產生或 Apply Proposal。
6. 新增、修改與刪除 Proposal 都能在透明預覽中辨識。
7. 「忽略」只清除目前前端 Proposal，不修改正式評分表。
8. 「同意套用」只保存已選取項目，且必須通過 `analysis_revision` 檢查。
9. revision conflict 不覆蓋較新的正式評分表，並提示重新請 AI 建立 Proposal。
10. Proposal 顯示在檢查項目區，不再佔用 AI 聊天訊息空間。
11. 重新整理後未 Apply Proposal 可以消失；再次要求時 AI 依對話語意重建新 Proposal。
12. 對話被清除或摘要資訊不足時，AI 重新詢問必要資料，不假裝恢復舊 Proposal。
13. 現有附件、class/session 授權、autosave、command catalog 與腳本安全檢查維持不變。
14. 正式評分表全綠時，「儲存並製作」仍走原有流程。

## 10. 驗證方式與限制

實作時執行 Teacher Judge chat／session focused pytest、變更檔 Ruff／mypy、`AiJudgePanel`
Vitest、Vite build、Impeccable detector 與 `git diff --check`。

另需 authenticated browser 驗收：單條補資料、多條拆解、部分 Ready、透明 Proposal 展開、
add／update／delete、Apply revision conflict、重新整理後由對話重建。這些測試不等於真實
vLLM、PVE／SSH 或學生 VM 端到端已驗證。
