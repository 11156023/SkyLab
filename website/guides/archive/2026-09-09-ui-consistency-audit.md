# 前端一致性與可用性稽核（2026-09-09）

針對 28 項回饋逐條查證，判定**是否成立**、指出**根因**、給出**方案與工作量**。

判定用語：

- **成立** — 已在程式碼中找到對應問題
- **部分成立** — 現象存在，但根因與描述不同，方案要跟著改
- **不成立** — 現況已非如此（通常是先前已修過）

工作量：**S** ≈ 半天內、**M** ≈ 1–2 天、**L** ≈ 3 天以上或需後端配合。

---

## 一、先看根因：七成的項目來自五個共用問題

逐條修會重複勞動。下面五項是多數回饋的共同來源，先處理它們，個別頁面的問題會一起消失。

### R1. 儲存按鈕沉在頁尾（影響 #16 #18 #19，以及配額／LDAP）

`settings.module.scss` 的 `.cardActions` 是 `justify-content: flex-end`，且**永遠是表單的最後一個元素**：

| 頁面 | 儲存按鈕行號 / 檔案總行數 | 卡片數 |
|---|---|---|
| 治理 | 195 / 215 | 8 |
| 資源排程 | 134 / 188 | 3 |
| 配額 | 345 / 554 | 2 |
| LDAP | 249 / 278 | 3 |

治理頁一次渲染 8 張卡、約 30 個欄位，按鈕在最底部、靠右、無 sticky。改一個開關要捲到底才能存。

**方案**：把 `.cardActions` 改成 sticky footer。**專案裡已經有這個實作可以直接抄** —— `ClassSetupPage.module.scss:99`：

```scss
.footer {
  position: sticky; bottom: $spacing-16; z-index: 50;
  display: grid; grid-template-columns: 150px 1fr 190px;
  align-items: center; gap: $spacing-16; padding: 12px $spacing-16;
  @include glass-surface($shadow: var(--shadow-md)); border-radius: $radius-12;
}
```

改 `settings.module.scss` 一處，五個設定頁同時受益。順帶加「尚未儲存」的 dirty 狀態提示。**S**

### R2. 表單型態三種混用（影響 #8 #11 #15）

同樣是「新增／編輯一筆設定」，全站有三種做法：

| 做法 | 使用頁面 |
|---|---|
| **Modal**（正確） | 網域管理、機器範本、班級建立、防火牆規則卡、終端機 |
| **頁內插入卡片** | PVE 連線（`ConnectionForm`，PveConnectionsPage.jsx:344）、IP 管理（`SubnetConfigForm`，IpManagementPage.jsx:214） |
| **整頁拓樸圖** | 防火牆頁（ReactFlow + ConnectionDialog） |

頁內插入的副作用很具體：表單一展開就把底下整張表推走，而且 IP 管理為了避免表單被洗掉，**編輯期間直接停掉自動刷新**（`useAutoRefresh(() => { if (!editing) load(true); })`）。

**方案**：統一走 Modal。樣式規範已定義四級寬度（420 / 640 / 1100 / 1280px），照用即可。**M**

### R3. 表格沒照自己的規範寫（影響 #14 #22 #25 #28）

`docs/2026-09-05-frontend-style-guide.md` 表格規則二寫明：

> `.table { @include table-base; table-layout: fixed; min-width: 1080px; }`

實際掃描結果：**11 個 stylesheet 用了 `@include table-base`，其中 0 個設 `min-width`**，也幾乎都沒有 `table-layout: fixed`：

```
ai-api-review / ai-monitoring / CourseOperations / RequestsPage
ResourceDetailPage / GpuMgmtPage / TemplatesPage / AuditPage
IpManagementPage / JobsPage / MonitoringPage
```

`table-wrap` mixin 有 `overflow-x: auto`，但**沒有 `min-width` 就永遠觸發不了橫向捲動** —— 表格會壓縮到 100% 寬度，欄位互相擠，`white-space: nowrap` 的狀態 badge 就溢出。這正是「狀態跑版」。

**方案**：逐一補 `table-layout: fixed` + `min-width` + `<colgroup>`。**M**

### R4. 版面規範被個別頁面破壞（影響 #1）

樣式規範：

