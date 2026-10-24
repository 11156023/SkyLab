# AI 導師檢查流程與不可破壞契約

本文件固定 Teacher Judge 從老師提出需求、AI 建立或編輯提案、老師同意寫回，到全表核對、保存與製作腳本的產品流程。後續修改應先對照本文的狀態、資料邊界與回歸案例，避免為了讓單一案例通過而破壞整體流程。

核心契約：**老師說明在哪台機器、檢查什麼，必要時補充檔案位置；系統先確認如何取證及由誰判定，形成合格提案。老師接受後才寫回檢查表；製作前核對目前檢查點的全部項目，保存同一份通過核對的內容，再由後端驗證與編譯。**

契約是應維持的行為；描述目前程式做法的段落會標明「目前」，尚未修正的落差集中在第 9 節。

## 每週唯一生效腳本組

每個週次只有一個工作區。`teacher_judge_sessions.active_artifact_set_id` 是新執行資格的唯一來源；核准狀態與生效資格分開處理。

- 每次製作或重新製作都建立新的 `artifact_set_id`；全組節點編譯、契約驗證與來源版本核對成功後，所有 child 與生效指標在同一交易提交。失敗保留原組。
- 提交前重新核對工作區、選定來源、`analysis_revision` 與原生效指標；競爭中的舊候選回傳 `409`，不能覆蓋已切換的新組。
- 檢查表修改先視為草稿；成功製作下一組前仍使用原生效組，介面顯示尚未套用提示。同一生效組可多次執行。
- 整組、工作區單支與班級單支執行入口均受生效檢查限制。已受理批次固定原組及其 child；切換不會封存舊腳本或改換執行中的內容。
- 老師目前核查、學生作業與判定僅採用生效組的 child；同組分批重跑可補齊結果，跨組結果保留於歷史查詢。學生自評與每週回覆保留。
- 生效組，以及已有 run 或已受理 batch 引用的腳本，不能個別封存或刪除。

`tjactive01` migration 只為來源 revision 一致、必要執行節點與項目完整且全部核准的最新組回填生效指標；無法證明完整的工作區保留空指標，須重新製作後才能執行。migration 不刪除或改寫歷史腳本與執行結果。

回歸入口：`tests/ai/teacher_judge/test_active_script_set.py`；包含失敗回滾、來源／指標競爭、全部 HTTP 執行入口、切換中的已受理批次、排隊執行及 migration 回填。SQLite 模擬不等於 PostgreSQL 實際鎖定驗證。

## 1. 老師要提供的資訊

老師不需要填寫 collector、assertion、step ID 或 SSH 參數。這些是系統把自然語言轉成可驗證計畫時的責任。

| 資訊 | 何時需要 | 處理原則 |
| --- | --- | --- |
| 機器節點 | 每個要執行的項目 | 由老師指定或已明確的上下文取得；無法唯一判斷就回問。P1、P2 是顯示名稱，保存班級內穩定的 `target_node_key` |
| 檢查對象與動作 | 每個項目 | 例如檢查 nginx 是否啟動、讀取設定檔、執行作業程式、觀察另一節點連通性 |
| 路徑或工作位置 | 讀取、檢查或執行檔案時 | 必須能定位實際檔案；版本、服務狀態、CPU 等查詢不需要工作目錄 |
| 其他取證條件 | 視需求而定 | 服務名稱、Port、URL、日期範圍、命令參數、觀察節點等；只問會影響檢查的必要資訊 |
| 結果判定方式 | 每個項目 | 明確條件由系統核對，或老師明確選擇取回證據後親自核查 |

目前前端 `ChatPanel` 是文字與附件輸入，不是固定的「機器、路徑、檢查內容」三欄表單。上表描述資訊契約，不表示三個獨立欄位已實作。

### 1.1 三種容易混淆的概念

- **工作目錄**是老師描述檔案位置的上下文，例如「作業放在 `/srv/homework`，檔名是 `main.py`」。
- **絕對檔案路徑**是最終要讀取或執行的對象，例如 `/srv/homework/main.py`；它屬於指定的學生機器，不是 backend 主機。
- **標準答案或成功條件**是判斷證據的規則，例如輸出包含 `hello`、數值大於 10、服務處於 active；它與檔案是否使用絕對路徑無關。

「有標準答案」不一定代表要有一份答案檔。文字、數字、狀態、存在與否都能形成客觀條件。若老師另外提供答案檔，仍須確認比對哪些內容、採取何種比對方式；不能假定目前有通用的兩份檔案比對 collector。

### 1.2 路徑的最終契約

目前新寫入格式要求 `file_text.path`、`file_stat.path` 及已辨識的命令檔案／程式參數使用絕對路徑，**不接受 `collector.cwd`，連 `cwd: null` 也不能送出**。

老師已提供同一項目的目錄與檔名時，AI 可據此整理成絕對路徑，不必重問。例如 `/srv/homework` 加上 `main.py` 可形成 `/srv/homework/main.py`；只有「執行 main.py」則不足。不能猜登入目錄、使用範例路徑，或借用其他機器的目錄。

