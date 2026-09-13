# Teacher Judge 運作收斂、上下文穩定與提案 Gate 修正計畫

日期：2026-09-12
性質：詳細分析與實作計畫；2026-09-12 已依本文件完成 runtime 收斂。
依據：目前工作樹的實際 route、service、prompt、session history、command catalog、前端 consumer 與測試。

實作驗證：backend focused tests 208 passed、frontend focused tests 32 passed、Ruff、targeted mypy 與 Vite build 通過；另以目前設定的 live vLLM 重現「再發提案」並確認修正後能形成提案；未執行 authenticated browser 或 PVE/SSH E2E。

2026-09-13 補充修正：完整需求若被模型直接標成 `manual` 且沒有 `check_steps`，過去不會被辨識成能力選擇錯誤；現在只要後端已提供 `system.run_command`，就會先進行一次結構化 capability review，要求模型重新選用安全唯讀命令。這不依賴自然語言關鍵字，也不放寬 command、argv、timeout 或 success criteria 驗證；真正無法安全取得證據的需求仍可維持 `manual`。

同日深入修正：proposal repair 改為依失敗類型分階段且去重。模型若先產生無效 `auto` 步驟、第一次修正又退回 `manual`，後端會再執行一次不同目的的 capability correction；相同失敗類型不會重複呼叫，總修正次數最多兩次。Capability correction 使用獨立低溫度請求，只攜帶最近需求、被拒候選、可用 command 與驗證事實，不再把模型送回完整長提示中延續原本的 `manual` 判斷。

正確編碼的 live 原始輸出確認模型已理解「再發提案」、保留 Python 版本焦點，並產生 `system.run_command` 與 `python3 --version`；真正被 gate 淘汰的原因是模型回傳 JSON boolean `detectable: true`，而後端只接受字串 `auto | partial | manual`。Normalization 現在明確將 `true` 映射為 `auto`、`false` 映射為 `manual`，再繼續既有 command、argv 與 proposal validation；不依自然語言推測檢查內容。

## 1. 結論先行

目前的問題不是單純「模型上下文太短」，也不是 Teacher Judge 完全沒有 CPU、記憶體或
磁碟資訊的取證能力。根因是三件事疊在一起：

1. `service.py` 同時承擔自然語言辨識、模型輸出修補、command 正規化、Proposal gate、
   教師回覆改寫與多輪補問，已累積大量中文片語、regex 與個案 recovery。修一個案例時，
   現在的慣性是再加一組關鍵字與 fallback；檔案變小與泛化能力都不會因此改善。
2. 每輪 assistant 最後只保存給老師看的 `content` 和 `metrics`。模型原先產生的
   `proposal_status`、已確認條件、未解缺口與被後端驗證淘汰的原因都沒有成為下一輪可用的
   結構化上下文。第二、三輪只能重新解讀散文，並由 `_is_requirement_follow_up()` 透過
   問號及固定句型猜測老師是否正在回答上一題。
3. Proposal 是否出現，實際取決於結構化 `rubric_proposal` 是否通過整條後端與前端 gate。
   模型只要用合法 JSON 回覆「目前無法取得」並給出 `proposal_status=none`、
   `updated_items=null`，目前程式就不會進 Proposal repair。事後改寫一句比較友善的說法，
   仍然不會產生提案。

建議採用「**單一結構化回合結果 + 後端衍生事實 + 精簡對話焦點**」方案：

- 模型負責理解老師意圖、規劃候選取證方式及把已驗證事實說成自然語言。
- 後端只負責 JSON/schema、command/parameter、安全、revision 與差異驗證；不得再用中文
  關鍵字代替語意理解。
- 後端從驗證結果衍生 `ready / needs_information / unsupported / system_error`，不再信任
  模型散文或 `proposal_status` 自我宣告。
- 只把「尚未解決的需求焦點」放進既有 message `metadata_json`，供下一輪使用；不保存未
  Apply Proposal、不新增 Requirement 資料表，session chat response 契約維持不變。
- `service.py` 回到編排角色，特例 parser/recovery 在對應情境回歸通過後刪除，不再移位保留。

### 1.1 最終產品定位：導師檢查助理，不是評分系統

本計畫後續所有實作與文案以「協助導師檢查」為唯一定位：

- 「評分表／rubric」對老師與模型的敘述一律改成「檢查表」。
- 「評分項目／評分標準」一律改成「檢查項目／檢查條件」。
- 「評分、評量、打分、心得」一律改成「檢查、核對、結果說明、證據摘要」。
- Teacher Judge 可保留為內部 feature/module 名稱；對外角色名稱改成「AI 檢查助理」或
  「導師檢查助理」。
- `rubric_*`、`TeacherJudgeRubric*` 等既有資料庫/API/Python 識別字可暫時保留作相容性實作
  名稱，但不得再出現在教師可見文字或自然語言 prompt；內部 model tool
  `get_current_rubric` 改名為 `get_current_checklist`，由 backend 映射既有資料來源。

兩種結果定位必須清楚分開：

| 檢查條件 | 內部模式 | 對老師的意義 |
| --- | --- | --- |
| 有明確、唯一且可直接比較的答案（絕對答案） | `judgement_mode=ai` | 系統依條件自動核對，輸出通過／未通過與證據 |
| 沒有唯一答案，或老師明確表示自己查看 | `judgement_mode=teacher` | 系統只收集並整理證據，標示「待導師核查」，不得替老師判斷 |
| 尚缺會改變檢查位置、對象、範圍或明確答案的資訊 | `detectable=partial` | 在同一對話自然詢問真正缺口 |
| 平台無法安全取得所需證據 | `detectable=manual` | 說明無法執行及安全邊界，不假裝已有結果 |