> **頁面根容器（`.page`）一律滿寬**：不設 `max-width`、不使用 `margin: 0 auto` 置中……寬螢幕下不會出現「某些頁置中留白、某些頁滿寬」的落差。

違規掃描結果 3 個檔案，其中 `AccountSettingsPage`（單欄表單）是規範明列的例外，**`AdminDashboardPage.module.scss:5-7` 是真違規**：

```scss
.page { max-width: 1440px; margin-inline: auto; }
```

管理首頁因此比其他所有頁窄一截、置中，切頁時整個內容區會跳。**S**

### R5. 說明文字策略不一致（影響 #3 #12 #13 #18 #20 #21 #23）

同一份資訊在不同頁面用四種載體呈現，且沒有規則：

| 載體 | 例子 | 問題 |
|---|---|---|
| `title=` tooltip | Storage 的速度等級、使用者優先度 | 觸控裝置看不到、不 hover 就不存在 |
| `.fieldHint` 逐欄小字 | 治理 17 條、LDAP 6 條 | 密度過高，反而沒人讀 |
| `.cardDesc` 段落 | 配額 3 段長句 | 同一件事重複講三次 |
| 完全沒有 | 節點 Priority、Cloudflare 預設 DNS 目標、網址清單 | 關鍵欄位零說明 |

**方案**：訂一條規則——**欄位名稱講「是什麼」，hint 只在「值會影響什麼」時出現，卡片說明只講「這張卡的用途」**；重複的移到頁面說明或說明連結。**M**

---

## 二、逐項分析

### #1 首頁優先處理：比例不正確、缺少相關處理訊息

**成立（兩個問題）**

*比例*：見 R4，管理首頁是全站唯一違反滿寬規則的內容頁。另外 `.assistantHero` 是 `minmax(280px, 0.8fr) minmax(460px, 1.2fr)` 且 `padding: $spacing-32`，AI 助理區塊視覺重量遠大於上方的「優先處理」清單，但後者才是這頁的主要工作區。

*缺少訊息*：`buildAdminIssues()` 目前只涵蓋 6 類：警告、失敗任務、VM＋規格申請、批次佈建、AI API 申請、服務不可用。**漏了刪除申請與挖礦事件**——兩者都有獨立後端路由（`deletion_requests.py`、`mining_incidents.py`）與獨立頁面，卻不會出現在首頁待辦。

**方案**：`.page` 移除 max-width；「優先處理」移到 AI 區塊之上並提高視覺權重；`buildAdminIssues` 補兩類來源。**S**

### #2 下載 App 連線要更明顯

**成立**

全站只有一個入口：`ResourcesPage.jsx:593`，「我的資源」頁首的 `btnSecondary`，排在「申請資源」主按鈕旁邊。新使用者第一次登入落在首頁，看不到它；側欄沒有、首頁沒有、帳號設定沒有。

**方案**：三個補位——(a) 學生／教師首頁加一張「連線方式」卡（桌面版下載 + 使用說明）；(b) 側欄底部常駐一個下載項；(c) 資源詳情頁的連線區塊直接提供下載。至少做 (a)。**S**

### #3 我的資源上方「容量限制」不夠明確

**成立**

`QuotaUsageBar` 渲染 4 條裸的進度條，**沒有卡片標題、沒有任何說明**。使用者看到「CPU 3 / 8 cores」但不知道：

- 這是什麼上限（個人配額）
- 誰決定的、超過會怎樣（申請時被 409 擋下）
- **班級／課程機器不算在內**（後端 `quota_service._owned_vmids` 只算 `allocation_scope == "personal"`）—— 這點完全沒告知，學生會覺得數字對不上
- `max === 0` 顯示為「無限制」，但沒說明為什麼有些項目無限制

另有一個小問題：`if (!data) return null` —— API 失敗時整條靜默消失，使用者不知道有這個東西。

**方案**：加標題「我的資源配額」＋一行說明（含「課堂機器不計入」）；接近上限時顯示「如需更多請提出規格調整申請」並連到申請頁；載入失敗顯示佔位而非消失。**S**

### #4 申請審核只需要標示通過

**部分成立**

