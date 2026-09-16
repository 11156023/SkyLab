# SkyLab 前端樣式規範 — 需求填寫範本

- 日期：2026-09-16（Asia/Taipei）
- 狀態：範本，待前端填寫
- 對應文件：`docs/2026-09-05-frontend-style-guide.md`（現行規範）、`docs/2026-09-09-ui-consistency-audit.md`（一致性稽核）
- 填寫人：（前端）
- 填寫日期：

> **這份文件怎麼用**
>
> 1. 每一節都分成「現況」與「前端要求」。現況是從現行規範與 `_variables.scss`／`_themes.scss`／`_mixins.scss` 抄過來的，只供對照，不用改。
> 2. 「前端要求」請直接寫在 `📝` 標記後面，或填進表格的空白欄。沒有意見就在該節勾 `[x] 沿用現況`，不要留空，留空會被當成「還沒看」。
> 3. 凡是要動 `_variables.scss`、`_themes.scss` 的，除了在該節寫要求，**也要登記到最後的「變數變更申請表」**，因為那兩個檔案的改動會影響全站。
> 4. 填完後由維護者把內容併回現行規範，這份範本本身不會變成規範。
>
> 標記說明：`📝` 待填、`❓` 需要前端決定的問題、`⚠️` 目前已知有落差或違規的地方。

---

## 0. 總則

### 現況

- 樣式一律用 SCSS Modules，檔案與元件同名同目錄（`XxxPage.jsx` + `XxxPage.module.scss`）。
- `variables` 與 `mixins` 由 vite 全域注入，元件內不再手動 `@use`。
- **禁止**在元件 SCSS 內新增 SCSS 變數或 CSS 自訂屬性；缺什麼先討論是否加進全域。
- 顏色一律 `var(--color-*)`，不寫死 HEX（終端機底色、logo 白底、Monaco vs-dark 編輯器為明列例外）。
- 介面語言為繁體中文。

### 前端要求

- [ ] 沿用現況
- 📝 要新增或收緊的總則：
- ❓ 「禁止在元件內新增變數」是否要放寬為「允許元件私有的 `--local-*` 變數，但不得跨檔引用」？
- ❓ 是否要求每個 `.module.scss` 開頭固定一段註解（用途、對應元件、例外說明）？

---

## 1. 顏色系統（`_themes.scss`）

### 現況

| 群組 | 變數 | 用途 |
|------|------|------|
| 背景 | `--color-bg-base` | 頁面底色 |
| 背景 | `--color-bg-gradient-blue/yellow/green` | 預設三色暈染 |
| 表面 | `--color-surface` | 卡片、面板 |
| 表面 | `--color-surface-glass` / `-border` | 毛玻璃表面與邊框 |
| 表面 | `--color-sidebar` / `--color-sidebar-glass` | 側欄 |
| 品牌 | `--color-primary` / `-dark` / `-light` | 主色（`-dark` 僅作底色） |
| 品牌 | `--color-primary-on-surface` | 品牌色文字／邊框，亮暗皆 AA |
| 文字 | `--color-text` / `-primary` / `-secondary` / `-muted` / `-on-primary` | |
| 邊框互動 | `--color-border` / `--color-divider` / `--color-hover` / `--color-row-hover` / `--color-overlay` | |
| 陰影 | `--shadow-sm` / `-md` / `-lg` / `-glass` | |

狀態色五種，黃橙只給「待審核」，警示與錯誤一律紅色，**沒有** `--color-warning`：

| 變數 | 亮 | 暗 | 語意 |
|------|----|----|------|
| `--color-success` | `#28a745` | 同 | 正常、運行中 |
| `--color-info` | `#2b4d98` | `#89a5e0` | 進行中、一般標記 |
| `--color-pending` | `#d97706` | `#f59e0b` | 待審核、草稿、排程中 |
| `--color-danger` | `#dc3545` | 同 | 錯誤、失敗、危險操作（hover 用 `--color-danger-dark`） |
| `--color-status-neutral` | `#6b7280` | `#9ca3af` | 已停止、disabled |

風格變體：`body[data-style="white"|"black"|…]` 只改表面實色與 `--glass-backdrop-filter`；背景花色由 `body[data-bg]` 切換，色碼由 ThemeContext 從基準色衍生。

### 前端要求

- [ ] 沿用現況
- 📝 要新增的色彩變數（名稱／亮色值／暗色值／用途）：