`judgement_mode` 在此只表示「誰負責核對」，不是計分模式。不能因 `ai` 就產生分數，也不能
因 `teacher` 沒有絕對答案就攔下可安全收集證據的 Proposal。

## 2. 本次查到的真實資料流

```text
AiJudgePanel.handleSendMessage()
  -> AiJudgeService.sendSessionMessage()
  -> POST /api/v1/teaching-classes/{class_id}/judge/sessions/{session_id}/messages
  -> teacher_judge_sessions.create_message()
       ├─ 檢查 class/session/source/analysis_revision
       ├─ 保存本輪 user message 與附件關聯
       ├─ bounded_history(): 最近 20 則、最多 24,000 字，加上既有摘要
       ├─ 取得目前 environment 與 enabled command catalog
       └─ chat_with_rubric()
            ├─ 組合大型 system prompt、歷史、附件與 catalog
            ├─ vLLM JSON 回覆
            ├─ _normalize_rubric_items()
            ├─ _proposal_changes()
            ├─ 必要時最多兩輪 tool/read/repair/audit
             └─ 特定結果再做 reply rewrite 或固定 fallback（目前問題之一）
  -> response.rubric_proposal
  -> buildProposalDiff()
  -> 非空才寫入前端 pendingProposal
  -> 老師選取並 Apply
  -> updateFileAnalysis(expected_revision)
  -> 正式 analysis_json
```

重要 ownership 不變：

| 資料 | Source of truth | 本計畫是否改變 |
| --- | --- | --- |
| 對話、附件 | session messages / attachments | 保留；只在既有 metadata 加入精簡焦點 |
| 未 Apply Proposal | 當前頁面的 `pendingProposal` | 不改，不持久化 Proposal |
| 正式檢查表 | file `analysis_json` + `analysis_revision` | 不改 |
| 可執行 command | 專用 command catalog（優先）+ `system.run_command` + 後端安全 policy | 不把 catalog 當白名單；安全邊界不放寬 |
| 腳本建立與執行 | Apply 後的 artifact / approval / run 流程 | 不繞過 |

## 3. `service.py` 過度硬編碼的具體位置

目前 `backend/app/ai/teacher_judge/service.py` 已達 1,910 行。值得收斂的不是所有判斷，
而是「把自然語言個案寫成後端真相」的部分。

### 3.1 應刪除或改由結構化結果取代

1. **特定語句 parser**
   - `_CONFIG_ASSIGNMENT_PATTERN`
   - `_POSIX_TEXT_FILE_PATTERN`
   - `_FILE_CONTENT_CONTAINS_PATTERN`
   - `_PYTHON_VERSION_INTENT_PATTERN`
   - `_EXPLICIT_PYTHON_VERSION_PATTERN`
   - `_PYTHON_PACKAGE_STATUS_PATTERN`
   - `_FILE_READ_COMMAND_ALIASES`
   - `_explicit_text_file_item()`
   - `_recover_known_catalog_steps()`

   這些 helper 能救特定範例，但「Python 版本」「Python 套件」「POSIX 文字檔」會比其他需求
   更容易成功。CPU、記憶體、磁碟、程序、HTTP、Windows、資料庫或日後的新需求沒有相同
   recovery，就形成不可預期的能力差異。

2. **用散文猜狀態**
   - `_reply_claims_ready_proposal()` 用「Ready」「已放入提案」等字串猜 Proposal 狀態。
   - `_is_requirement_follow_up()` 用問號、「請提供」「還不知道」等片語猜下一輪是否為補充。
   - `_PLATFORM_OWNED_SYSTEM_COMMAND_INFORMATION` 用固定中文值刪除不該問老師的缺口。

   這些規則會被語氣改寫、同義詞、英文、HTML entity 或模型措辭輕易繞過。

3. **目前工作樹新增的學生紀錄特例**
   - 五組 `_STUDENT_RECORD_*_MARKERS`
   - `_student_record_scope()`
   - `_is_student_record_access_refusal()`
   - `_student_record_scope_fallback()`

   這組 guard 約新增 152 行 `service.py` 變更，能讓一部分「學生指令紀錄」拒絕回覆換成
   檢查導向文案，但只改 `reply_text`，沒有把 `updated_items=null` 變成有效 Proposal。
   CPU／記憶體／磁碟沒有命中這組 marker，下一個案例仍會要求再加另一組關鍵字。

### 3.2 必須保留的 deterministic boundary

以下不是妨礙泛化的硬編碼，而是安全與資料正確性的必要邊界：

- class、session、source ownership 與 active 狀態。
- `analysis_revision` 的呼叫前及回覆保存前雙重檢查。
- check step 必須引用已知的受控執行入口：有專用 command 時優先使用；沒有專用項目時，
  其他唯讀診斷指令可經 `system.run_command` 提供完整 argv，不得因 executable 沒有獨立
  catalog 項目就阻擋 Proposal，也不得發明新的 `command_key`。
- `system.run_command` 不使用 shell、pipe 或 redirect，且需拒絕修改、刪除、安裝、重啟、
  停止服務、提權及其他會改變系統狀態或具有破壞性的操作。
- argv 必須是非空字串陣列，timeout 必須有限；平台可以補預設 timeout。
- `judgement_mode=ai` 才需要可直接判定的 `success_criteria`；
  `judgement_mode=teacher` 只要能安全帶回證據就可 Ready。