不同機器可以使用相同的 `/srv/homework/main.py`，由 `target_node_key` 區分；不需要把 P1／P2 或 node key 接到檔案路徑前面。`cat main.py`、`~` 或工作目錄加相對路徑都不能直接作為新計畫的最終執行位置。

服務／版本查詢如 `systemctl is-active nginx`、`python3 --version` 不涉及作業檔案，不必補路徑。`python3` 等工具名稱可由 PATH 尋找；需要絕對定位的是被檢查的檔案或程式。

絕對路徑只解決「找到哪個程式」，不等於「以該程式所在目錄執行」。目前編譯器不設定作業 cwd，遠端腳本從受管暫存目錄執行。若學生程式依賴相對資源、特殊環境或專案工作目錄，仍須確認該執行方式是否受支援；不能只補上 `main.py` 的絕對路徑就宣稱整個執行環境完整。

## 2. 合格提案的定義

合格是「計畫可以安全取證、判定方式已明確」，不是「學生作業已通過」。必須同時符合：

1. 執行節點屬於目前班級，執行者與觀察對象沒有混淆。
2. 保留老師的檢查目標，沒有拿較容易執行但無關的檢查替換需求。
3. 有完整、受支援的取證步驟與必要參數，沒有尚未解決的實際資訊缺口。
4. 判定方式已確定：系統自動核對有相容的 assertion；老師核查有明確取證內容且不帶 assertion。
5. 通過 typed schema、命令政策、步驟 ID 與路徑等契約檢查。
6. 提案依據目前檢查表版本建立，尚待老師在前端接受。

### 2.1 取證能力與判定方式是兩個維度

| 欄位組合 | 意義 | 可否製作腳本 |
| --- | --- | --- |
| `detectable=auto`、`judgement_mode=ai` | 可取證，並依明確規則自動判定 | 可以；每個步驟必須有相容 assertion |
| `detectable=auto`、`judgement_mode=teacher` | 可取證，老師看原始結果後判定 | 可以；省略 assertion，成功取證回傳 `collected` |
| `detectable=partial` | 還缺檔案位置、服務、範圍或其他必要資訊 | 不可以，先補資訊 |
| `detectable=manual` | 目前沒有受支援的安全取證方案 | 不可以，保留原因與 fallback |

`judgement_mode=ai` 是既有欄位名稱；目前新腳本的執行期由確定性 assertion 判定，不是把每份學生結果再交給 LLM 自由評分。編譯後的計畫把此模式標為 `system`。

「老師親自看」不能誤標成 `manual`。只要平台能安全取回指定證據，便可採用 `auto + teacher`。反過來，缺少標準答案也不能由 AI 擅自改成老師核查；應詢問「依什麼條件自動判定，或是否只取回結果由老師查看」。既有項目的判定方式只能依老師明確要求變更。

### 2.2 回問與修正的責任

| 情況 | 應採取的動作 | 不可採取的動作 |
| --- | --- | --- |
| 不知道哪個節點、哪個檔案或哪個服務 | 問老師缺少的實際資訊 | 猜測節點、路徑或服務 |
| 老師要求對答案，但無可用的客觀條件 | 問明確答案／規則，或是否要老師自行核查 | 擅自改成 teacher，或以命令退出碼 0 代替答案正確 |
| 老師已選擇自行核查 | 確認取回哪些證據、如何取得 | 再要求標準答案才能製作 |
| AI 使用錯誤欄位、漏掉技術結構、尚未讀既有項目 | AI 依 `issues`、`repair_hint` 修正 | 要老師重填已知資訊或解釋內部 JSON |
| 步驟已有絕對路徑，卻宣稱缺路徑 | 回報分析契約矛盾，核對並修正 | 刪除所有 `missing_information`，掩蓋其他真實缺口 |
| 平台不支援取證方式，或要求啟停／修改系統 | 說明限制，保留老師目標與替代建議 | 用無關的成功命令假裝已完成 |

普通 Chat 採用 Ready-only 提案：缺資訊或不支援的需求應留在回問與狀態中，不進入可套用候選。Finalizer 則可提出反映真實缺口的修正候選，但有缺口時 `script_ready=false`，不能接續製作。

## 3. 整體流程

```text
[老師輸入新需求]                         [老師要求調整既有項目]
        |                                         |
        |                          list_checklist / get_checklist_item
        |                          取得正式 ID、完整內容、目前版本
        +--------------------+--------------------+
                             v
             [確認節點 + 對象/動作 + 必要位置/範圍]
                             |
                  [確認判定方式與取證能力]
                             |
              +--------------+----------------+
              |                               |
     [缺少老師才知道的資訊]           [資訊完整且受支援]
              |                               |
          [回問老師]                    [建立 typed 候選]
              |                               |
              +---- 補充後重新分析 ---> [後端提案驗證]
                                              |
                      +-----------------------+-------------------+
                      |                                           |
              [格式/政策不合格]                           [合格暫存提案]
                      |                                           |
            [AI 修正或說明不支援]                      [前端顯示新增/修改差異]
                                                                  |
                                                         [老師選擇接受]
                                                                  |
                                                [檢查版本並寫回檢查表]
                                                                  |
                                                      [儲存並製作腳本]
                                                                  |
                                                     [先完成 autosave]
                                                                  |
                                              [Finalizer 核對目前整張檢查表]
                                                                  |
                                         [後端驗證套用修正後的完整 candidate]
                                                                  |
                         +----------------------------------------+------+
                         |                                               |
              [缺資訊/不支援/分析錯誤]                         [script_ready=true]
                         |                                               |
             [停止製作，回問或修正]                          [保存同一份核對結果]
                                                                         |
                                                            [取得新 revision]
                                                                         |
                                                [後端重新驗證 + 依節點確定性編譯]
                                                                         |
                                                       [靜態安全/品質/peer 驗證]
                                                                         |
                                                            [保存 approved 腳本集]
                                                                         |
                                                           [另行發起學生機器執行]
```

