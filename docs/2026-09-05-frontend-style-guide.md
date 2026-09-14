# SkyLab Frontend — 樣式規範

- 日期：2026-09-05（Asia/Taipei；原 `frontend/src/assets/styles/STYLE_GUIDE.md` 移入 docs）
- 狀態：現行規範，持續維護
- 適用範圍：前端所有頁面與元件

> 本文件說明前端樣式架構與撰寫規範，所有新頁面、元件都應遵循此指南，確保視覺與程式碼風格一致。若想自行變更_variables.scss、_themes.scss兩檔案，請事先與前端討論。

---

## 目錄結構

```
src/assets/styles/
├── global.scss       # 全域樣式入口（@use themes、reset，背景暈染）
├── _themes.scss      # CSS 自訂屬性（亮色 / 深色主題）
├── _variables.scss   # SCSS 結構變數（間距、字體、斷點、圓角）
├── _mixins.scss      # 可重用的 SCSS mixin
└── _reset.scss       # CSS Reset
```

元件 / 頁面的樣式請以 **CSS Modules** 撰寫，放在元件旁：

```
src/pages/personal/resources/
├── ResourcesPage.jsx
└── ResourcesPage.module.scss   ← 與元件同名，同目錄
```

---

## 在 SCSS Module 中使用共用變數與 mixin

`vite.config.js` 已透過 `css.preprocessorOptions.scss.additionalData` 對**所有** SCSS 檔全域注入：

```scss
@use "@/assets/styles/variables" as *;
@use "@/assets/styles/mixins" as *;
```

因此 `.module.scss` 內可直接使用 `$spacing-*`、`$font-size-*`、`@include flex-center` 等，**不需要（也不應）在檔案開頭手動再加 `@use variables / mixins`**——手動引入是冗餘的。舊檔殘留的手動引入無害，重構經過時順手移除即可。

> 注意：全域注入僅涵蓋 `variables` 與 `mixins` 兩檔；`_themes.scss` 的顏色是 CSS 自訂屬性（`var(--color-*)`），本來就不需引入。

---

## ⚠️ 變數使用原則

**禁止在元件 SCSS 中自行新增新的 SCSS 變數或 CSS 自訂屬性。**

請優先查閱並沿用 `_variables.scss`（間距、字體、圓角等）與 `_themes.scss`（顏色）中已定義的變數。若確實找不到對應的變數，應先討論是否有必要加入全域定義，而非在元件內自行宣告。

---

## 顏色系統

**所有顏色一律使用 `_themes.scss` 中定義的 CSS 自訂屬性**，不可在元件 SCSS 內直接寫死 HEX 色碼。狀態色也一律走 `--color-success` 等變數（僅下方明列的例外可寫死色碼）。

### 主要變數

#### 背景
| 變數 | 用途 |
|------|------|
| `--color-bg-base` | 頁面底色 |
| `--color-bg-gradient-blue/yellow/green` | 三色暈染背景漸層 |

#### 表面
| 變數 | 用途 |
|------|------|
| `--color-surface` | 卡片、面板背景 |
| `--color-surface-glass` | 毛玻璃效果背景 |
| `--color-surface-glass-border` | 毛玻璃邊框 |
| `--color-sidebar` | 側邊欄背景 |

#### 品牌色
| 變數 | 用途 |
|------|------|
| `--color-primary` | 主色（藍紫） |
| `--color-primary-dark` | 深色主色（**僅作底色**，如 primary 按鈕 hover；深色模式仍為深色，當文字會不可讀）|
| `--color-primary-light` | 淺色主色 |
| `--color-primary-on-surface` | 品牌色**文字／邊框**用；亮暗兩色都達 AA。勿與 `--color-text-primary` 混淆（語序相反、兩者皆為藍色）|

#### 文字
| 變數 | 用途 |
|------|------|
| `--color-text` | 一般文字 |
| `--color-text-primary` | 標題、強調文字 |
| `--color-text-secondary` | 次要文字 |
| `--color-text-muted` | 輔助說明、placeholder |
| `--color-text-on-primary` | 主色背景上的文字（白） |