- update/delete/引用現有項目必須先讀取目前 rubric，且只能操作真實 item id。
- Proposal 必須是實際 add/update/delete 差異；無差異不得顯示 Apply。
- 沒有選定 rubric source 時不得建立可套用 Proposal。
- Proposal 仍不能直接寫入 `analysis_json`，必須由老師 Apply。

收斂原則是：**保留結構、安全及 ownership 驗證；刪除中文語意關鍵字與案例型補值。**

## 4. 為什麼 2～3 輪就失去專注力

### 4.1 已排除：不是近期訊息已被裁切

- `bounded_history()` 上限是 20 則訊息與 24,000 字。
- summary 每 10 次 assistant 回覆才排程，且最多放回 8,000 字。
- 因此一般 2～3 輪對話仍在請求中；把 `HISTORY_MESSAGE_LIMIT` 從 20 調成更大，不能修正
  目前案例。

### 4.2 主要根因：結構化需求狀態在每輪結束時消失

`create_message()` 目前將 assistant message 保存為：

```text
content = 給老師看的自然語言 reply
metadata_json = { metrics, ui_hidden? }
```

沒有保存：

- 本輪是純問答、建立需求，還是在回答上一輪缺口。
- 目前聚焦哪一條需求或哪個 rubric item。
- 已確認的 target、scope、path/service/port、判定方式。
- 真正仍缺少且需要老師回答的資訊。
- 是老師輸入不足、模型 JSON/command 生成錯誤，還是平台能力不支援。

下一輪模型只能從已被改寫成 2～3 句的散文重建以上資訊。若散文沒有保留 item id、scope 或
先前的結構化欄位，第二輪就可能重問、改題或把需求誤判成能力詢問。

### 4.3 固定 reply／rewrite 確實會影響下一輪

會，而且影響的不只是語氣。現行 reply 可能依序經過：

1. 主對話模型產生 `reply + proposal_status + updated_items`。
2. Proposal 無效或缺資訊時，由 `_proposal_unavailable_reply()` 組合固定中文；特定 recovery
   也會先建立固定 `fallback_reply`。
3. 部分分支再呼叫 `_rewrite_teacher_reply()`，用另一份 system prompt 和裁切後的 facts 改寫；
   rewrite 失敗或不符合固定檢查時，又回到 deterministic fallback。
4. 學生紀錄特例還可能由 `_student_record_scope_fallback()` 整段覆蓋原 reply。
5. 下一輪 `_is_requirement_follow_up()` 不是讀取結構化缺口，而是檢查最後顯示文字是否含問號、
   「請提供」「還不知道」等固定 marker。

因此，一次 rewrite 若省略了先前已知條件，下一輪就無法恢復；fallback 換了措辭，也可能讓
follow-up audit 不再啟動。這正是「回覆文案反過來控制流程」的耦合，應和語意 marker 一起移除。

目標不是再設計一組更完整的 reply 模板，而是把需求核查、自然回覆與 Proposal 候選維持在
**同一個 `chat_with_rubric()` 對話回合**：模型在完整對話中自然說明已知道什麼、真正還缺
什麼，並同時輸出相同語意的結構化 requirement/candidate。後端驗證若發現模型格式或安全
錯誤，只把結構化 issue 回送同一對話做一次修正；修正結果本身就是最終回覆，不再另開一個
缺少完整歷史的 reply-rewrite 流程。只有 vLLM/JSON 完全失敗時保留一句不判定需求內容的通用
服務錯誤 fallback。

### 4.4 其他上下文壓力

1. 目前最小 normal system prompt 約 7,566 字，尚未加入真實完整 catalog、附件與歷史。
   規則大量重複「不得／只有／若」，同時混有安全、意圖、rubric 操作、回覆語氣與範例，
   對小型本機模型造成 instruction competition。
2. `chat_temperature` 是 0.7；對嚴格 JSON 規劃與多輪需求承接偏高，會放大措辭與欄位漂移。
3. 初次模型回覆若為 `needs_information`，目前會先完成一次可能已經重問的 generation，
   才因 `_is_requirement_follow_up()` 再進 audit call。repair request 又附上原始回覆與更多
   system instruction，焦點及 token 壓力反而增加。
4. `bounded_history()` 排除 `system_notice`，但沒有依 `metadata_json.ui_hidden` 排除舊的
   refine 對話。對普通聊天而言，已 Apply 後的全表潤飾指令與隱藏回答不是必要上下文，
   可能和最新單一需求競爭。
5. 歷史限制用字元數，不是整個 request 的 token budget；system prompt、catalog、附件、
   rubric tool 回傳與預留輸出並沒有被同一個 budget 計算。

## 5. 為什麼 CPU／記憶體／磁碟案例沒有提案

使用者看到的句子：

> 目前系統還無法直接取得學生的 CPU、記憶體或磁碟使用量等資訊 &#x20;

這句不是 repository 內的固定字串。現行流程會將模型 `reply` 原樣保存；
`redact_message_content()` 只遮蔽 secret，不處理 HTML entity，前端又用 `{msg.content}` 當
純文字顯示，所以 `&#x20;` 會原樣出現在畫面。

### 5.1 這個需求並非一律不支援

目前環境提供的專用 command 包含：

- `linux.disk_usage`：`df -h`
- `linux.memory_usage`：`free -m`
- `linux.processes`：`ps aux`
- 通用 `system.run_command`：可規劃單一唯讀診斷 argv，平台補有限 timeout