AI 工具建立暫存提案不等於寫回；寫回不等於製作；腳本 approved 不等於已執行；取證成功不等於作業通過。任何畫面文案與狀態都必須維持這些區分。

## 4. 新增與 AI 編輯既有提案

### 4.1 新增

AI 根據老師文字、附件與既有上下文整理需求，確認條件後呼叫 `create_checklist_item`。工具參數以 `TeacherJudgeTypedCheckStep` 為唯一新步驟格式，先驗證原始參數，再檢查項目契約。不能先丟棄壞步驟，讓剩下的部分看似成功。

提案只存在該回合的 staged operations 與回應／訊息 metadata；`TeacherJudgeFile.analysis_json` 此時尚未被提案工具修改。前端以差異預覽讓老師選擇接受，接受後才呼叫檢查表保存 API。

### 4.2 編輯

```text
老師要求修改既有檢查
  -> list_checklist 找到正式項目 ID（若尚不知道）
  -> get_checklist_item 讀完整項目與 execution_readiness
  -> edit_checklist_item 送出變動欄位
  -> 後端合併既有內容並驗證
  -> 前端顯示差異
  -> 老師接受
  -> 驗證 base revision
  -> 保存更新後的檢查表
```

`list_checklist` 只有索引，不含完整路徑與步驟；不得視為已閱讀。後端以 `read_ids` 強制先讀再改，否則回傳 `checklist_item_not_read`，由模型讀取後修正，無須老師補資料。

編輯省略的欄位保留原值，包括 `missing_information`。送出 `check_steps` 則是**整個步驟陣列替換**，必須保留原需求所需的全部步驟，不能只傳本次修改的一步。改成 `auto` 不會自動清空缺口；已解決的缺口要明確移除，其他範圍與條件要留下。

同一回合的工具可以讀到已暫存候選。已保存的檢查表仍是持久化依據，不能讓聊天摘要取代正式資料。新增項目遇到相同標題也不能直接建立重複項，應先確認是否在修改原項目。

### 4.3 所有新步驟使用同一寫入格式

新增、修改、附件提案合併與直接儲存的新步驟，都使用 `TeacherJudgeTypedCheckStep`。每步包含穩定的 `id`、`title`、`collector`，系統判定另附相容的 `assertion`。collector 與 assertion 的類型欄位統一為 `type`，預期值統一為 `expected`；第 8 節提供 JSON 範例。

新步驟不接受 flat `argv`、`command_key`、`parameters` 或 `cwd`。command 的參數應放在 `collector.argv`；command catalog 只提供能力參考，不是第二種提案格式。歷史讀取模型所提供的步驟 JSON schema 也引用同一 typed 模型，不能另外放寬模型工具的寫入格式。

新增／修改工具須先驗證原始步驟，再呼叫 `typed_item_issues()` 檢查候選。附件逐項分析完成後，合併階段仍要再次呼叫 `typed_item_issues()`，檢查合併後的項目及同節點 step ID 衝突；不能因為個別分析成功就省略合併驗證。

### 4.4 歷史資料的讀取與更新

歷史 flat／catalog 步驟與非空 cwd 保留讀取，避免遺失既有定位資訊。只變更顯示名稱等中繼資料時，可原樣保存未改動的歷史計畫；一旦改動執行計畫、判定方式或缺口，就必須符合目前格式。歷史解析能力不能成為新提案的驗證例外。

含 cwd 的舊計畫不能直接製作新腳本。核對時只能根據同一項目的已知資訊轉成絕對路徑；資料不足就保留缺口。既有歷史腳本不會因為檢查表更新而自動改寫，必須重新製作才會使用新的計畫與編譯器。

## 5. 儲存並製作的順序與失敗處理

目前前端 `handleSaveAndCreate()` 的順序如下：

```text
前端                      後端                        資料庫/編譯器
 |                         |                               |
 |-- flush autosave ------>|-- 保存 revision R ----------->|
 |<-- 保存完成 ------------|                               |
 |-- messages ------------>|                               |
 |   is_refine=true        |-- 讀取 R、呼叫 Finalizer       |
 |   analysis_revision=R   |-- 合併 proposal 為完整 candidate|
 |                        |-- 全表 readiness + 工具錯誤檢查 |
 |<-- proposal +           |                               |
 |    script_ready -------|                               |
 |                        |                               |
 |  false/缺欄位：停止     |                               |
 |  true：核對版本與候選   |                               |
 |                        |                               |
 |-- PATCH analysis ----->|-- CAS 保存 candidate --------->|
 |   expected_revision=R  |                               |
 |<-- 新 revision R+1 ----|                               |
 |-- POST script-sets --->|-- 重讀 R+1、完整前置驗證 ------->|
 |   analysis_revision   |-- 依 target_node_key 分組編譯 -->|
 |   =R+1                |-- 靜態政策/品質/peer 驗證 ------>|
 |                        |-- 全部成功後保存 artifacts --->|
 |<-- approved 腳本集 ----|                               |
```