`useStatusMeta()` 定義了 **12 種狀態**：pending / approved / rejected / cancelled / expired / running / completed / failed / approved_awaiting_apply / applying / applied / apply_failed。

但審核者的決策只有兩種（准 / 不准），後面 8 種是**申請通過之後的執行進度**，屬於申請人與資源頁的事。列表把它們全部混在同一個 badge 系統裡，審核者要分辨「applying」和「approved_awaiting_apply」對審核毫無幫助。

**方案**：列表 badge 收斂成 待審／已通過／已駁回 三種；執行進度改成次要資訊（灰字或右側細節面板），不佔主 badge 位置。**S**

### #5 審核類需要身分

**成立，需後端配合**

`normalizeVmRequest` / `normalizeSpecRequest` / `normalizeDeletionRequest` 三個都只帶 `user`（姓名或 email）與 `userSubtext`（email 或 user_id）。**沒有任何地方顯示角色**。

後端 `VMRequestPublic` 有 `user_email`、`user_full_name`，**沒有 role 欄位**。審核 GPU 或大規格申請時，申請人是學生還是教師是關鍵判斷依據。

**方案**：後端三個 Public schema 加 `user_role`；前端在申請人名稱旁加角色 badge（沿用 `AdminPage.roleAdmin/roleTeacher/roleStudent` 既有翻譯）。**M**

### #6 AI API 按鈕意義不明、排版怪、訊息太多

**成立（三個都是）**

*按鈕意義不明* —— 憑證卡有 5 個按鈕，其中：

- 兩個只有 icon + 「Base URL」／「API Key」，**沒有動詞**，看不出是複製
- **「刷新」是真正的問題**：`actionRefresh` = 「刷新」，但它做的是 `doRotate`（重新產生金鑰、舊金鑰立即失效）。中文 UI 的「刷新」預設語意是重新載入。確認框寫「刷新後舊金鑰會失效」——**一個破壞性動作被貼上非破壞性的標籤**

*訊息太多* —— 頁面 = 3 張統計卡 + 4 個分頁 + 申請表單；「我的用量」分頁再放 2 個 panel × (3 統計卡 + 2 個明細列表)。單頁承載 4 種不同任務。

**方案**：「刷新」改成「重新產生金鑰」並套用 danger 樣式；複製鈕補上動詞；統計卡收進分頁內或移除；申請表單改 Modal。**M**

### #7 我的用量要折線圖

**成立，需後端配合**

現況只有累計數字（總呼叫次數 / 輸入 Tokens / 輸出 Tokens）＋ 依模型／呼叫類型的明細列表，**完全沒有時間維度**。

前端條件很好：**recharts 已經是專案相依套件**，而且已有現成的 `components/RrdChart/RrdChart.jsx`（AreaChart 包裝，資源監控頁與資源詳情共用）可以直接套。

卡在後端：`UsageStatsResponse` 只有 `total_requests / total_input_tokens / total_output_tokens / by_model / start_date / end_date`，**沒有逐日分桶**。

**方案**：`UsageStatsResponse` 與 `TemplateUsageStatsResponse` 各加一個 `daily: list[{date, requests, input_tokens, output_tokens}]`；前端用 `RrdChart` 呈現。**M**

### #8 防火牆表單不一致

**成立，且是全站最大的一致性落差**

同一份防火牆規則資料，有兩套完全不同的心智模型：

| 入口 | UI | 使用者要理解的概念 |
|---|---|---|
| 網路 → 防火牆 | ReactFlow **拓樸圖**，拉線建立連線，`ConnectionDialog` | 來源節點 → 目標節點 |
| 資源詳情 → 進階設定 | 傳統**規則表單** Modal（`FirewallCard`） | type / action / proto / port / source / dest |

兩邊寫進同一個後端，但欄位集合與操作邏輯不同。使用者在一邊學到的東西到另一邊完全用不上。

**方案**：確認哪一套是主要入口（建議拓樸圖給管理員看全域、規則表單給單機），另一套明確定位為「進階／單機視圖」，並讓兩者共用同一個規則編輯元件。**L**

### #9 使用者管理需要分頁；重設密碼是否拔掉

**分頁：成立，而且是資料正確性問題**

`AdminPage.jsx:254`：