這些專用 command 是環境已知可用、應優先選擇的捷徑，不是完整白名單。即使沒有 `cpu_usage`
之類的專用 `command_key`，AI 仍可依需求選擇合適的 Linux／Windows 唯讀診斷工具，透過
`system.run_command` 提交完整 argv。此時後端應檢查的是「是否唯讀、是否會改變系統、是否
提權、是否使用 shell／pipe／redirect、範圍及 timeout 是否受控」，不是 executable 名稱有沒有
獨立 catalog entry。

這也不表示允許任意 shell：額外檢測仍統一走已登錄的 `system.run_command` runner，不可發明
新的 `command_key`，也不能用診斷需求包裝安裝、寫檔、刪除、重啟或其他狀態修改行為。

因此，如果需求是「在後續 Teacher Judge 執行階段，收集每台學生 VM/LXC 內目前的整機
資源資訊，結果交由老師查看」，合理結果應是：

```text
detectable = auto
judgement_mode = teacher
missing_information = []
check_steps = 優先使用已登錄的 memory/disk command；CPU 或其他檢測可使用
              system.run_command + 完整唯讀 argv
=> Ready Proposal
```

沒有門檻值不應攔截，因為老師可以看原始結果。

### 5.2 真正需要追問或不支援的分界

| 老師實際目的 | 應有結果 | 原因 |
| --- | --- | --- |
| 學生 VM 內目前整機 CPU／記憶體／磁碟快照，老師查看 | `auto + teacher`，直接提案 | 可用唯讀 guest command 收集；不需成功門檻 |
| 同上，但要系統自動判斷是否通過 | 缺少門檻時 `needs_information` | 需 CPU%、memory%、disk% 門檻與判定方式 |
| 指定程序的資源使用量 | 未提供程序名稱時 `needs_information` | target 會改變 argv 與資料範圍 |
| 指定磁碟／掛載點 | 未提供路徑且上下文有多個合理範圍時詢問 | 不應任意選根目錄或所有掛載點 |
| 一段時間平均值、峰值或歷史趨勢 | 需詢問期間／聚合方式；若要求 PVE 歷史則另判能力 | 單次 guest command 和歷史 telemetry 是不同來源 |
| PVE host 端的歷史 CPU／RAM／disk telemetry | 目前 Teacher Judge Proposal 不應假稱已支援 | 現行 check step/executor 是 guest 取證，不是 PVE metrics connector |

### 5.3 現在實際會攔下 Proposal 的條件

| Gate | 現況效果 | 判定 |
| --- | --- | --- |
| 前端沒有 `analysis`／selected source | 不送出或後端清掉 Proposal | 合理 |
| request revision 已過期或回覆期間 source/revision 改變 | 409，不保存舊回答 | 合理 |
| 模型回傳非 JSON、未知 status、或宣稱 Ready 卻沒有有效 items | 最多兩次 repair | 合理，但 repair 應收斂成一次結構修復 |
| 模型回傳 `proposal_status=none` + `updated_items=null` + 能力拒絕 | 不進 repair，沒有 Proposal | 本案例的主要誤擋 |
| `updated_items` 不是 list、item 不是 object | normalization 後消失 | 合理；應回傳 model issue code，不該問老師 |
| `detectable` 不是 `auto` | 一般 chat 的 `_proposal_changes(ready_only=True)` 排除 | 合理；但狀態必須由 validated facts 衍生 |
| 模型發明未知 `command_key` | step 被移除 | 合理；應回傳 model issue，不得轉成老師缺資料 |
| 診斷工具沒有專用 catalog 項目，但以 `system.run_command` 提供完整唯讀 argv | 應通過 runner 與安全 policy 驗證 | 不得以「不在 catalog」阻擋 Proposal |
| argv 含修改系統、破壞性操作、提權、shell／pipe／redirect 或無法受控的執行方式 | Proposal 排除 | 真正安全 gate；不得用 reply rewrite 繞過 |
| `system.run_command` 沒有非空 argv | item 轉成缺少／不支援，Proposal 排除 | 應先歸類為 model planning error；通常不是老師缺資料 |
| `judgement_mode=ai` 沒有 success criteria | `partial`，Proposal 排除 | 合理；可以改由老師查看時不應擋 |
| `judgement_mode=teacher` 沒有 success criteria | 允許 Ready | 現有正確契約，必須保留 |
| `auto` 沒有 detection method | 轉 `partial` | 合理，但文字可由已驗證 step 一般化產生，不必讓模型重填 |
| update/delete 指向不存在項目，或需要 rubric 卻沒讀 tool | operation 排除／強制 tool repair | 合理 |
| candidate 與正式 item 無實際差異 | 不回傳 Proposal | 合理 |
| API 有 Proposal，但前端 `buildProposalDiff()` 算出空差異 | `pendingProposal=null` | 合理；需在 trace 中可辨識 |

另需和「儲存並製作」分開：Proposal Apply 後，正式檢查表仍可能因 `manual`、`partial`、
`detectability_needs_review` 或不完整/失效 check step 被 script-generation gate 阻擋。那是
第二階段安全檢查，不是本次「為何沒有 Proposal」的同一個問題。

## 6. 方案比較

### 方案 A：只繼續補 prompt

- 優點：改動最小。
- 缺點：無法保留結構化多輪焦點；合法但錯誤的 `none` 仍可繞過 Proposal repair；prompt
  會繼續增長。
- 結論：不採用。

### 方案 B：把關鍵字與 regex 搬出 `service.py`

- 優點：`service.py` 行數下降。
- 缺點：行為仍由中文片語和案例清單決定，只是把耦合移到別的檔案；CPU 案例仍要再加規則。
- 結論：不採用。

### 方案 C：結構化回合結果 + backend-derived outcome（建議）