CAS 指保存時 SQL 同時比對目前 revision；若其他請求已更新，不能覆蓋它。正常流程中的 R+1 是一次成功保存後的新版本，實作應使用 API 回傳值，不能自行猜測。

一般聊天提案需要老師按接受；目前「儲存並製作」按鈕本身包含套用本輪合格核對結果的授權，因此 `script_ready=true` 時前端會自動保存 Finalizer 修正，不再多按一次接受。此語意不能被誤改成背景聊天也能自動保存。

| 失敗點 | 應有結果 |
| --- | --- |
| autosave 未完成或失敗 | 不送 Finalizer 或製作請求，保留未保存狀態 |
| proposal 結構不合法 | 停止，不把自由文字當成可套用提案 |
| `script_ready=false` 或缺少明確 `true` | 停止，顯示缺口／錯誤；有有效修正時可保留候選供老師處理 |
| 核對期間 revision 改變 | 衝突，不能套用舊版本候選 |
| 核對結果保存失敗 | 不呼叫腳本製作 API |
| 腳本製作失敗 | 保留已保存的檢查表供修正或重試，不宣稱已有腳本 |
| 任一節點編譯或政策驗證失敗 | 不留下部分成功的新腳本集；修正後重試 |

沒有提案變動的核對仍須取得後端就緒結果、保存核對狀態，再製作；不能用「沒有修改」推論「已經安全」。

## 6. Finalizer 要檢查什麼

範圍是**目前檢查點的全部檢查項目**，不是僅檢查本輪修改項或老師勾選的項目。`checked` 不等於允許跳過其他項目；現在的 blockers 與編譯器會遍歷全表。

| 核對面向 | 必須保留的契約 |
| --- | --- |
| 需求語意 | 不改老師目的、不任意降低成功門檻、不把系統判定改為老師核查來繞過問題 |
| 節點 | 每項有正確執行節點；跨節點觀察時另外設定 `peer_node_key`，不能把執行者與被觀察者交換 |
| 位置與範圍 | 只對實際依賴檔案位置的項目要求路徑；保留已知位置、服務與日期範圍 |
| 取證步驟 | 完整 typed collector，命令與參數受支援，沒有缺步或同節點重複 step ID |
| 系統判定 | 每一步有相容 assertion，預期值來自老師需求或已確認資料 |
| 老師核查 | 有明確的證據取得方式，省略 assertion；不要求標準答案 |
| 安全與完整性 | 不含啟停服務等變更操作；不靠刪除問題項目、換成無關命令使全表通過 |
| 歷史資料 | 舊 flat/catalog/cwd 計畫可讀，但製作新腳本前要轉為目前 typed 絕對路徑契約 |
| 工具結果 | 未解決的 error、rejected、duplicate 必須阻擋；已明確 resolved 的錯誤不再阻擋 |

最終 `script_ready` 由後端 `refine_readiness_workflow()` 計算：在已保存表上套入提案，再呼叫 `get_script_generation_blockers(require_typed_plan=True)`，補上未解決工具錯誤。不是採用模型自由文字中的「已完成」或「ready」。

目前 AI 初次必須呼叫 `list_checklist`，成功後回到自動選工具；修改某項前必須讀該項完整內容，但**沒有強制所有項目逐一呼叫 `get_checklist_item`**。因此目前可以證明「後端驗證整表機器契約」，不能宣稱「AI 每次均逐項閱讀並完成全部語意審查」。此落差見第 9 節。

### 6.1 缺資訊與分析錯誤必須分開

真正缺少檔案位置、日期或服務範圍時，才需要老師提供資訊。步驟已有絕對位置，但 `missing_information` 又宣稱缺路徑時，應回傳 `check_plan_contract_invalid` 與 `teacher_input_required=false`，讓模型核對修正。

路徑是否完整應獨立於 assertion 或其他編譯錯誤判斷；不能因為 assertion 不合法，就把已知路徑視為未知。一個項目缺資訊，也不能遮住另一個項目的契約錯誤。最終工作流程回覆由後端實際 blockers 組成，不再拼接模型自由文字的缺口摘要。

### 6.2 保護既有位置與真實缺口

Finalizer 核對同一機器、項目與步驟時，不能把已知絕對位置退回無法定位的相對輸入。只有已被目前步驟解決、且由系統固定產生的舊位置缺口，才可由程式清除；其他路徑說法交回模型核對修正。

不得以路徑關鍵字直接刪除老師需求，也不能借用其他機器的位置。日期、服務或資料範圍等其他缺口必須保留。這項保護是製作前核對的限制，不能誤用來禁止老師明確提出的新位置修改。

### 6.3 工具回合與空回覆的收尾

初次索引讀取成功後，工具選擇回到 `tool_choice=auto`。模型可以讀取完整項目後修改、詢問缺資訊，或說明無須修改的依據；只讀索引時，不得宣稱已檢查執行細節。