| 變數名 | 亮色值 | 暗色值 | 用途 | 為什麼既有變數不夠 |
|--------|--------|--------|------|--------------------|
| | | | | |

- 📝 要調整的既有變數（填「變數變更申請表」，這裡只寫理由）：
- ❓ 是否維持「不設 `--color-warning`」？若要加，警示與錯誤的分界是什麼？
- ❓ 狀態色是否要補「hover／active」階（例如 `--color-success-dark`），還是統一用 `color-mix()` 現算？
- ❓ 圖表（recharts）的序列色要不要獨立一組 `--color-chart-1..n`，還是沿用狀態色？
- ❓ 對比度底線：正文與次要文字各要求 WCAG AA 幾比幾？

---

## 2. 尺寸級距（`_variables.scss`）

### 現況

| 類別 | 級距 |
|------|------|
| 間距 | 4 / 8 / 16 / 24 / 32 / 48 px |
| 字級 | 10（限資料密集區）/ 12 / 14 / 16 / 18 / 24 / 28 / 32 px |
| 字重 | 400 / 500 / 700 |
| 行高 | `$line-height-base: 1.6`，標題 1.3 |
| 圓角 | 8 / 12 / 16 px、`$radius-pill: 999px` |
| 動畫 | `$transition-base: 0.2s ease`、`$transition-slow: 0.3s ease` |
| 斷點 | sm 576 / md 768 / lg 992 / xl 1200，側欄切換 1024 |
| 版面 | header 60、sidebar 260（收合 72）、content padding 40、container 1200 |
| 字型 | `"Noto Sans TC", "Helvetica Neue", Arial, sans-serif` |

### 前端要求

- [ ] 沿用現況
- 📝 要新增的級距（例如 `$spacing-12`、`$font-size-20`）與理由：
- 📝 要刪除或合併的級距：
- ❓ 12px 以下的字級是否全面禁止？現行只允許資料密集區用 10px。
- ❓ 圓角級距要不要分「控制項 8 / 卡片 16 / Dialog ?」固定對應，還是由頁面自選？
- ❓ 等寬字型（terminal、log、IP、VMID）是否要定義 `$font-family-mono`？目前各頁自行寫。
- ❓ 中英文混排是否要求字型包含英文 fallback 順序調整？

---

## 3. Mixin（`_mixins.scss`）

### 現況

| Mixin | 用途 |
|-------|------|
| `flex-center` / `flex-between` / `flex-column` | Flex 排版 |
| `text-truncate` / `text-clamp($lines)` | 文字截斷 |
| `container` | 置中容器 |
| `respond-to(sm|md|lg|xl)` | mobile-first 斷點；`respond-below` 給既有 desktop-first 版面收斂用 |
| `glass-surface($shadow, …)` | 毛玻璃，濾鏡走 `var(--glass-backdrop-filter)` |
| `badge-base` | Badge 基底 |
| `form-field` / `form-control` / `form-control-invalid` | 標準表單欄位，控制項高度 36px 與按鈕對齊 |
| `table-wrap` / `table-base` / `table-th` / `table-tr` / `table-tr-clickable` / `table-td` | 標準列表表格 |

### 前端要求

- [ ] 沿用現況
- 📝 要新增的 mixin（名稱／參數／要取代哪些頁面重複寫法）：

| Mixin 名 | 參數 | 輸出什麼 | 目前重複出現在哪些檔案 |
|----------|------|----------|------------------------|
| | | | |

- 📝 要修改的既有 mixin 與理由：
- ❓ 按鈕（`.btnPrimary` / `.btnSecondary` / 危險按鈕）目前沒有 mixin，各頁重抄，是否要收成 `button-primary` 等？
- ❓ Dialog 遮罩與內容框是否要收成 `dialog-overlay` / `dialog-panel` mixin？
- ❓ 空狀態、載入骨架是否需要 mixin 或改成共用元件（見第 8 節）？

---

## 4. 命名與檔案慣例

### 現況

- 類別名 camelCase；變體用底線 `_`（`.badge_success`），不用 BEM 的 `--`。
- 後綴：`Out`（離場動畫）、`Active`（選中）、`Disabled`（優先用 `:disabled`）。
- Icon 一律 `MIcon`（material-icons filled），禁止 inline SVG、emoji、其他 icon 庫。

### 前端要求