- 優點：直接修復多輪焦點、Proposal 誤擋與錯誤歸因；可刪除既有語意特例；public API、DB
  schema、Apply 與 executor 都不需改。
- 代價：需要調整內部 model JSON contract，並在既有 message metadata 保存精簡的未解焦點。
- 結論：採用。

不建議新增 Requirement 資料表、workflow state machine、長期草稿或另一條 Proposal API。
這些能力超出目前問題，也會和既有 session message／前端暫存 Proposal 重複。

## 7. 目標設計

### 7.1 單一內部回合契約

新增內部 Pydantic/dataclass 結果，不成為 public API：

```text
TeacherJudgeTurnDecision
  turn_kind: question | requirement | follow_up
  requirements:
    - focus_key
      target_item_id?
      status: ready | needs_information | unsupported
      known_information[]
      missing_information[]
      candidate_item?
  reply
```

原則：

- 模型回傳 `turn_kind` 與逐條 requirement；不再要求模型自己決定最終 `proposal_status`。
- `candidate_item` 可沿用既有 `TeacherJudgeRubricItem` 與 operation，不新增正式 rubric 欄位。
- backend 先驗證 candidate，再自行衍生本輪 outcome：
  - 至少一個有效 Ready diff → `ready`
  - 沒有 Ready，且存在老師真正需要補充的欄位 → `needs_information`
  - 沒有 Ready，且平台沒有安全取證方式 → `unsupported`
  - JSON/schema/command 生成錯誤 → `system_error`
  - 純問答且沒有 rubric 變更 → `none`
- public response 仍只有 `assistant_message`、`rubric_proposal`、`base_revision`。

### 7.2 明確區分三種 issue owner

command normalization 不再把 invalid step 靜默丟掉，而是回傳結構化 issue：

| owner | 範例 | 對外行為 |
| --- | --- | --- |
| `teacher` | 真正缺少檔案位置、程序名稱、Port、AI 判定門檻 | 問老師一個最小問題 |
| `model` | JSON 錯誤、發明 `command_key`、少 argv、把 teacher review 誤寫成需門檻 | 在同一對話內修復一次；不可說老師少給資料 |
| `platform` | 現行 executor 無法安全取得 PVE 歷史 telemetry | 清楚說明能力邊界，不假造 Proposal |

`timeout_seconds`、command 選擇、一般 CLI 參數是 backend/model 責任，不列成老師缺口。

### 7.3 精簡但穩定的多輪焦點

只在 assistant message 現有 `metadata_json` 加入例如：

```json
{
  "metrics": {},
  "conversation_focus": {
    "source_file_id": "...",
    "turn_kind": "requirement",
    "requirements": [
      {
        "focus_key": "student-resource-usage",
        "status": "needs_information",
        "known_information": ["檢查整台學生 VM 的資源使用量"],
        "missing_information": ["要目前快照，還是一段時間的平均／峰值"]
      }
    ]
  }
}
```

限制：

- 只保存已驗證的焦點與缺口，不保存完整未 Apply Proposal、command 草稿或執行結果。
- 只讀最新有效 assistant focus；同一 selected source 才帶入下一輪。
- 更新／刪除既有 item 時保留 target item id；下一輪仍必須以目前 rubric tool/revision 驗證。
- 清除訊息即自然清除 focus；不新增 migration 或 session 欄位。
- 舊 message 沒有 focus 時直接退回現有文字歷史，不建立長期相容層。
- summary 只保留長期背景；最新 focus 另行注入，不等待第 10 輪摘要。

這是對舊文件「完全不保存 Requirement 結構」取捨的最小修正：仍不保存 Proposal 或建立
Requirement entity，只讓既有對話訊息帶有下一輪所需的已驗證語意，避免 2～3 輪就失焦。

### 7.4 `service.py` 的目標責任

`service.py` 最後只保留：

1. 組合 request-scoped prompt/context。
2. 呼叫既有 Teacher Judge vLLM client。
3. 解析單一內部 turn contract。
4. 呼叫 command/rubric validator。
5. 需要時做一次 generic repair。
6. 回傳 `TeacherJudgeChatResult`。

純函式移動與收斂：

- rubric item/check step normalization 可放入新的
  `backend/app/ai/teacher_judge/rubric_normalization.py`。
- command existence、template resolution、parameter defaults 與 structured issue 留在
  `template_command_service.py`，成為唯一 command contract。
- message focus 的讀寫與 context assembly 留在既有 `session_service.py`。
- model-only turn schema 放在 `schemas.py` 或單一小型 `turn_contract.py`；二選一，不同時建立
  多層 wrapper。

### 7.5 統一對話與 Prompt 收斂

`CHAT_SYSTEM_TEMPLATE` 改只保留四組規則：

1. 角色、資料不可信與不立即執行學生環境。
2. `question / requirement / follow_up` 的判斷原則。
3. `auto + ai`、`auto + teacher`、缺資訊、不支援的語意。
4. 單一 JSON schema。

回覆與提案不得再拆成兩條語意流程：

- 同一次對話輸出自然 `reply`、逐條 requirement 狀態與 candidate items；三者必須描述同一結果。
- backend validation 只決定 candidate 是否可用及 issue owner，不自行用中文模板重講需求。
- 若 validation 發現 `owner=model` 問題，把結構化 issue 帶回原 messages 做一次同回合 repair；
  repair 回覆直接成為最終 assistant message。
- `owner=teacher` 時由模型依完整對話自然說出真正缺少的資料；不得經
  `_proposal_unavailable_reply()` 重新拼裝，也不得再以 reply marker 決定下一輪流程。