工具回合耗盡後會明確要求文字收尾。模型回傳空文字且沒有工具呼叫時，只有已回報 completion usage 且仍有預算，才補問一次；補問共用原呼叫的時間與 token 上限，保留既有工具結果，不重跑已執行的工具。成功修復的用量包含空回覆那一輪。

再次留空、預算不足或回應結構錯誤都回報失敗，不能產生「檢查表狀態良好」的預設結論。工具回傳的修正僅在 `retryable=true` 時繼續，不能承諾未實作的背景重試。

## 7. 腳本製作與後端安全邊界

### 7.1 編譯流程

`POST script-sets` 重新讀取已保存的檢查表與 revision，驗證全表就緒及節點契約。`create_artifact_set()` 再執行 `canonicalize_check_plan()`，按 `target_node_key` 分組，以 `compile_check_plan()` 產生每個節點的 Python 腳本。

編譯器不呼叫 LLM。AI 負責解讀需求與整理計畫；確定性編譯器負責驗證並產生受控的取證及 assertion 程式。`ai_review_result_json` 是既有欄位名稱，新腳本記錄的 `mode=deterministic_compiler` 不能被解讀成額外一輪模型安全審查。

編譯後還會檢查靜態安全政策、輸出品質及 peer runtime 契約。所有節點完成後才保存新 artifacts，包含來源檔案、來源 revision、rubric snapshot、腳本內容與驗證結果；新的成功腳本直接為 `approved`。

### 7.2 不可省略的驗證

- **入口權限與班級範圍：**由 instructor route 與 `_access` 控制，節點必須屬於本班；前端按鈕不是權限邊界。
- **結構契約：**新提案、更新、附件合併、直接保存與製作要使用同一 typed 規則，不能只改 prompt 或只檢查其中一條入口。
- **執行位置：**已辨識的檔案與程式路徑必須絕對定位；路徑通過驗證不代表遠端檔案已存在或可讀。
- **命令政策：**字面 argv、命令白名單與逐命令旗標檢查；阻擋 shell launcher、管線、重導向、inline code/eval 及不允許的系統修改。會主動連線的工具（`curl`／`wget`／`nc -z`／`ping`／`traceroute`／`dig @server`／資料庫客戶端）只能指向本機，跨節點只有 `ping` 可接 `{{peer.ip}}`；`openssl` 只允許離線檢視（不含 `s_client`，也不可載入 engine／provider）。
- **服務檢查：**查詢是否啟動可接受；`systemctl start/restart/enable` 等實際改動不屬於檢查腳本。
- **判定契約：**collector 與 assertion 類型必須相容；teacher 步驟不能帶 assertion，ai 步驟不能漏 assertion。
- **跨節點：**使用穩定的 peer key 與受管 runtime context；不能在 AI 提案中猜 IP，`{{peer.ip}}` 只能作為完整 argv 元素。
- **產物驗證：**編譯後仍須通過靜態 policy、quality 與 peer 驗證，不能因為「由編譯器產生」就省略。

例如 `file_text` 可以比對文字、數值或 JSON 欄位，但不能拿 `exists` assertion 配它；判斷檔案存在使用 `file_stat + exists`。命令正常退出只能證明該命令的退出狀態，不能代替內容正確，除非老師要檢查的就是該退出狀態。

### 7.3 取回與執行的實際含義

「抓回檔案」在目前 typed `file_text` 流程是讀取指定文字內容，放進受限長度的證據／結果 JSON，不是承諾提供任意二進位檔案、完整目錄或無上限原始檔下載。老師需要完整檔案或超過上限的資料時，應明確判斷是否超出能力，不能把截斷結果當成完整答案。

實際執行是另一個動作：建立 script set run，解析學生機器，透過 Linux SSH/python3 上傳腳本與 runtime context、執行、下載 `result.json`／stderr、驗證結果並清理暫存檔。此階段才知道工具是否存在、檔案是否存在、權限是否足夠、命令是否逾時。

目前直譯器政策允許部分學生原始碼檔案的執行；檢查腳本預設使用 root SSH。這些 argv／副檔名／路徑檢查**沒有證明學生程式內容無副作用，也不是 OS 沙箱**。不能把「靜態政策通過」寫成「執行任何學生程式都保證安全」。

系統 assertion 比對成功／失敗與取證異常要區分：命令缺失、找不到待讀檔案等通常回傳 `unknown` 與錯誤；`file_stat + exists` 則可依明確的存在條件判定。teacher 成功取證為 `collected`，等待老師決定，不自動判 pass。程式退出碼非零也不能一律當正常收集完成。

### 7.4 批次受理、背景準備與逾時恢復

`POST script-sets/{artifact_set_id}/runs` 在工作區列鎖內確認生效腳本組，保存 batch、child runs 與選定學生機器快照，再提交既有背景執行器並回傳批次資料。受理請求只讀寫資料庫，不等待 PVE、OS、IP、SSH 或整批執行完成；背景準備沿用受理時固定的腳本與 VMID，不重新挑選已切換的生效組或學生機器。