- [ ] 沿用現況
- 📝 要補充的命名規則（例如 `.page` / `.section` / `.toolbar` 等頁面骨架類別是否固定命名）：
- ❓ 同一元件多個變體時，`_` 變體是否限制只允許一層（`.badge_success` 可以、`.badge_success_small` 不行）？
- ❓ 共用元件的 `.module.scss` 是否要禁止 `:global`？現有 `:global(body.dark) &` 用法要不要保留？
- ❓ Icon 尺寸是否要收成固定幾級（16 / 20 / 24）？

---

## 5. 頁面版面（Layout）

### 現況

- `.page` 一律滿寬，不設 `max-width`，不置中；只有單欄表單頁可限寬（例：640px）。
- 標準寫法：`flex-column` + `gap: $spacing-24` + `padding: $spacing-8 $spacing-16`，md 以上 `padding: 0`。
- 已有共用 `components/PageHeader`（props：`eyebrow`、`title`、`subtitle`、`leading`、`children`），但規範沒寫哪些頁必須用、右側動作怎麼放。
- ⚠️ 稽核 R4：有頁面自行破壞版面規範（首頁比例、節點管理跑版）。

### 前端要求

- [ ] 沿用現況
- 📝 頁面標題列（標題、說明、右側主要動作按鈕）的固定結構：
- 📝 區塊（section）之間的間距、區塊標題字級：
- 📝 卡片網格的欄數與斷點對應（例如 sm 1 欄／md 2 欄／xl 3 欄）：
- ❓ **儲存按鈕位置**（稽核 R1，影響治理、班級管理、排程、配額、LDAP）：固定在頁首右上、sticky 頁尾、還是每個區塊各自一顆？
- ❓ 設定型頁面是否統一「上方 tabs，下方單一表單，一顆儲存」？
- ❓ 側欄收合狀態下的內容區 padding 是否不同？

---

## 6. 元件慣例

### 6.1 卡片

現況：`glass-surface` + `$radius-16` + `flex-column` + `overflow: hidden`。

- [ ] 沿用現況
- 📝 卡片內 padding、標題列、頁尾動作列的固定寫法：
- ❓ 卡片是否分「玻璃／實色／外框」三種，各用在哪？

### 6.2 按鈕

現況：`.btnPrimary` / `.btnSecondary` 高度 36px；hover／active／disabled 由 `_reset.scss` 給統一 transition；危險操作 hover 用 `--color-danger-dark`。

- [ ] 沿用現況
- 📝 按鈕層級（primary / secondary / ghost / danger / link）與各自的用途邊界：
- 📝 尺寸級距（是否需要 small 28px、large 44px）：
- 📝 icon 按鈕（只有圖示）的最小可點面積與 aria-label 要求：
- ❓ 同一列可以同時出現幾顆 primary？

### 6.3 表單

現況：`form-field` / `form-control` / `form-control-invalid` mixin；欄位排列寫在頁面 grid；起迄值算一個欄位。
⚠️ 稽核 R2：表單型態三種混用（inline 展開、Modal、獨立頁），#11 #15 #22 已建議改 Modal。

- [ ] 沿用現況
- 📝 **表單型態決策規則**（什麼情況用 inline、Modal、獨立頁）：
- 📝 必填標記、欄位說明文字、inline 錯誤訊息的位置與樣式：
- 📝 Select / Checkbox / Radio / Toggle / 日期時間選擇器的統一外觀（目前只有 input/select/textarea 走 mixin）：
- ❓ 送出前驗證失敗：只標紅欄位、還是同時 toast（稽核 #26 反映 toast 太吵）？
- ❓ 表單是否要求「髒狀態未儲存離開」提示？

### 6.4 表格

現況：`table-*` mixin 組；RWD 一律容器橫向卷動，不轉卡片；整列 hover 只給可點的列；列數會變的表格用 `table-layout: fixed` + `<colgroup>`；儲存格圖示要帶文字沒有的資訊。
⚠️ 稽核 R3：多頁沒照規範寫（#14 #22 #25 #28）。

- [ ] 沿用現況
- 📝 表頭固定（sticky）與否、分頁元件外觀、每頁筆數選項：
- 📝 表格內操作欄（按鈕群）的位置與最多顆數：
- 📝 空表格、載入中的呈現（見第 8 節）：
- ❓ 篩選列是否統一用 `SegmentedControl`（稽核 #25）？
- ❓ 排序指示、多選 checkbox 欄的樣式是否要進規範？