- 移除獨立 `TEACHER_REPLY_REWRITE_SYSTEM_PROMPT` 正常路徑；不再為自然化文字額外呼叫模型。

刪除：

- 已由 backend validator 保證的 timeout、command reference、revision、差異細節重述。
- 為特定範例新增的多組同義句與反例。
- 同一規則在 base prompt、situation、repair、reply rewrite 中的重複版本。

catalog prompt view 只列 selected `environment_keys` 已確認可優先使用的專用 command，加上
通用 `system.run_command`。backend 驗證已知 runner、argv 與安全 policy，但不要求每個額外
唯讀診斷 executable 都有專用 catalog key。

`chat_temperature` 建議由 0.7 降到 0.2～0.3，先以 scenario probe 驗證；本輪是結構化規劃，
不需要高創造性。`max_tokens` 暫不先加大，先縮短 prompt 並量測實際 prompt/completion tokens。

### 7.6 Reply 與 `&#x20;`

建立一個 model reply normalization boundary：

- 只將 whitespace entity（`&#x20;`、`&#32;`、`&nbsp;`）正規化成一般空白。
- 不把 reply 當 HTML，不使用 `dangerouslySetInnerHTML`。
- 新回覆在保存前正規化；舊 message 在 `message_public()` 使用同一 helper 呈現，無資料 migration。
- teacher reply 由統一對話直接產生；若內容與 backend-derived outcome 矛盾，視為
  `owner=model`，把結構化矛盾帶回同一對話做唯一一次 repair，不進獨立 rewrite prompt。
- deterministic fallback 只處理模型服務或 JSON 最終失敗，不判斷老師缺什麼，也不參與下一輪
  requirement/follow-up 辨識。

### 7.7 全域移除評分語意

不能只修改聊天室的 `CHAT_SYSTEM_TEMPLATE`。目前 `script_result_analysis_service.py` 仍要求模型
產生 5 分制 `score/max_score`，前端執行結果也會顯示 `x/5`；這和「協助導師檢查」直接衝突。
本次實作需一起收斂下列邊界：

1. **所有 Teacher Judge prompts**
   - `prompt.py`、`script_generation_contract.py`、`script_artifact_service.py`、
     `script_result_analysis_service.py`、coverage/quality review prompts 全部改用檢查語意。
   - AI 的角色是整理檢查條件、收集證據及核對有明確答案的項目，不提供成績或評分建議。
   - model-facing tool 改稱 `get_current_checklist`；prompt 不再出現「評分表／評分項目／評量」。

2. **檢查結果契約**
   - 新產生的 result 移除總分與逐項 `score/max_score`，不能只把「分數」改名後繼續計算。
   - 每個項目只保留 `item_id`、`title`、`status`、`evidence_refs`、`comment` 與核查責任。
   - `judgement_mode=ai` 只有在檢查表已有明確可比較答案時，才可依直接證據輸出
     `pass/fail`；缺證據時是 `unknown/skipped`，不得猜測。
   - `judgement_mode=teacher` 一律輸出 `needs_teacher_review`（或既有相容值 `unknown` 加上明確
     review flag），只整理證據與待看重點，不替導師判定通過／未通過。
   - 外層完成狀態表示「檢查流程已完成」，不代表全部項目通過。

3. **前端與匯出**
   - 移除 `x/5`、總分、評分心得；改顯示「已核對」「未通過」「待導師核查」「證據不足」及
     「檢查結果說明」。
   - 「評分標準」「評分項目」「AI 自動判斷」「導師人工審核」分別改為「檢查項目」
     「檢查條件」「系統自動核對」「導師核查」。
   - Excel sheet 與欄位改成「檢查表／檢查項目／確認狀態／核對方式」，不輸出分數欄位。
   - `checked` 對外顯示為「已確認／未確認」，不暗示課程成績。

4. **相容性範圍**
   - session chat response、Proposal、Apply 與 `analysis_json` 的既有 envelope 不改。
   - `rubric_proposal`、`rubric_snapshot_json`、`judgement_mode` 等內部欄位暫時保留，避免為名詞
     更換製造 DB migration 或大範圍 API churn；它們不再出現在教師可見文字。
   - 新執行結果停止寫入數字分數；既有歷史結果若含 `score/max_score`，前端只讀其 status、
     evidence 與 comment，不再顯示分數，無需回寫或 migration。
   - scope 僅限 Teacher Judge；其他真正負責課程成績的模組不做全域字串替換。

## 8. 實作階段

### Phase 0：先建立行為基線

不改 production 行為，先補 scenario characterization：

- 整機 CPU／記憶體／磁碟，老師查看結果。
- 同一需求要求自動判定但未給門檻。
- 程序資源使用量但未給程序名稱。
- PVE 歷史平均／峰值。
- 純詢問「目前能取得哪些資訊」。
- 三輪補充：目標 → 範圍 →「我自己看」。
- 多條需求同時有 Ready 與缺資料。
- 有明確答案的項目只能依直接證據核對 pass/fail，不產生分數。
- 沒有唯一答案或老師說「我自己看」時，仍建立收集證據的 Proposal，結果固定交由導師核查。

測試應驗證結構化 outcome、Proposal 與 issue owner，不再大量 assert prompt 必須含某段中文；
另建立 Teacher Judge 範圍的禁用詞檢查，確保教師可見文字與自然語言 prompt 不再出現
「評分表／評分項目／評量／打分／分數／評分心得」。

### Phase 1：建立 backend-derived turn result