```js
const res = await UsersService.list({ limit: 100 });
```

**寫死 100 筆、沒有任何分頁 UI**。後端 `read_users(skip, limit)` 支援分頁。校園規模超過 100 人時，第 101 位之後的使用者在管理頁上**根本不存在**——不只是難用，是查不到也改不到。

好消息：`AuditPage` 已經實作了完整分頁（`PAGE_SIZE`、`skip/limit`、`paginationInfo`、上下頁按鈕，AuditPage.jsx:52-66、278-293），**照抄即可**。

**重設密碼：建議保留，但要加條件**

編輯表單有「新密碼」欄位（`fieldNewPassword`，送 `payload.password`）。專案有 LDAP 登入（`ldap_auth_service.login_ldap`），對 LDAP 來源的帳號設定本地密碼沒有意義、還可能造成「明明改了卻登不進去」的困惑。

建議不是拔掉，而是：本地帳號保留；LDAP 帳號把欄位停用並顯示「此帳號由 LDAP 管理」。這需要 User model 有帳號來源標記——目前沒有，要一併補。**M**

### #10 IP 管理需要分頁

**成立**

`visible.map(...)` 一次渲染全部 allocations，沒有分頁也沒有虛擬捲動。一個 /24 網段就是 254 列，/16 是六萬多列。

順帶發現：`new Date(a.allocated_at).toLocaleString("zh-TW")` —— **寫死語系**。全站共 16 處這種寫法，日文／英文介面下仍顯示台灣格式。

**方案**：套 AuditPage 的分頁模式；日期改用 i18n 感知的格式化（建議抽一支 `utils/formatDate.js`，順手把 16 處一起收斂）。**M**

### #11 編輯子網設定需要跳表單

**成立** —— 見 R2。`SubnetConfigForm` 插在統計列與工具列之間（IpManagementPage.jsx:214），展開時把整張 IP 表推到畫面外，且編輯期間自動刷新被停用。**S**

### #12 Cloudflare 連線設定說明不夠清楚

**成立**

整個設定 Modal 只有一句說明：

> `configModalDesc` = 「API Token 需具備 Zone / DNS 編輯權限；Token 只寫入不回讀。」

缺的東西很具體：

- **沒說去哪裡拿** Account ID 與 API Token（沒有連到 Cloudflare 後台的連結，也沒說要建哪種 Token）
- **「預設 DNS 目標類型／值」兩個欄位零說明**——只有 placeholder。這兩個值決定系統自動建立對外網址時指向哪裡，填錯會讓所有學生網址失效
- **沒有連線測試按鈕**，只能存了看 `last_verified_at` 有沒有變

**方案**：補 Token 建立指引（附外部連結與所需權限清單）；兩個預設目標欄位加 hint 說明用途與典型值；加「測試連線」按鈕。**S**

### #13 網址清單是什麼

**成立——命名本身就是問題**

同一個東西有兩個名字，而且都沒解釋：

- 分頁標籤：`DomainPage.tabReverseProxy` = 「對外網址」
- 面板標題：`ReverseProxyPage.listTitle` = 「網址清單」（**沒有任何說明文字**）

它實際是「把學生機器上的服務，透過反向代理／Tunnel 對外公開的網址對照表」。使用者問「網址清單是什麼」完全合理。

**方案**：兩處統一叫「對外網址」；面板標題下加一行說明：「把課程機器上的服務（例如網頁、資料庫管理介面）指派一個對外網址，學生不必連 VPN 就能開啟。」**S**

### #14 閘道 VM 比例修正、微跑版

**成立**

`GatewayPage.module.scss:70` 在 lg 斷點：

```scss
grid-template-columns: minmax(0, 2fr) minmax(0, 3fr);
grid-template-areas: "status editor" "logs editor";
```

編輯器跨兩列、佔 3fr；狀態與日誌疊在 2fr 欄。配上 `align-items: start` 與日誌區的 `max-height: 320px`，兩欄高度各走各的，短的那邊留一大塊空白——就是「微跑版」的來源。

另外 `.formGrid` 在此頁是 `2fr 1fr 1fr`，而 `settings.module.scss` 是 `repeat(2/3, minmax(0,1fr))`，同類表單兩種比例。