### 6.5 Dialog / Modal

現況：寬度四級 400 / 640 / 1100 / 1280；高度 88vh；遮罩 `--color-overlay` + blur(4px) + z-index 300；進場 fadeIn + slideUp，離場由 `useDialogPresence` 保留 150ms；確認框一律 `useConfirm()`。

- [ ] 沿用現況
- 📝 Dialog 標題列、關閉鈕、頁尾按鈕順序（取消在左或右）：
- 📝 Dialog 內長內容的捲動方式（整個框捲動或內容區捲動）：
- ❓ 手機寬度下 Dialog 是否改為全螢幕 bottom sheet？
- ❓ 巢狀 Dialog（Dialog 內再開確認框）是否允許？z-index 怎麼疊？

### 6.6 Dropdown / 浮層

現況：absolute 向上展開；被 `overflow: hidden` 玻璃容器裁到時改 `createPortal` + `position: fixed`（範例 `PowerMenu`），背景用不透明 `--color-surface`。

- [ ] 沿用現況
- 📝 選單項目高度、hover 色、分隔線、危險項目顏色：
- ❓ 是否統一改成 portal 版本，不再允許 absolute 版本？

### 6.7 Badge / 狀態指示

現況：`badge-base` + `color-mix(… 12%, transparent)` 底色 + 狀態色文字；`.badge_muted` 用 `--color-hover`。

- [ ] 沿用現況
- 📝 狀態點（dot）與 badge 的使用分界：
- ❓ 是否需要帶 icon 的 badge 規格？

### 6.8 Toast / 通知

現況：sonner，經 `hooks/useToast`；Web Push 在分頁前景聚焦時不顯示。

- [ ] 沿用現況
- 📝 哪些操作要 toast、哪些只改按鈕狀態（稽核 #6 反映訊息太多）：
- 📝 toast 停留時間、位置、是否可堆疊：
- ❓ 成功訊息是否一律 toast，還是「列表即時更新就不用提示」？

### 6.9 其他共用元件

- 📝 Tabs、SegmentedControl、Tooltip、Pagination、Stepper、進度條、Skeleton 各自的規格（沒有的寫「不需要」）：

---

## 7. 動畫與互動

### 現況

- 入場：`slideUp 0.18s cubic-bezier(0.25, 0.8, 0.25, 1)`、`fadeIn 0.15s ease`。
- 離場：`setTimeout` + CSS `transition`，不用 `onAnimationEnd`；Dropdown 130ms、Dialog 150ms。
- 按鈕狀態變化統一 0.2s。

### 前端要求

- [ ] 沿用現況
- 📝 允許的動畫時長級距（例如 120 / 150 / 200 / 300ms）與對應場景：
- 📝 列表新增／刪除項目是否要動畫：
- ❓ 是否遵守 `prefers-reduced-motion`？要的話停掉哪些？
- ❓ 骨架屏（skeleton）與 spinner 的使用分界？載入超過幾毫秒才顯示？

---

## 8. 頁面狀態（空／載入／錯誤）

### 現況

- 已有共用元件但規範沒寫何時必用：`components/EmptyState`（`icon`、`iconSize`、`title`、`description`、`action`）、`components/LoadingState`（`text`、`fullPage`）。
- 錯誤狀態沒有共用元件，各頁自行處理。稽核 R5 指出說明文字策略不一致（#3 #12 #13 #18 #20 #21 #23）。

### 前端要求

- 📝 **空狀態**：`EmptyState` 是否強制使用？圖示、標題、說明、主要動作按鈕的固定結構與字級：
- 📝 **載入中**：整頁（`LoadingState fullPage`）、區塊、按鈕內三種情境各用什麼：
- 📝 **錯誤**：API 失敗時在原地顯示重試、還是 toast？權限不足頁怎麼呈現？
- 📝 **說明文字策略**：欄位說明放 label 下方、tooltip、還是頁首一段？「太多小註解」的收斂原則是什麼？
- 📝 **數字摘要卡**（稽核 #27 質疑必要性）：什麼頁面該有、什麼頁面不該有？

---

## 9. 響應式（RWD）

### 現況

- 新版面 mobile-first 用 `respond-to`；既有 desktop-first 版面用 `respond-below` 收斂。
- 側欄在 1024px 以下改抽屜。
- 表格橫向卷動，不轉卡片。

### 前端要求