1. 新增單一內部 turn contract。
2. 讓 command validation 回傳 normalized value + issues，不再靜默刪除後讓
   `_proposal_unavailable_reply()` 猜原因。
3. 由 validated requirements 衍生 Proposal/outcome。
4. repair 僅處理 `owner=model`，最多一次；`owner=teacher` 直接問真缺口，
   `owner=platform` 直接說明邊界。
5. 最終自然 reply 與 Proposal 留在同一對話結果，移除正常流程的第二次 reply rewrite。
6. public API 與前端 `rubric_proposal` consumer 維持不變。

### Phase 2：加入多輪 focus

1. `chat_with_rubric()` 回傳 compact conversation focus。
2. `create_message()` 將 focus 寫入同一 assistant message metadata。
3. `session_service` 讀取最新有效 focus，和 text history 分開交給模型。
4. 一般 chat 排除較舊 `ui_hidden` refine 訊息；本輪 refine 仍明確傳入。
5. 刪除 `_is_requirement_follow_up()` 與 follow-up audit 的額外模型 call。

### Phase 3：刪除特例並縮短 prompt

1. 先讓 Phase 0 scenario 全部通過新的 generic planner/validator。
2. 刪除 Python、package、config、text-file 與 student-record 的語意 regex/marker recovery。
3. 刪除 prose Ready detection 與中文 platform-owned 缺口比對。
4. 刪除 `_proposal_unavailable_reply()`、student-record reply fallback 與正常流程的獨立 reply
   rewrite；只留真正服務失敗的通用錯誤文案。
5. 將共用規則收斂到單一對話 prompt 與單一 command contract。
6. 確認刪除行數高於新增 orchestration/schema 行數，避免只是重新分檔膨脹。

### Phase 4：檢查語意、結果契約與 UI 回歸

1. 在 backend reply/public message 邊界處理 whitespace entity。
2. 將 Teacher Judge 的 prompts、schema descriptions、locale、Excel 與前端文字統一成檢查語意。
3. `script_result_analysis_service.py` 停止產生 5 分制與逐項分數，只輸出狀態、證據與說明。
4. 前端移除所有數字分數，改顯示系統核對或待導師核查；仍以 React 純文字顯示，不引入
   HTML renderer。
5. 舊 result 中的 score 欄位只做忽略式讀取，不回寫、不顯示、不 migration。
6. 驗證 Proposal panel、部分選取、Apply revision conflict、skip 與新訊息取代行為不變。

## 9. 預計修改檔案

必要範圍：

- `backend/app/ai/teacher_judge/service.py`
- `backend/app/ai/teacher_judge/prompt.py`
- `backend/app/ai/teacher_judge/schemas.py` 或單一新 `turn_contract.py`
- `backend/app/ai/teacher_judge/template_command_service.py`
- `backend/app/ai/teacher_judge/session_service.py`
- `backend/app/ai/teacher_judge/script_generation_contract.py`
- `backend/app/ai/teacher_judge/script_artifact_service.py`
- `backend/app/ai/teacher_judge/script_result_analysis_service.py`
- `backend/app/ai/teacher_judge/script_coverage_validator.py`
- `backend/app/ai/teacher_judge/export.py`
- `backend/app/api/routes/teacher_judge_sessions.py`
- `backend/app/locales/zh-TW/ai_teacher_judge.json`
- Teacher Judge 實際使用到的其他 backend/frontend locale keys
- `backend/config/system-ai.json`（只調整已量測需要的 Teacher Judge generation 參數）
- `backend/tests/test_ai_p1_regressions.py`
- `backend/tests/test_rubric_template_commands.py`
- `backend/tests/test_teacher_judge_sessions.py`
- `backend/tests/test_teacher_judge_script_artifacts.py`
- `frontend/src/pages/course-operations/class-workspace/AiJudgePanel.jsx`
- `frontend/src/pages/course-operations/class-workspace/AiJudgePanel.test.jsx`

視實際 diff 才修改：

- `frontend/src/services/aiJudge.js`
- `frontend/src/services/aiJudge.test.js`

不修改：

- session/file/message 資料表與 Alembic migration。
- public session message API 路徑與 `TeacherJudgeSessionChatResponse` 欄位。
- `pendingProposal` 暫存 ownership。
- Apply、autosave 與 `analysis_revision` 契約。
- script artifact、approval、PVE/SSH authorization 與 executor policy。
- 專用 command 優先策略、`system.run_command` 受控 runner 與 executor 安全 policy。

## 10. 驗證矩陣

### 10.1 Backend focused tests

```powershell
cd backend
uv run python -m pytest tests/test_ai_p1_regressions.py tests/test_rubric_template_commands.py tests/test_teacher_judge_sessions.py tests/test_teacher_judge_automation_support.py tests/test_teacher_judge_files.py tests/test_teacher_judge_script_artifacts.py -q
uv run ruff check app/ai/teacher_judge app/api/routes/teacher_judge_sessions.py tests/test_ai_p1_regressions.py tests/test_rubric_template_commands.py tests/test_teacher_judge_sessions.py tests/test_teacher_judge_files.py tests/test_teacher_judge_script_artifacts.py
uv run mypy app/ai/teacher_judge/service.py app/ai/teacher_judge/session_service.py app/ai/teacher_judge/template_command_service.py app/api/routes/teacher_judge_sessions.py
```

必要 assertions：