#### 邊框與互動
| 變數 | 用途 |
|------|------|
| `--color-border` | 一般邊框 |
| `--color-divider` | 分隔線 |
| `--color-hover` | Hover 背景 |
| `--color-row-hover` | 表格列 hover 背景（比 `--color-hover` 深，避免與表頭同色） |
| `--color-overlay` | Modal 遮罩 |

#### 陰影
| 變數 | 用途 |
|------|------|
| `--shadow-sm` | 細微陰影 |
| `--shadow-md` | 中等陰影（卡片） |
| `--shadow-lg` | 大陰影（Dialog） |
| `--shadow-glass` | 毛玻璃陰影 |

### 狀態色

前端使用以下五種語意顏色，**黃橙色僅限「待審核 / pending」語意，不作為警示色**——警示、錯誤一律紅色，統一走 `--color-danger`（不另設 `--color-warning`）：

| 變數 | 亮色值 | 深色值 | 語意 | 使用情境 |
|------|--------|--------|------|----------|
| `--color-success` | `#28a745` | 同左 | 🟢 正常 | 運行中、已連接、成功 |
| `--color-info` | `#2b4d98` | `#89a5e0` | 🔵 一般 | 進行中、說明、一般標記 |
| `--color-pending` | `#d97706` | `#f59e0b` | 🟠 待審核 | 待審核、草稿、排程中、等待處理 |
| `--color-danger` | `#dc3545` | 同左 | 🔴 危險 | 錯誤、失敗、危險操作 |
| `--color-status-neutral` | `#6b7280` | `#9ca3af` | — | ⚫ 未啟用 | 已停止、已暫停、disabled |

危險操作的 hover 加深色用 `--color-danger-dark`（`#b91c1c`）。

> **例外**：終端機式的內容面固定深色、不隨主題切換——VNC / xterm 畫面底（ConsoleDialog、Classroom 的 `#1e1e1e`）、任務 log 輸出區（Jobs `dialogOutput`），以及需要白底墊圖的透明 logo（`tplLogo` 的 `#fff`）、PDF 檢視器的 iframe 底（`StudentHomePage` 的 `#fff`——PDF 頁面本身即白底，跟著主題轉深會有黑框）。
>
> **例外**：Gateway 頁的類 VSCode 設定檔編輯器（`ConfigCodeEditor.module.scss`）整組寫死 vs-dark 色票（`#1e1e1e`、`#252526`、`#007acc` 等）與 13px/12px 字級，刻意不隨主題切換——外框需與 Monaco `theme="vs-dark"` 一致，模擬 VSCode 視窗本身即為獨立配色的容器。

#### 狀態 Badge 的標準寫法

```scss
.badge_success { background: color-mix(in srgb, var(--color-success) 12%, transparent); color: var(--color-success); }
.badge_info    { background: color-mix(in srgb, var(--color-info)    12%, transparent); color: var(--color-info); }
.badge_pending { background: color-mix(in srgb, var(--color-pending) 12%, transparent); color: var(--color-pending); }
.badge_danger  { background: color-mix(in srgb, var(--color-danger)  12%, transparent); color: var(--color-danger); }
.badge_muted   { background: var(--color-hover); color: var(--color-status-neutral); }
```

> 一律用 `var(--color-*)`，不要把狀態色寫死成 HEX——深色模式的 info / pending 亮色值才吃得到。

---

## SCSS 變數（\_variables.scss）

### 間距

```scss
$spacing-4: 4px   $spacing-8: 8px   $spacing-16: 16px
$spacing-24: 24px  $spacing-32: 32px  $spacing-48: 48px
```

### 字體大小

```scss
$font-size-10: 10px   $font-size-12: 12px   $font-size-14: 14px   $font-size-16: 16px
$font-size-18: 18px   $font-size-24: 24px   $font-size-28: 28px   $font-size-32: 32px
```