- [ ] 沿用現況
- 📝 必須支援的最小寬度（例如 360px）與主要驗證寬度：
- 📝 哪些頁面明確不支援手機（例如 VNC 主控台、Gateway 設定編輯器）：
- ❓ 卡片網格、表單 grid 在各斷點的欄數是否要寫成固定對照表？
- ❓ 觸控裝置的最小可點面積（44px）是否要求？

---

## 10. 深色模式與風格變體

### 現況

- `body.dark` 切換；元件只用 CSS 變數，不自寫 `body.dark &` 覆蓋。
- 風格變體（白底／黑底／液態玻璃…）只改表面與濾鏡，由 ThemeContext 保證白底限亮色、黑底限暗色。
- 背景花色 `data-bg` 與風格無關。

### 前端要求

- [ ] 沿用現況
- 📝 允許例外寫 `body.dark &` 的情境清單：
- 📝 新增風格變體時必須定義的變數清單（checklist）：
- ❓ 圖片、logo、圖表在深色模式的處理原則（反白、換圖、加底）？
- ❓ 使用者自訂主色時，狀態色是否也要跟著衍生？

---

## 11. 無障礙（a11y）

### 現況

- 按鈕 `:focus-visible` 有 2px 主色外框。
- `MIcon` 一律 `aria-hidden`。
- 其餘無明文規範。

### 前端要求

- 📝 icon-only 按鈕的 `aria-label` 要求：
- 📝 Dialog 的焦點鎖定與 Esc 關閉：
- 📝 表格、表單的 label 關聯要求：
- ❓ 對比度、鍵盤操作的驗收標準要不要進 PR checklist？

---

## 12. 文案與語言

### 現況

- 介面繁體中文，無 i18n 框架。
- 稽核 #21 反映 LDAP 頁英文術語不合適。

### 前端要求

- 📝 中英文混排規則（中英之間是否加空格、專有名詞保留英文的清單）：
- 📝 按鈕文案動詞規則（「儲存」vs「儲存變更」、「刪除」vs「移除」的分界）：
- 📝 錯誤訊息語氣與格式（是否帶錯誤碼、是否給下一步）：
- 📝 標點：全形／半形、句尾是否加句號：

---

## 13. 檔案與工具

### 現況

- 前端無 linter（biome 列為後續）；Vitest 只測 services 層。
- 全域注入僅 `variables` 與 `mixins`。

### 前端要求

- 📝 是否要導入 stylelint 或 biome 的 CSS 規則？要擋哪些（寫死 HEX、`!important`、深層巢狀）？
- 📝 SCSS 巢狀深度上限：
- 📝 屬性排序規則（有無偏好，例如定位 → 盒模型 → 文字 → 視覺）：
- ❓ 是否需要一份 UI 元件展示頁（內部 storybook 替代）方便驗收？

---

## 14. 變數變更申請表

> 凡動到 `_variables.scss` 或 `_themes.scss` 都要在此登記一列。新增填「新增」，調整填現值與新值，刪除要列出所有引用處。

| 動作 | 檔案 | 變數名 | 現值（亮／暗） | 新值（亮／暗） | 理由 | 受影響檔案 | 狀態 |
|------|------|--------|----------------|----------------|------|------------|------|
| | | | | | | | 待討論 |

---

## 15. PR 檢核清單（草案）

> 前端填完後，這一節會變成 PR review 時逐項勾的清單。請刪掉不需要的、補上缺的。

- [ ] 沒有寫死 HEX（例外已在規範明列）
- [ ] 沒有在元件內宣告新變數
- [ ] 表單用 `form-*` mixin，表格用 `table-*` mixin
- [ ] 可點的列才有 hover，`<tr>` 有 `onClick` 才用 `table-tr-clickable`
- [ ] 列數會變的表格有 `table-layout: fixed` + `<colgroup>`
- [ ] 確認框走 `useConfirm()`，不自建 Modal
- [ ] Dialog 離場走 `useDialogPresence` 或 `closing` prop
- [ ] Icon 用 `MIcon`，icon-only 按鈕有 `aria-label`
- [ ] 手機寬度（📝 填寬度）檢查過不會橫向捲動
- [ ] 深色模式檢查過
- [ ] 📝 其他：

---

## 16. 開放問題與備註

- 📝 以上各節沒涵蓋、但前端覺得該定下來的事：
- 📝 目前最想先解決的三件事（依優先順序）：
  1.
  2.
  3.