**方案**：`align-items: stretch` 讓兩欄等高，或把日誌移到編輯器下方改成上下堆疊；`.formGrid` 統一比例。**S**

### #15 PVE 連線新增需要跳表單

**成立** —— 見 R2。`ConnectionForm` 是 `<form className={styles.card}>`，插在連線列表與節點提示之後（PveConnectionsPage.jsx:344）。這張表單欄位很多（名稱／Host／Port／使用者／密碼／CA／pool／storage／網段／預設節點），插在頁內會把頁面撐得很長。**S**

### #16 資源排程邏輯修正、儲存按鈕不明顯

**成立（儲存按鈕見 R1；邏輯問題已於本次修掉一半）**

*已修*：`ProxmoxConfigUpdate` 是**全量取代** schema，前端 `UPDATE_KEYS` 漏列的欄位存檔時會被重置成預設值。三個磁碟爭用參數原本就是這樣被默默改掉的。已於 commit `00b27fad` 修復，並在 `UPDATE_KEYS` 上方補了警告註解。

*仍待處理的邏輯問題*：這一頁把三種不同性質的東西放在同一個儲存按鈕底下——

1. **放置演算法權重**（超配比、峰值裕度、負載權重、資源權重）：改了立即影響下一次 VM 落點
2. **排程開機參數**（批次大小、間隔、提前量）：下一個排程週期生效
3. **練習時段與到期提醒**（練習時數、提醒分鐘、到期提醒）：其實是使用者體驗設定，跟放置演算法無關

而且頁面**送出了 12 個自己不顯示的連線欄位**（host / user / storage / pool …）只為了「原樣回送 singleton」。這是 PUT 用全量取代 schema 造成的設計債。

**方案**：`ProxmoxConfigUpdate` 全欄位改 `X | None = None` + `exclude_unset` 局部更新，前端就不必回送不相干欄位；三組參數拆成三張卡並各自標註生效時機。**M**

### #17 資源排程 vs 治理

**不是問題，是需要說清楚的分工**（已於前次對話完整分析，此處摘要）

| | **配額** | **資源排程** | **治理** |
|---|---|---|---|
| 管什麼 | 能不能拿、拿多少 | 放哪個節點、何時開機 | 開出去後怎麼管、何時回收 |
| 性質 | 准入 gate | 引擎調校旋鈕 | 生命週期政策 |
| 資料表 | `quota_config` + `resource_quotas` | `proxmox_config` | `governance_config` |
| 時機 | 申請當下擋（409） | 佈建與排程時 | 事後背景掃描 |

三者是三個獨立 singleton，彼此不引用。**建議**：在三頁的 subtitle 各加一句話點出分工，並互相連結，而不是合併。

本次已順帶清掉排程側的死設定（`placement_strategy`、`placement_search_depth`、`placement_search_max_reassignments` 三欄全庫零讀取點，commit `00b27fad` 移除）。

### #18 治理：儲存按鈕不明顯、太多小註解、跑版

**成立**

- *儲存按鈕*：見 R1。8 張卡、約 30 個欄位，按鈕在第 195 行（全檔 215 行）
- *太多小註解*：**17 條 `*Hint`**，平均每張卡 2 條以上。開關下面掛小字、數字欄位下面也掛小字，密度高到讀者會整片略過
- *跑版*：`.checkRow` 是 `display: inline-flex` + `align-self: flex-start`，而 `.checkRow + .checkRow { margin-top: -$spacing-8 }` 這條負邊距只在**連續兩個開關**時成立。反挖礦區塊是「兩個開關 + 三個數字欄位」，開關與 `.formGrid` 之間的間距就與其他卡片不一致

**方案**：sticky 儲存列；hint 收斂到只保留「值會影響什麼」的那幾條（估計可砍掉一半）；開關統一包進 `.toggleGrid` 取代負邊距。**S**

### #19 班級管理儲存按鈕不明顯

**部分成立——根因不是沒有 sticky，是按鈕會消失**

`ClassSetupPage` **已經有 sticky footer**（`.footer { position: sticky; bottom: $spacing-16 }`）。真正的問題在 ClassSetupPage.jsx:375：