> `$font-size-10` **僅限資料密集區**（密集網格、卡片 meta 列）的次要標籤使用；一般內文、說明文字最小 `$font-size-12`。

### 字重

```scss
$font-weight-400: 400   $font-weight-500: 500   $font-weight-700: 700
```

### 圓角

```scss
$radius-8: 8px   $radius-12: 12px   $radius-16: 16px   $radius-pill: 999px
```

### 動畫

```scss
$transition-base: 0.2s ease   $transition-slow: 0.3s ease
```

### 斷點

```scss
$breakpoint-sm: 576px   $breakpoint-md: 768px
$breakpoint-lg: 992px   $breakpoint-xl: 1200px
```

---

## Mixin（\_mixins.scss）

### Flex 排版

```scss
@include flex-center;    // display:flex; align-items:center; justify-content:center
@include flex-between;   // display:flex; align-items:center; justify-content:space-between
@include flex-column;    // display:flex; flex-direction:column
```

### 文字截斷

```scss
@include text-truncate;     // 單行截斷＋省略號
@include text-clamp(3);     // 多行截斷（預設 2 行）
```

### 容器

```scss
@include container;   // max-width: 1200px; margin-inline: auto; padding-inline: 16px
```

### 毛玻璃效果

```scss
@include glass-surface;                                // 預設玻璃陰影 var(--shadow-glass)
@include glass-surface($shadow: var(--shadow-sm));    // 換陰影
@include glass-surface($shadow: none);                // 不輸出 box-shadow
@include glass-surface(8px, 1.2);                     // 固定濾鏡參數（不跟隨風格切換，特殊情況才用）
```

玻璃表面的 backdrop-filter 一律走 `var(--glass-backdrop-filter)`
（sidebar 用 `var(--sidebar-backdrop-filter)`），讓「液態玻璃」等
風格變體能整體換濾鏡——**不要在元件裡寫死 `blur(12px) saturate(1.4)`**。

### 響應式斷點

```scss
@include respond-to(md) {
  // min-width: 768px 時套用
}
```

---

## Icon 使用規範

**所有 Icon 一律使用 `material-icons`（filled 風格），透過 `MIcon` 元件呼叫。**

```jsx
import MIcon from "../components/MIcon";

<MIcon name="search" size={16} />
```