1. `none + refusal` 不再讓具體 requirement 靜默結束。
2. `auto + teacher` 沒有 success criteria 仍能形成 Proposal。
3. model 少 argv／發明 `command_key` 只觸發同一對話內的 repair，不要求老師提供 command key。
4. teacher 真缺 scope/target/threshold 時只問該缺口。
5. 第二、第三輪靠 `conversation_focus` 承接，不依上一句是否含問號或固定詞。
6. source 切換、清除對話、revision 變更不會沿用錯誤 focus。
7. `&#x20;` 不再出現在 public message，其他 HTML-like 文字仍作純文字處理。
8. 沒有專用 catalog key 的唯讀診斷可經 `system.run_command` 建立 Proposal；修改系統、
   破壞性或不受控 argv 仍被安全 policy 擋下。
9. 正常 Ready／缺資訊回合不再呼叫獨立 reply rewrite；最終文字和 Proposal 出自同一回合結果。
10. 明確答案項目只輸出 pass/fail 與證據；導師核查項目只輸出待核查與證據，兩者都沒有
    `score/max_score`。
11. Teacher Judge prompts、schema descriptions、export 與教師可見 UI 沒有殘留評分語意；
    測試 fixture 中若需驗證舊資料，必須明確標註為 legacy compatibility。

### 10.2 Frontend checks

```powershell
cd frontend
bun run test -- src/pages/course-operations/class-workspace/AiJudgePanel.test.jsx
bun run build
```

驗證 session/Proposal response contract 不變、Ready diff 仍顯示、空 diff 不顯示、Apply revision
防護不變；另驗證執行結果不顯示總分或逐項分數，並正確區分「系統自動核對」與
「待導師核查」。

### 10.3 真實模型驗收

mock tests 只能證明程式分支，不能證明本機模型真的能泛化。需要對目前部署的 vLLM 做一組
小型、固定案例驗收，至少包含：

1. 「檢查學生 VM 的 CPU、記憶體、磁碟使用量，我自己看結果。」
2. 「CPU 超過 80% 算不通過；記憶體和磁碟我自己看。」
3. 「檢查某個程序的 CPU 使用量。」→ 只問程序名稱。
4. 「查看過去七天 PVE 的平均 CPU。」→ 不得假裝 guest command 等於 PVE telemetry。
5. 三輪自然補充，最後必須出現可 Apply Proposal，且不重問已回答資訊。

記錄每輪 prompt tokens、completion tokens、模型 call 次數、derived outcome、issue owner 與
Proposal 筆數；不記錄 credential。驗收目標：正常 Ready 或真缺資訊回合原則上一次 planner
call 完成，只有 model 結構錯誤才允許一次 repair。

若要證明 check steps 實際可執行，另在隔離測試班級的一台學生 VM/LXC 執行唯讀 smoke；
Proposal 測試本身不需要也不應冒充 PVE/SSH E2E。

## 11. 風險與回退

- **風險：縮 prompt 後模型漏規則。** 先以 Phase 0 scenario 固定語意契約，再逐段刪除；
  安全與 revision 規則始終由程式驗證，不依 prompt。
- **風險：message metadata 成為另一份 Proposal。** 明確禁止保存 candidate item 與 command
  草稿，只保留未解焦點；Proposal ownership 仍只有前端暫存。
- **風險：舊對話沒有 focus。** 直接使用既有文字 history；不回填、不 migration。
- **風險：通用 command 被模型濫用。** 本計畫只改善 planning；既有 no-shell、argv、timeout、
  confirmation、policy 與 executor authorization 全數保留。
- **風險：歷史執行結果仍含分數欄位。** 不改寫歷史 JSON；前端忽略舊 `score/max_score`，只顯示
  既有 status/evidence/comment。新結果不再寫入分數。
- **風險：一次大改難回退。** 依 Phase 1～4 分批提交；每階段都維持 session/Proposal
  response 與 Apply 契約；只有執行結果內的數字分數欄位依本定位停止產生，可在不動 DB 的
  情況下回退。

## 12. 完成定義

本修正完成時應同時滿足：

1. `service.py` 不再含學生紀錄、Python、package、文字檔等自然語言案例 marker/recovery。
2. Proposal outcome 由 backend validated result 衍生，不從散文猜 Ready，也不因合法 `none`
   把具體 requirement 靜默吞掉。
3. 自然回覆、缺口說明與 Proposal 候選收斂在同一對話結果；固定 reply/rewrite 模板不再
   決定需求狀態或下一輪是否承接。
4. 兩至三輪補充能依結構化 focus 承接，已回答資訊不重問。
5. 整機 CPU／記憶體／磁碟由導師核查的案例能產生 Ready Proposal；真正要求自動門檻、
   特定程序、特定期間或 PVE 歷史時才問必要問題或說明能力邊界。
6. 專用 catalog 沒有列出的額外唯讀診斷仍可透過 `system.run_command` 規劃；安全 policy
   繼續阻擋修改系統、破壞性、提權與不受控執行方式。
7. `&#x20;` 等 whitespace entity 不再顯示，回覆仍以純文字安全渲染。
8. 未新增資料表、Proposal API、workflow framework 或持久化草稿；Apply、revision、catalog、
   approval、PVE/SSH 與 executor 安全邊界保持不變。
9. 教師可見介面、匯出與所有 Teacher Judge 自然語言 prompt 統一使用「檢查表／檢查項目／
   系統自動核對／導師核查」，不再把功能描述成評分或計分。
10. 新執行結果完全移除 5 分制、總分與逐項分數；明確答案輸出 pass/fail，沒有唯一答案的
    項目只整理證據並等待導師核查。
11. focused backend/frontend tests、Ruff、targeted mypy、build 通過；真實 vLLM 與需要時的隔離
   guest smoke 結果分開回報，不把 mock/build 稱為 E2E。