```jsx
{step === 3 && !templateId
  ? <em className={styles.footerHint}>…請先選擇範本…</em>
  : <button className={styles.btnPrimary}>儲存並繼續</button>}
```

第 3 步還沒選範本時，**主按鈕整個被一段提示文字取代**——使用者看到的是「按鈕不見了」。而且 footer 是 `grid-template-columns: 150px 1fr 190px`，第三欄固定 190px，塞進較長的提示文字會擠壓變形。

**方案**：按鈕保留但 `disabled`，提示改用 `title` 或按鈕下方一行小字；footer 第三欄改 `minmax(190px, auto)`。**S**

### #20 配額太多小註解

**部分成立——是「太長且重複」而非「太多」**

實際只有 3 段說明，但三段都在講同一件事（覆寫優先於全域）：

- `globalQuotaDesc`：「沒有個人覆寫的使用者一律套用這組上限。調整只影響之後的新增與擴容，不會回頭處理既有資源。」
- `overridesDesc`：「為特定使用者設定專屬上限，優先於全域預設值。」
- `createHint`：「欄位已帶入目前的全域預設值，改成這位使用者專屬的上限即可。勾選「無限制」代表該項目不設上限。」

**方案**：優先順序講一次就好（放頁面 subtitle）；卡片說明各留一句；「無限制」的解釋靠 checkbox 標籤本身，不必另寫。**S**

### #21 LDAP：英文不合適、太多小註解、分太多塊

**成立（三個都是）**

- *英文*：`LdapPage.jsx:161` 的 `<span>Bind DN</span>` 是**硬編碼字串，完全沒走 i18n**，三種語言都顯示英文。其餘欄位則是半中半英的混搭：「伺服器 URI」「Email 屬性」「管理員群組 DN（選填）」
- *太多小註解*：6 條 `fieldHint` + 3 段 `cardDesc`，一頁 9 段說明文字
- *分太多塊*：3 張卡（登入 / 服務帳號與使用者搜尋 / 帳號建立與角色對映）承載一份設定，每張卡之間 24px，整頁被切得很碎

**方案**：`Bind DN` 走 i18n（中文可作「繫結帳號 DN」並保留英文原詞在括號）；術語統一策略——LDAP 專有名詞保留英文、其餘全中文；三張卡合成一張、內部用 `.sectionTitle` 分隔（`settings.module.scss` 已有這個樣式且會自動處理首個區塊不加分隔線）。**S**

### #22 節點管理跑版

**成立**

`.nodeRow` 是 `display: flex; flex-wrap: wrap`，一列裝著：節點資訊（`flex: 1; min-width: 200px`）、上線 badge、啟用 checkbox、編輯按鈕。

點「編輯」時，**一顆按鈕被換成 5 個元素**（3 個 input + 儲存 + 取消），該列瞬間 wrap 成 2–3 行，其他列的對齊全被破壞。而且因為是 flex 不是 grid，各列的 `host:port · Priority N` 本來就對不齊。

**方案**：改 grid 固定欄位；編輯改 Modal（同 R2），列本身不變形。**S**

### #23 節點管理與 Storage 欄位說明不清楚

**成立**

| 欄位 | 現況 | 缺什麼 |
|---|---|---|
| 節點 `Priority` | 只在 meta 顯示「Priority 0」，編輯時只有 `placeholder="Priority"` | 數字大代表優先還是延後？範圍？0 是什麼意思？ |
| 節點 `Host/Port` | 可編輯，無說明 | 為什麼要改？改了影響什麼？ |
| Storage 速度等級 | 裸 `<select>`，只有 `title` tooltip | 影響什麼？系統怎麼用？ |
| Storage 使用者優先度 | 裸數字 `<input>`，`title` = 「使用者優先度」 | **tooltip 只是把欄位名重講一次，零資訊** |

兩者都直接餵進放置決策（`get_node_priorities`、storage 挑選），但管理員無從得知調哪邊會發生什麼。而 `title` 屬性在觸控裝置上根本看不到。

**方案**：加可見的欄位標籤與一行 hint（說明方向性與生效時機）；tooltip 不作為唯一說明載體。**S**

### #24 資源監控：分太多塊、排除非重要內容、運行狀態太擠、Top 5

**成立**