- Icon 名稱請至 [Material Symbols](https://fonts.google.com/icons) 查詢，使用 **filled** 風格的名稱
- 禁止直接使用 `<span className="material-icons">` 或其他 Icon 庫
- 禁止使用 SVG inline、emoji、或其他圖示系統混搭

---

## 命名規範

### CSS Modules 類別名稱

使用 **camelCase**：

```scss
.cardHeader { }
.statusDot  { }
.headerBtn  { }
```

### BEM 風格的子變體

用底線 `_` 區隔變體，而非 BEM 的 `--`：

```scss
.badge_success { }
.badge_danger  { }
.dot_connected { }
.dot_error     { }
```

### 動畫 / 狀態後綴

| 後綴 | 用途 |
|------|------|
| `Out` | 元素離場動畫（如 `.powerMenuOut`） |
| `Active` | 主動選中狀態（如 `.menuBtnActive`） |
| `Disabled` | 禁用樣式（優先用 CSS `:disabled` 偽類） |

---

## 頁面版面（Page Layout）

**頁面根容器（`.page`）一律滿寬**：不設 `max-width`、不使用 `margin: 0 auto` 置中，讓內容吃滿 DashboardLayout 的內容區。全站頁面留白因此一致，寬螢幕下不會出現「某些頁置中留白、某些頁滿寬」的落差。

標準寫法：

```scss
.page {
  @include flex-column;
  gap: $spacing-24;
  padding: $spacing-8 $spacing-16;
  flex: 1;

  @include respond-to(md) { padding: 0; }
}
```

> **例外**：表單型窄頁（如 `AccountSettingsPage` 的 `max-width: 640px`）可限制寬度——輸入欄位拉滿寬螢幕反而難用。這類例外限於「單欄表單」頁面，一般內容頁請維持滿寬。

---

## 元件樣式慣例

### 卡片（Card）

```scss
.card {
  @include glass-surface;
  border-radius: $radius-16;
  @include flex-column;
  overflow: hidden;
}
```

### Dialog / Modal

- Dialog 寬度四級：確認框／命名框 `max-width: 400px`；小型單欄表單 `max-width: 640px`；一般 `max-width: 1100px`；寬版（如 VNC）`1280px`
- 高度：`height: 88vh`
- 全螢幕：使用 `:fullscreen` 偽類，設 `max-width: 100%; height: 100%; border-radius: 0`
- 遮罩：`position: fixed; inset: 0; background: var(--color-overlay); backdrop-filter: blur(4px); z-index: 300`

#### 確認彈窗（全站統一）

危險操作／二選一確認**一律用共用的 `useConfirm()`**（`components/ConfirmDialog/ConfirmProvider`），
不要在頁面內自建本地 ConfirmModal（2026-09-09 已全數整合，AiJudgePanel 的多動作對話框為唯一例外）：

```jsx
const confirm = useConfirm();
if (!(await confirm({ title, message, confirmText, danger: true }))) return;
// …按下確認後彈窗即關閉，進度用按鈕 disabled + toast 呈現，不在彈窗內轉圈
```

卡片樣式基準（與「我的申請」錯誤記錄 modal 同構的毛玻璃卡）：

```scss
.dialog {
  width: 100%;
  max-width: 400px;          // 錯誤 log 等寬內容款可放寬到 560px
  @include glass-surface;    // 毛玻璃底，不用實色 surface + 邊框
  border-radius: $radius-16;
  padding: $spacing-24;
  @include flex-column;
  gap: $spacing-16;          // 標題／內文／按鈕列間距全交給 gap，不用 margin
}
```

- 標題：`$font-size-16` / `$font-weight-700`，danger 帶紅色 `warning` 圖示、一般帶 `help` 圖示
- 內文：`$font-size-14` 次要色，**必加 `overflow-wrap: anywhere`**（常插入主機名稱等連續長字串）
- 進出場：遮罩 fadeIn 0.15s、卡片 slideUp 0.18s，Esc 可關閉

### 按鈕

```scss
// 主要按鈕
.btnPrimary {
  background: var(--color-primary);
  color: var(--color-text-on-primary);
  border-radius: $radius-8;
  transition: background $transition-base;
  &:hover:not(:disabled) { background: var(--color-primary-dark); }
  &:disabled { opacity: 0.5; cursor: not-allowed; }
}

// 次要按鈕
.btnSecondary {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  color: var(--color-text-secondary);
  &:hover:not(:disabled) { background: var(--color-hover); }
}

// 危險按鈕
.btnDanger {
  background: var(--color-danger);
  color: var(--color-text-on-primary);
  border: 1px solid var(--color-danger);
  &:hover:not(:disabled) { background: var(--color-danger-dark); }
}
```

> **規則**：所有按鈕 hover 都必須加 `:not(:disabled)`，disabled 狀態一律 `opacity: 0.5; cursor: not-allowed`。

### 表單（Form）

表單欄位一律使用 `_mixins.scss` 的表單 mixin 組，**不要在頁面內另立一套字級與內距**：

```scss
.field { @include form-field; }                              // 標籤 + 控制項的直式容器
.field input, .field select, .field textarea { @include form-control; }
.fieldInvalid.fieldInvalid { @include form-control-invalid; } // 送出時未填的欄位
```

```jsx
<label className={styles.field}>
  <span>班級名稱</span>
  <input className={invalid ? styles.fieldInvalid : undefined} aria-invalid={invalid} … />
</label>
```

> **規則一**：控制項高度固定 36px、字級 14px，刻意與 `.btnPrimary` / `.btnSecondary`
> 一致——同一列的欄位與按鈕才會對齊。要更矮更小的表單請先問是不是真的需要，
> 不要在頁面裡改 `min-height` 或 `font-size`。

> **規則二**：欄位怎麼排（幾欄、哪個跨欄）寫在頁面自己的 grid 上（`.formGrid`、
> `.createFormGrid`、`.fieldFull`），mixin 只負責欄位本身長什麼樣。

> **規則三**：一組「起—迄」的值是**一個**欄位，不是兩個。用 `.timePair` 這種
> 成對控制項，標籤寫「上課時間」，不要拆成「開始時間」「結束時間」兩個 `.field`。

### 表格（Table）

列表頁表格一律使用 `_mixins.scss` 的表格 mixin 組，**不要在頁面內重抄整組樣式**：

```scss
.tableWrap { @include table-wrap; }        // 玻璃容器 + 圓角 + 橫向卷動
.table     { @include table-base; min-width: 720px; }  // min-width 依內容自定，撐出卷動
.th        { @include table-th; }
.tr        { @include table-tr; }          // 基底不含 hover，見下方規則
.td        { @include table-td; }
```

- 欄寬、對齊、特殊儲存格（`.thRight`、`.tdNowrap`…）等頁面差異寫在 `@include` 之後
- RWD 行為統一為 **容器橫向卷動**（`table-wrap` 內建 `overflow-x: auto`），不做表格轉卡片
- 表格嵌在既有卡片內時可只用 `table-base` / `table-th` / `table-tr` / `table-td`，省略外層 `table-wrap`

#### 規則一：整列 hover 變色 = 這一列可以點

整列 hover 變色是**互動訊號，不是裝飾**。列本身不可點（互動都在儲存格內的按鈕上）時，
不要讓整列變色，否則是假的可點暗示。

```scss
.tr          { @include table-tr; }            // 不可點：只有分隔線，無 hover
.trClickable { @include table-tr-clickable; }  // 可點：游標 + hover 一起給
```

- 判斷標準只有一個：**`<tr>` 自己有沒有 `onClick`**（或 `role` / `tabIndex`）
- 同一張表可以混用（監控頁：節點列可點、VM 列不可點），所以用兩支 mixin 疊加，不用布林參數
- 不要自己寫 `cursor: pointer` 或 `&:hover { background: … }`——游標與 hover 會各自漂移
- 需要不同的 hover 色時（例如群組列本身已有底色），可在 `@include` 之後覆寫 `background`

#### 規則二：列數會變的表格要固定欄寬

`table-layout` 預設的 `auto` 依**全部列的內容**計算欄寬。只要展開／收合會增減
**完整欄位的列**，每一欄的內容都變了，整張表就會跳動。

```scss
.table { @include table-base; table-layout: fixed; min-width: 1080px; }

.colStatus { width: 100px; }   // 欄寬集中宣告，搭配 JSX 的 <colgroup>
```

- 只有在展開列是 **`<td colSpan={N}>` 的整寬詳情面板**時才不需要——
  colspan 儲存格不參與個別欄寬計算，不會造成跳動
- 改用 `fixed` 後，要移除儲存格內為了「跟 auto layout 搶寬度」而設的 `min-width`，
  它們只會讓內容溢出欄位
- 不指定寬度的那一欄會吸收剩餘空間（通常留給名稱欄）

#### 規則三：儲存格圖示要帶文字沒有的資訊

`MIcon` 一律 `aria-hidden`，螢幕閱讀器讀不到。若圖示編碼的資訊
就寫在緊鄰的文字裡（型別、分類），它只是版面慣性，拿掉讓文字說話即可。

### Dropdown 選單

- `position: absolute; bottom: calc(100% + 6px); right: 0`（向上展開）
- 父元素需有 `position: relative`
- 關閉動畫用 `setTimeout`（130ms）+ CSS `transition`，不用 `onAnimationEnd`

---

## 動畫規範

### 入場動畫

```scss
@keyframes slideUp {
  from { opacity: 0; transform: translateY(12px); }
  to   { opacity: 1; transform: translateY(0); }
}
// 使用：animation: slideUp 0.18s cubic-bezier(0.25, 0.8, 0.25, 1);
```

```scss
@keyframes fadeIn {
  from { opacity: 0; }
  to   { opacity: 1; }
}
// 使用：animation: fadeIn 0.15s ease;
```

### Dialog / Popup 的進出場（標準作法）

Dialog 一律「遮罩 `fadeIn` + 內容 `slideUp`」進場；離場由共用 hook `hooks/useDialogPresence.js` 處理——關閉時先保留 DOM 150ms 套上 `Out` class 播放淡出，再卸載：

```jsx
import useDialogPresence from "../hooks/useDialogPresence";

const dialog = useDialogPresence(editTarget);   // 布林或資料物件皆可
// 關閉期間 dialog.item 會保留最後一筆資料，避免內容閃爍
{dialog.open && (
  <div className={`${styles.modalOverlay} ${dialog.closing ? styles.modalOverlayOut : ""}`}>
    <EditModal target={dialog.item} … />
  </div>
)}
```

```scss
.modalOverlay {
  /* …定位與遮罩… */
  animation: fadeIn 0.15s ease;
  transition: opacity 0.15s ease;
}
.modalOverlayOut {
  animation: none;   // 覆蓋入場 animation，讓 transition 接管
  opacity: 0;
  pointer-events: none;
}
```

共用 Dialog 元件（如 `ConnectionDialog`、`ReverseProxyRuleModal`）接受 `closing` prop 套用 Out class，由父層的 `useDialogPresence` 控制。自含式 Dialog（如 `VncDialog`、`TerminalDialog`）則在內部 `setClosing(true)` 後 `setTimeout(onClose, 150)`。

### 離場動畫（關閉）

優先使用 **`setTimeout` + CSS `transition`**，不使用 `onAnimationEnd`（有已知邊界問題）：

```jsx
// JSX
function closeMenu() {
  setClosing(true);
  setTimeout(() => { setOpen(false); setClosing(false); }, 130);
}
```

```scss
// SCSS
.menu {
  opacity: 1;
  transform: translateY(0);
  transition: opacity 0.12s ease, transform 0.12s ease;
}
.menuOut {
  animation: none;   // 覆蓋入場 animation，讓 transition 接管
  opacity: 0;
  transform: translateY(6px);
  pointer-events: none;
}
```

---

## 深色模式

主題切換透過 `body.dark` class 實現，所有顏色均已在 `_themes.scss` 中定義亮色 / 深色兩套值。

元件 SCSS 一律使用 CSS 自訂屬性，**不需要自行寫 `body.dark &` 覆蓋**。

如果某元件有特殊深色需求：

```scss
// 使用 data-theme 屬性（已有部分元件採用此方式）
[data-theme="dark"] & {
  color: #xxx;
}

// 或使用 body.dark
:global(body.dark) & {
  color: #xxx;
}
```

---

## z-index 層級

| 層級 | 值 | 用途 |
|------|-----|------|
| 基礎卡片 | 1 | 一般卡片 |
| 卡片 hover / 選單 | 50 | 容器內的 Dropdown 選單 |
| Sticky Header | 100 | 頁面頂部導覽列 |
| Portal 浮層選單 | 150 | portal 到 body 的 Dropdown（如 `components/PowerMenu`） |
| Dialog / Modal | 300 | 全頁覆蓋 Dialog |
| Toast / Tooltip | 400 | 通知、提示 |

> ⚠️ 注意：使用 `backdrop-filter` 或 `transform` 的元素會建立新的 stacking context，子元素的 `z-index` 無法穿透至外層。若發現 Dropdown 被其他卡片遮住，請確認父元素是否有這類屬性。
>
> 玻璃表面（`glass-surface`）搭配 `overflow: hidden` 的容器還會**裁掉**溢出的 absolute 選單。浮層若可能超出容器範圍，改用 `createPortal` 掛到 `document.body` 並以 `position: fixed` 定位（範例：`components/PowerMenu`），且背景要用不透明的 `var(--color-surface)`，否則會透出底下的列表內容。