- 同一週已有 `pending`／`running` 批次時，新提交回應 409，前端接回現有進度。同一操作可攜帶 `run_batch_id`；遺失回應後重新確認必須沿用原 ID 與範圍，即使原批次已完成也不得另建任務。未帶此欄位的既有呼叫仍由後端產生 ID。
- 初始 run 的 `progress_json.stage=preparing`、`prepared_at=null`；背景完成前置檢查後才轉為 `pending_executor` 並填入準備完成時間。逐台失敗包含未分配 VMID 的機器，仍保存在既有 canonical target 紀錄。
- 前端 timeout 只表示受理結果未知，不能宣稱執行失敗或取消。前端先查原批次 ID；找不到時查進行中的批次，查詢暫時失敗則保持阻擋新執行並持續重查。頁面重載也從批次清單接回，不依賴已開始執行的 child run。
- 背景準備失敗、執行器拒收與取消都必須把尚未完成的 run／target 收斂為終態；取消先停止後續準備並等待當前同步操作結束。慢速準備返回後須鎖定並重查 run 狀態，不得恢復已回收的任務。

SSH 維持每 run 最多 50 台、單一 backend process 共用最多 200 workers；同批次的 child runs 依序執行。背景工作仍使用既有 process-local runner，並非可跨重啟自動續跑的持久佇列；非正常程序中止留下的紀錄由既有 stale-run 回收處理。

回歸入口：`test_batch_admission.py`、`test_execution_records.py`、`AiJudgePanel.batchExecution.test.jsx`。100 台快照與模擬 SSH 測試不等於 100 台實機容量或 PostgreSQL 跨程序鎖定驗證。

## 8. 具體情境

| 老師需求 | 路徑需求 | 合格計畫或回問 |
| --- | --- | --- |
| 「P1 的 nginx 應該啟動，幫我確認」 | 不需工作目錄 | 解析 P1 的 node key，查詢 nginx 狀態，依明確 active／退出狀態規則判定 |
| 「看看服務需不需要啟動」 | 不需先問目錄 | 若服務名稱或預期狀態未知，先問；不把檢查理解成代為啟動服務 |
| 「P1 抓 `/srv/hw/result.txt` 回來，我自己看」 | 已有完整路徑 | `file_text`、`auto + teacher`、無 assertion；取證後為 `collected` |
| 「P1 的作業目錄是 `/srv/hw`，執行 main.py，輸出包含 hello」 | 由已知目錄與檔名形成絕對路徑 | `command.argv=["python3", "/srv/hw/main.py"]`，配 `text_contains`；另確認沒有尚未滿足的執行依賴 |
| 「P1 執行 main.py 幫我對答案」 | 尚缺檔案位置 | 回問實際位置與預期條件；不能以退出碼 0 假裝已對答案 |
| 「P1 執行 `/srv/hw/main.py`，結果我自己判斷」 | 不需再問目錄 | 受支援的 command、`auto + teacher`，保留輸出與錯誤 |
| 「把剛才包含 hello 改成只能輸出 hello」 | 沿用已知位置 | 先讀既有項目，以完整步驟陣列把 `text_contains` 改成適當的 `text_equals`，保留節點與其他步驟，讓老師確認差異 |
| 「從 P1 ping P2」 | 不需檔案路徑 | P1 是 target、P2 是 peer，使用受管 peer 解析，不猜學生 IP |

以下只示範新步驟格式，非完整 API request；實際值必須來自該項目需求。

```json
{
  "id": "result.text",
  "title": "取得作業輸出",
  "collector": {"type": "file_text", "path": "/srv/hw/result.txt"},
  "assertion": {"type": "text_contains", "expected": "hello"}
}
```

老師核查版本沿用 collector，省略 assertion，並在所屬項目設 `judgement_mode=teacher`。不能僅刪除 assertion 而留下 `judgement_mode=ai`。

## 9. 目前落差與需保留的限制

以下落差在目前程式中仍然存在，不是已完成的修正。

| 發現 | 目前證據 | 對後續修改的要求 |
| --- | --- | --- |
| 全表機器驗證不等於全表 AI 語意審查 | `_run_proposal_tool_loop` 強制第一次 list，之後 auto；`test_refine_tool_loop.py` 明確容許 list 後直接說明 | 若產品要求每項均由 AI 深讀，需另補可驗證的完整內容覆蓋機制；本文件不宣稱目前已有 |
| 製作 API 不要求 Finalizer 完成證明 | `_session_rubric_for_script_set` 重讀保存內容、檢查版本與 blockers，沒有驗證同 revision 必須有先前 Finalizer 成功紀錄；revision 在 API schema 亦可省略 | 正常 UI 已維持完整順序，但不能把 UI 流程說成所有 API client 都被強制走過 AI 核對 |
| 絕對程式路徑不能保證程式的工作目錄依賴 | 編譯器不設定 cwd；遠端從受管暫存目錄啟動 script.py | 需要專案目錄才可執行的作業不能被靜默判為資訊已足夠；不要重新偷偷加入 cwd 相容層 |
| 程式執行不是沙箱 | 允許指定語言的學生原始碼，executor 使用 root SSH | 不把靜態政策當作學生程式的副作用隔離；進一步隔離屬另一次明確的安全設計工作 |
| 文案可能沿用舊模型審查描述 | 前端部分註解仍說「靜態與 AI 導師檢查」；artifact 的 review 實際 mode 為 deterministic compiler | 區分製作前 AI 整理與編譯後確定性驗證，不能宣稱存在額外的腳本 LLM 審查 |