- *分太多塊*：一頁堆了 4 張統計卡 + 運行狀態卡 + 節點用量表 + CPU Top 5 + 記憶體 Top 5 + 警告清單 + 挖礦事件面板，**7 個以上區塊**
- *運行狀態太擠*：`.overviewTop` 把 3 條 `statusLine` 加一顆 icon 塞進一張卡
- *Top 5 太擠*：`.topGrid` 在 lg 是 `1fr 1fr`，兩張 Top 表並排，**每張是 5 欄表格**（VMID / 名稱 / 節點 / 類型 / 用量）擠在半個版面寬。名稱欄被壓到幾乎不可讀。而且 `.table` 沒有 `min-width`（見 R3），所以不會出現橫向捲動，只會硬擠

**方案**：Top 5 改上下堆疊或縮成 3 欄（VMID+名稱合併 / 節點 / 用量）；挖礦事件與警告收進分頁；運行狀態併入頂部統計列。**M**

### #25 背景任務：篩選風格不一樣、狀態跑版、欄位確認

**成立（三個都是）**

- *篩選風格*：JobsPage 用**兩個原生 `<select>`**，而 `SegmentedControl` 這個共用元件已被 6 個頁面採用（AI 金鑰、AI 審核、AI 監控、課程環境、批次審核、申請審核）。JobsPage 是唯一的例外
- *狀態跑版*：7 欄表格（任務／類型／狀態／進度／建立／更新／申請人），`.table` 只 `@include table-base`，**沒有 `table-layout: fixed` 也沒有 `min-width`**。見 R3——`overflow-x: auto` 因此永遠不觸發，欄位互擠，`white-space: nowrap` 的狀態 badge 溢出
- *欄位確認*：「建立時間」與「更新時間」兩欄並列但多數情況差異極小；「申請人」只給 email（`j.user_email`），沒有姓名

**方案**：篩選改 `SegmentedControl`；表格補 `fixed` + `min-width` + `<colgroup>`；兩個時間欄合併成「建立 / 更新」單欄，空出的寬度給申請人顯示姓名。**S**

### #26 建立學習環境跳出「尚不能儲存：請先輸入環境名稱」

**部分成立——那個 toast 已經沒有了，但換成了更糟的無聲失敗**

全 locale 檔搜尋「尚不能」「請先輸入」都找不到這句，`CourseTemplateEditorPage.needNameReason` 也不存在——這個 toast 在先前的重構中已被移除。

現在的行為（`validateBeforeSave`，CourseTemplateEditorPage.jsx:589）：

```js
if (!template.name.trim()) {
  setInvalidField("name");
  changeTab("basic");            // ← 把使用者傳送到另一個分頁
  setTimeout(() => focusInvalidField(nameRef.current), 60);
  return false;                  // ← 沒有任何訊息
}
```

問題在於**儲存按鈕在「機器配置」分頁，必填的名稱欄位在「基本」分頁**。使用者在機器配置按「儲存草稿」，畫面無預警跳到另一個分頁，欄位只有一個紅框，**沒有任何文字說明為什麼**。`aria-invalid` 有設但沒有 `aria-errormessage`，螢幕閱讀器使用者完全得不到資訊。

**方案**：欄位下方加 inline 錯誤訊息（不是 toast）；更根本的是把儲存／發布按鈕提到分頁列旁邊的共用工具列，讓它不隸屬於任何單一分頁。**S**

### #27 審核頁上方方框數字的必要性

**成立——建議移除**

四張統計卡（總數 / 待審 / 已核准 / 已駁回）**正下方就是同樣三個選項的 SegmentedControl**（待審 / 已核准 / 已駁回）。數字與分頁一一對應，資訊完全重複，卻多佔一整列高度。

更糟的是「總數」會誤導：`stats.total = allRequests.length`，包含**刪除申請**，但刪除申請的 `reviewStatus` 被寫成 `"other"`（`normalizeDeletionRequest`），而 `filterByTab` 只認 pending / approved / rejected——

> **刪除申請被抓進來、被算進總數，卻在三個分頁裡都看不到。**

這是一個實際的功能缺陷，不只是版面問題（同時對應 #1 的「缺少相關處理訊息」）。

