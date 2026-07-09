# SkyLab Frontend — 樣式規範

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

## 在 SCSS Module 中引入共用樣式

每個 `.module.scss` 檔案最上方需加：

```scss
@use "../../../assets/styles/variables" as *;
@use "../../../assets/styles/mixins" as *;
```

> 路徑依元件深度調整。引入後即可直接使用 `$spacing-*`、`$font-size-*`、`@include flex-center` 等。

---

## ⚠️ 變數使用原則

**禁止在元件 SCSS 中自行新增新的 SCSS 變數或 CSS 自訂屬性。**

請優先查閱並沿用 `_variables.scss`（間距、字體、圓角等）與 `_themes.scss`（顏色）中已定義的變數。若確實找不到對應的變數，應先討論是否有必要加入全域定義，而非在元件內自行宣告。

---

## 顏色系統

**所有顏色一律使用 `_themes.scss` 中定義的 CSS 自訂屬性**，不可在元件 SCSS 內直接寫死 HEX 色碼（狀態色碼除外，見下方說明）。

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
| `--color-primary-dark` | 深色主色 |
| `--color-primary-light` | 淺色主色 |

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
| `--color-overlay` | Modal 遮罩 |

#### 陰影
| 變數 | 用途 |
|------|------|
| `--shadow-sm` | 細微陰影 |
| `--shadow-md` | 中等陰影（卡片） |
| `--shadow-lg` | 大陰影（Dialog） |
| `--shadow-glass` | 毛玻璃陰影 |

### 狀態色

前端只使用以下四種語意顏色，**不使用黃色 / 橙色作為警示色**：

| 變數 | 色碼 | 語意 | 使用情境 |
|------|------|------|----------|
| `--color-success` | `#28a745` | 🟢 正常 | 運行中、已連接、成功 |
| `--color-info` | `#2b4d98` | 🔵 一般 | 進行中、審核中、說明 |
| `--color-danger` | `#dc3545` | 🔴 危險 | 錯誤、失敗、危險操作 |
| `--color-warning` | `#dc3545` | 🔴 同 danger | （等同 danger，已統一為紅色） |
| —（灰色） | `--color-hover` / `--color-text-muted` | ⚫ 未啟用 | 已停止、已暫停、disabled |

> **例外**：可用名額不足的日曆格（AvailabilityPanel `calendarDayLimited`）保留黃色 `#f59e0b`，因其屬視覺漸層語意，非 UI 警示色。

#### 狀態 Badge 的標準寫法

```scss
.badge_success { background: color-mix(in srgb, #28a745 12%, transparent); color: #28a745; }
.badge_info    { background: color-mix(in srgb, #2b4d98 12%, transparent); color: #2b4d98; }
.badge_danger  { background: color-mix(in srgb, #dc3545 12%, transparent); color: #dc3545; }
.badge_muted   { background: var(--color-hover); color: var(--color-text-muted); }
```

---

## SCSS 變數（\_variables.scss）

### 間距

```scss
$spacing-4: 4px   $spacing-8: 8px   $spacing-16: 16px
$spacing-24: 24px  $spacing-32: 32px  $spacing-48: 48px
```

### 字體大小

```scss
$font-size-12: 12px   $font-size-14: 14px   $font-size-16: 16px
$font-size-18: 18px   $font-size-24: 24px   $font-size-28: 28px   $font-size-32: 32px
```

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

- Dialog 寬度：`max-width: 1100px`（一般）/ `1280px`（寬版，如 VNC）
- 高度：`height: 88vh`
- 全螢幕：使用 `:fullscreen` 偽類，設 `max-width: 100%; height: 100%; border-radius: 0`
- 遮罩：`position: fixed; inset: 0; background: var(--color-overlay); backdrop-filter: blur(4px); z-index: 300`

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
  background: #dc3545;
  color: #fff;
  border: 1px solid #dc3545;
  &:hover:not(:disabled) { background: #b91c1c; }
}
```

> **規則**：所有按鈕 hover 都必須加 `:not(:disabled)`，disabled 狀態一律 `opacity: 0.4; cursor: not-allowed`。

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
| 卡片 hover / 選單 | 50 | Dropdown 選單 |
| Sticky Header | 100 | 頁面頂部導覽列 |
| Dialog / Modal | 300 | 全頁覆蓋 Dialog |
| Toast / Tooltip | 400 | 通知、提示 |

> ⚠️ 注意：使用 `backdrop-filter` 或 `transform` 的元素會建立新的 stacking context，子元素的 `z-index` 無法穿透至外層。若發現 Dropdown 被其他卡片遮住，請確認父元素是否有這類屬性。