後續修正應逐項依真實需求處理，修正後再從本表移除。

## 10. 維護入口與資料依據

下列連結指向 GitHub 上 `main` 分支的原始碼。

| 邊界 | 主要入口與責任 |
| --- | --- |
| 前端提案／接受／製作 | [AiJudgePanel.jsx](https://github.com/SkyLab-tw/SkyLab/blob/main/frontend/src/pages/course-operations/class-workspace/AiJudgePanel.jsx)：`ChatPanel`、`applyPendingProposal`、`handleSaveAndCreate` |
| 前端 API 與潤飾指令 | [aiJudge.js](https://github.com/SkyLab-tw/SkyLab/blob/main/frontend/src/services/aiJudge.js)：`sendSessionMessage`、`updateFileAnalysis`、`createSessionScriptSet` |
| 對話 HTTP 與交易 | [teacher_judge_sessions.py](https://github.com/SkyLab-tw/SkyLab/blob/main/backend/app/api/routes/teacher_judge_sessions.py)：版本、session、refine readiness、script set 入口 |
| AI 意圖與工具回合 | [prompt.py](https://github.com/SkyLab-tw/SkyLab/blob/main/backend/app/ai/teacher_judge/prompt.py)、[service.py](https://github.com/SkyLab-tw/SkyLab/blob/main/backend/app/ai/teacher_judge/service.py)：read-before-edit、staged proposals、缺口與工具修正 |
| 欄位契約 | [schemas.py](https://github.com/SkyLab-tw/SkyLab/blob/main/backend/app/ai/teacher_judge/schemas.py)、[check_plan_contract.py](https://github.com/SkyLab-tw/SkyLab/blob/main/backend/app/ai/teacher_judge/check_plan_contract.py)：typed schema、新寫入、位置矛盾、舊資料邊界 |
| 全表製作就緒 | [session_chat_service.py](https://github.com/SkyLab-tw/SkyLab/blob/main/backend/app/ai/teacher_judge/session_chat_service.py)、[automation_support.py](https://github.com/SkyLab-tw/SkyLab/blob/main/backend/app/ai/teacher_judge/automation_support.py)：candidate 合併後的 blockers 與 script_ready |
| 保存與版本 | [file_service.py](https://github.com/SkyLab-tw/SkyLab/blob/main/backend/app/ai/teacher_judge/file_service.py)：`TeacherJudgeFile.analysis_json`、`analysis_revision`、資料庫條件更新 |
| 腳本集 | [script_artifact_service.py](https://github.com/SkyLab-tw/SkyLab/blob/main/backend/app/ai/teacher_judge/script_artifact_service.py)：依節點編譯、來源 revision、snapshot、全部成功後保存 |
| 編譯與政策 | [deterministic_compiler.py](https://github.com/SkyLab-tw/SkyLab/blob/main/backend/app/ai/teacher_judge/deterministic_compiler.py)、[script_policy.py](https://github.com/SkyLab-tw/SkyLab/blob/main/backend/app/ai/teacher_judge/script_policy.py)、[script_quality_validator.py](https://github.com/SkyLab-tw/SkyLab/blob/main/backend/app/ai/teacher_judge/script_quality_validator.py) |
| 執行與收證 | [script_executor_service.py](https://github.com/SkyLab-tw/SkyLab/blob/main/backend/app/ai/teacher_judge/script_executor_service.py)：Linux SSH、受管暫存目錄、結果下載與驗證 |

Teacher Judge 屬於內建 System AI，模型 transport 經共用 `VLLMClient` 與 `VLLM_*` 設定。它不是公開 `ccai_*` data plane，不應為修正提案流程而改動公開 AI relay 或 LiteLLM credential 邊界。

### 10.1 各入口的驗證責任

| 入口 | 必須使用的驗證與結果 |
| --- | --- |
| 模型／API 步驟 schema | `schemas.py` 的 typed 模型；歷史讀取模型對外提供相同的新步驟 JSON schema |
| `list_checklist` | 只提供索引，不能當成已讀過執行內容 |
| `get_checklist_item` | 提供完整項目與後端 `execution_readiness`，修改既有項目前必須讀取 |
| 新增／修改工具 | 驗證原始步驟，再以 `typed_item_issues()` 檢查候選；不丟棄失敗步驟 |
| 附件提案合併 | 再以 `typed_item_issues()` 檢查合併結果與同節點 step ID 衝突 |
| 檢查表儲存 | 以 `analysis_write_issues()` 驗證變更，再以資料庫 revision 條件更新 |
| Finalizer／製作前檢查 | 以 `get_script_generation_blockers()` 判斷完整候選，區分缺資訊與契約錯誤 |
| 腳本編譯 | 以 `canonicalize_check_plan()` 驗證完整計畫，再由確定性編譯器產生不含 cwd 的腳本 |
| 前端 | 顯示保存內容與後端結果；舊格式提示待核對，不另外實作一套命令就緒規則 |

## 11. 防止回歸的驗收案例

以下是未來改動的驗收清單；列出案例不代表每項已有自動化測試，實際執行範圍見第 12 節。

| 案例 | 必須觀察到的結果 |
| --- | --- |
| 服務／版本查詢沒有路徑 | 不要求工作目錄，資訊足夠即可形成合格提案 |
| 檔案路徑不足 | 回問真正缺口，不建立可套用的普通 Chat 提案 |
| 已有絕對路徑但 assertion 壞掉 | 修 assertion，不再要求老師提供路徑 |
| 系統對答案缺明確條件 | 回問判定規則，不偷偷切 teacher，也不改成退出碼 0 |
| 老師明確要自行查看 | `auto + teacher` 可製作，無 assertion，不因無標準答案而阻擋 |
| 修改既有項目只讀過 list | 拒絕 edit，先 get 完整內容 |
| 只修改多步驟中的一個 | 送出完整步驟陣列，其他步驟不遺失 |
| 尚未接受提案 | 正式檢查表與 revision 不因提案工具而改變 |
| 提案建立後檢查表被改動 | 舊提案套用或製作被版本衝突阻擋 |
| 表中一項缺資訊、另一項格式錯誤 | 兩種問題均可被辨識；不因第一項缺資訊而遮蔽後者 |
| 表中有未勾選但不可製作的項目 | 仍阻擋全表，不偷偷漏掉 |
| Finalizer 未修改任何項目 | 仍做完整後端 readiness，不靠「無變更」直接製作 |
| 未解決的工具錯誤或空模型回覆 | 失敗可見，不補出「檢查表良好」的成功預設文字 |
| 核對結果保存尚未完成或保存失敗 | 不送 script-sets request |
| 已保存核對結果但編譯失敗 | 保留已保存內容，停在可修正狀態 |
| 任一節點編譯失敗 | 不出現部分成功的新腳本集 |
| 要求啟動服務、shell 串接、inline code | 後端政策拒絕，不能靠前端允許或 prompt 說安全放行 |
| 舊 cwd/flat 計畫重新製作 | 必須轉為有效 typed 絕對路徑計畫，否則停止 |
| 跨節點檢查 | 正確區分 target／peer，不借用其他節點位置 |
| teacher 取證成功／命令失敗 | 分別呈現 collected／錯誤，不當成同一種作業通過 |

## 12. 回歸測試與現場診斷

修改本文涉及的流程後，至少重跑下列聚焦測試：

```powershell
# backend/
uv run python -m pytest tests/ai/teacher_judge/test_absolute_path_contract.py tests/ai/teacher_judge/test_finalizer_execution_contract.py tests/ai/teacher_judge/test_finalizer_path_conflicts.py tests/ai/teacher_judge/test_deterministic_compiler.py tests/ai/teacher_judge/test_contract_convergence.py tests/ai/teacher_judge/test_refine_tool_loop.py -q

# frontend/
bun run test src/pages/course-operations/class-workspace/AiJudgePanel.saveAndBuild.test.jsx src/pages/course-operations/class-workspace/AiJudgePanel.autosave.test.jsx src/services/aiJudge.test.js
```

這些測試涵蓋 typed／絕對路徑、Finalizer 修正與位置保存、確定性編譯、模擬模型工具回合，以及前端保存後製作順序。模型使用 mock/scripted transport，相關持久化案例使用隔離 SQLite；不等同正式 PostgreSQL、真實 LLM、遠端學生 VM 或部署端到端驗證。

### 12.1 修改契約時的擴大回歸

涉及新寫入格式、附件合併、保存或製作入口時，應依影響範圍擴大至下列既有測試。

```powershell
# backend/
uv run python -m pytest tests/ai/teacher_judge tests/test_teacher_judge_automation_support.py tests/test_teacher_judge_convergence.py tests/test_teacher_judge_boundaries.py tests/test_teacher_judge_files.py tests/test_teacher_judge_attachments.py -q

# frontend/
bun run test src/pages/course-operations/class-workspace/AiJudgePanel.test.jsx src/pages/course-operations/class-workspace/AiJudgePanel.saveAndBuild.test.jsx src/pages/course-operations/class-workspace/AiJudgePanel.autosave.test.jsx src/services/aiJudge.test.js
```

主要契約案例位於 `test_absolute_path_contract.py`、`test_contract_convergence.py` 與 `test_finalizer_path_conflicts.py`，涵蓋路徑矛盾、直接儲存、跨節點、歷史資料及混合錯誤。

### 12.2 現場診斷

上述測試都不涵蓋真實模型生成、SSH／PVE、正式資料庫或部署環境。現場驗證應追同一條鏈：`session_id -> source_file_id / analysis_revision -> proposal / blockers -> artifact_set / source_analysis_revision -> run / target evidence`，並逐項比對老師意圖與實際結果。

診斷時同時核對部署版本、工具參數及結果、`target_node_key`、collector、`missing_information`、`reason_code` 與 `script_ready`。若尚未產生 artifact，先追提案與核對；已有 artifact 且實際取證失敗，才追學生 VM 的檔案、工具與權限。紀錄不得包含憑證、SSH 密碼或學生檔案內容。