**方案**：移除統計卡，數字改成分頁標籤上的角標（`待審 3`）；刪除申請要嘛給它自己的分頁與審核動作，要嘛就不要載入。**S**

### #28 金鑰管理跑版

**成立**

`AiApiKeysPage.module.scss` 兩處固定尺寸在 i18n 下會壞：

- `.detailTitle`：`font-size: 24px` + `white-space: nowrap` + `text-overflow: ellipsis`——金鑰名稱稍長就被截斷，而這一頁的主要識別資訊就是名稱
- `.detailItem`：`grid-template-columns: 112px minmax(0, 1fr)`——**標籤欄固定 112px**。中文標籤剛好，英文（"Created at" / "Last used"）與日文會換行或溢出

主從版面 `minmax(300px, 0.8fr) minmax(0, 1.2fr)` 搭配 `grid-template-rows: minmax(0,1fr)` 與 `overflow-y: auto`，在內容少時右側會留大片空白。

**方案**：`.detailTitle` 允許兩行（`-webkit-line-clamp: 2`）；`.detailItem` 標籤欄改 `minmax(112px, max-content)`；三種語言各驗一次。**S**

---

## 三、建議施作順序

### 第一批：共用修正，改一處多頁受益（約 2 天）

| 項目 | 涵蓋回饋 |
|---|---|
| R1 sticky 儲存列（改 `settings.module.scss`） | #16 #18 #19 #20 #21 |
| R4 移除 AdminDashboardPage 的 max-width | #1 |
| R3 表格補 `table-layout: fixed` + `min-width` | #14 #22 #25 #28 |
| 抽 `utils/formatDate.js` 收斂 16 處寫死語系 | #10 |

### 第二批：功能缺陷（約 3 天）

| 項目 | 說明 |
|---|---|
| 使用者管理分頁（#9） | **資料查不到**，第 101 位之後的使用者管理不到 |
| 刪除申請在審核頁不可見（#27 / #1） | 抓了、算進總數、看不到 |
| 首頁待辦補刪除申請與挖礦事件（#1） | 有頁面有 API，就是沒進待辦 |
| 「刷新」改名為「重新產生金鑰」（#6） | 破壞性動作標籤錯誤 |
| IP 管理分頁（#10） | /24 就是 254 列 |

### 第三批：說明與命名（約 2 天）

#3 配額說明、#12 Cloudflare 指引、#13 網址清單正名、#23 節點／Storage 欄位說明、#21 LDAP 術語統一、#20 配額文案收斂、#26 inline 錯誤訊息。

### 第四批：表單型態統一（約 3 天）

#11 #15 改 Modal；#22 節點編輯改 Modal；#4 狀態收斂；#24 監控版面重整；#25 篩選改 `SegmentedControl`。

### 第五批：需要設計決策，先討論再動工

| 項目 | 要決定什麼 |
|---|---|
| #8 防火牆兩套 UI | 哪一套是主要入口？能否共用規則編輯元件？ |
| #6 AI API 頁資訊架構 | 四個分頁 + 統計卡 + 申請表單要怎麼重組？ |
| #7 用量折線圖 | 後端逐日分桶要存多久？粒度到日還是小時？ |
| #9 重設密碼 | 要不要先加帳號來源（本地 / LDAP）標記？ |
| #16 排程頁拆卡 | `ProxmoxConfigUpdate` 改局部更新的相容性 |
| #17 三頁分工 | 只加說明與互連，還是調整導覽層級？ |

---

## 附錄：本次查證用到的檔案

**版面規範**：`docs/2026-09-05-frontend-style-guide.md`（頁面滿寬規則、表格規則二、Dialog 四級寬度）

**共用樣式**：`frontend/src/assets/styles/_mixins.scss`、`frontend/src/pages/system/settings/settings.module.scss`

**可直接複用的既有實作**：

- 分頁 → `AuditPage.jsx:52-66, 278-293`
- sticky footer → `ClassSetupPage.module.scss:99`
- 篩選控制 → `components/SegmentedControl`
- 折線圖 → `components/RrdChart/RrdChart.jsx`（recharts 已在相依中）
- Modal → `TemplateFormDialog` / `ClassCreateDialog` / `ConfirmDialog`
