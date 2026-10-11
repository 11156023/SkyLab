# SkyLab Frontend — Style Guide

> **English** | [繁體中文](./frontend-style-guide.zh-TW.md)

- Date: 2026-09-05 (Asia/Taipei; originally `frontend/src/assets/styles/STYLE_GUIDE.md`, moved into `docs/`; now lives at `docs/frontend-style-guide.md`)
- Status: current standard, maintained continuously
- Scope: every page and component in the frontend

> This document describes the frontend styling architecture and the rules for writing styles. Every new page and component must follow it so that the visual result and the code style stay consistent. If you want to change `_variables.scss` or `_themes.scss` yourself, discuss it with the frontend team first.

---

## Directory structure

```
src/assets/styles/
├── global.scss       # Global style entry point (@use themes, reset, background glow)
├── _themes.scss      # CSS custom properties (light / dark theme)
├── _variables.scss   # SCSS structural variables (spacing, fonts, breakpoints, radii)
├── _mixins.scss      # Reusable SCSS mixins
└── _reset.scss       # CSS reset
```

Component and page styles are written as **CSS Modules** and live next to the component:

```
src/pages/personal/resources/
├── ResourcesPage.jsx
└── ResourcesPage.module.scss   ← same name as the component, same directory
```

---

## Using the shared variables and mixins in an SCSS Module

`vite.config.js` already injects the following into **every** SCSS file through `css.preprocessorOptions.scss.additionalData`:

```scss
@use "@/assets/styles/variables" as *;
@use "@/assets/styles/mixins" as *;
```

So inside a `.module.scss` you can use `$spacing-*`, `$font-size-*`, `@include flex-center` and so on directly. **You do not need to (and should not) add `@use variables / mixins` manually at the top of the file** — a manual import is redundant. Leftover manual imports in older files are harmless; remove them when you happen to refactor the file.

> Note: the global injection covers only the `variables` and `mixins` files. The colours in `_themes.scss` are CSS custom properties (`var(--color-*)`) and never needed an import in the first place.

---

## ⚠️ Rules for variables

**Do not declare new SCSS variables or CSS custom properties inside component SCSS.**

Always look up and reuse what is already defined in `_variables.scss` (spacing, fonts, radii, etc.) and `_themes.scss` (colours). If there really is no matching variable, discuss first whether a global definition should be added instead of declaring one inside the component.

---

## Colour system

**Every colour must use a CSS custom property defined in `_themes.scss`**; never hard-code a HEX value in component SCSS. Status colours also go through `--color-success` and friends (hard-coded colours are allowed only in the exceptions listed below).

### Main variables

#### Background
| Variable | Purpose |
|------|------|
| `--color-bg-base` | Page background |
| `--color-bg-gradient-blue/yellow/green` | The three-colour glow gradient background |

#### Surfaces
| Variable | Purpose |
|------|------|
| `--color-surface` | Card and panel background |
| `--color-surface-glass` | Frosted-glass background |
| `--color-surface-glass-border` | Frosted-glass border |
| `--color-sidebar` | Sidebar background |

#### Brand colours
| Variable | Purpose |
|------|------|
| `--color-primary` | Primary colour (blue-violet) |
| `--color-primary-dark` | Dark primary (**backgrounds only**, e.g. the hover state of a primary button; it stays dark in dark mode, so text in this colour becomes unreadable) |
| `--color-primary-light` | Light primary |
| `--color-primary-on-surface` | Brand colour for **text / borders**; meets AA in both light and dark mode. Do not confuse it with `--color-text-primary` (the words are in the opposite order and both are blue) |

#### Text
| Variable | Purpose |
|------|------|
| `--color-text` | Body text |
| `--color-text-primary` | Headings, emphasised text |
| `--color-text-secondary` | Secondary text |
| `--color-text-muted` | Helper text, placeholders |
| `--color-text-on-primary` | Text on a primary-colour background (white) |

#### Borders and interaction
| Variable | Purpose |
|------|------|
| `--color-border` | Regular border |
| `--color-divider` | Divider line |
| `--color-hover` | Hover background |
| `--color-row-hover` | Table row hover background (darker than `--color-hover` so it does not match the header colour) |
| `--color-overlay` | Modal backdrop |

#### Shadows
| Variable | Purpose |
|------|------|
| `--shadow-sm` | Subtle shadow |
| `--shadow-md` | Medium shadow (cards) |
| `--shadow-lg` | Large shadow (dialogs) |
| `--shadow-glass` | Frosted-glass shadow |

### Status colours

The frontend uses the following five semantic colours. **Amber is reserved for the "pending review / pending" meaning and is not a warning colour** — warnings and errors are always red and always use `--color-danger` (there is deliberately no `--color-warning`):

| Variable | Light value | Dark value | Meaning | When to use |
|------|--------|--------|------|----------|
| `--color-success` | `#28a745` | same | 🟢 OK | running, connected, succeeded |
| `--color-info` | `#2b4d98` | `#89a5e0` | 🔵 Neutral info | in progress, explanations, generic labels |
| `--color-pending` | `#d97706` | `#f59e0b` | 🟠 Pending | pending review, draft, scheduled, waiting |
| `--color-danger` | `#dc3545` | same | 🔴 Danger | errors, failures, destructive actions |
| `--color-status-neutral` | `#6b7280` | `#9ca3af` | — | ⚫ Inactive | stopped, paused, disabled |

For the darker hover state of destructive actions use `--color-danger-dark` (`#b91c1c`).

> **Exception**: terminal-style content areas stay dark and do not follow the theme — the VNC / xterm screen background (ConsoleDialog and Classroom's `#1e1e1e`), the task log output area (Jobs `dialogOutput`), transparent logos that need a white backing (`tplLogo`'s `#fff`), the PDF viewer iframe background (`StudentHomePage`'s `#fff` — the PDF page itself is white, so turning it dark with the theme would leave a black frame), and the white backgrounds of the error-page illustrations (`NotFoundPage`'s cloud, `CrashState`'s application window and `ConnectionLostState`'s cloud and window are all `#fff`, and their inner colour blocks are also mixed against `#fff` — the illustration is the same image in both themes; in light mode the outline comes from a drop-shadow).
>
> **Exception**: sample code blocks (the code samples in the AI API quick start and the code blocks inside API chat replies) keep a fixed dark background and do not follow the theme, mirroring a dark VS Code editor; use the `--color-code-*` palette from `_themes.scss` (bg / header / border / hover / text / muted) and do not define another dark palette inside a page.
>
> **Exception**: the VS Code-like config file editor on the Gateway page (`ConfigCodeEditor.module.scss`) hard-codes the whole vs-dark palette (`#1e1e1e`, `#252526`, `#007acc`, etc.) and the 13px/12px font sizes, and deliberately does not follow the theme — the frame has to match Monaco's `theme="vs-dark"`, emulating a VS Code window, which is a container with its own colour scheme.

#### The standard way to write a status badge

```scss
.badge_success { background: color-mix(in srgb, var(--color-success) 12%, transparent); color: var(--color-success); }
.badge_info    { background: color-mix(in srgb, var(--color-info)    12%, transparent); color: var(--color-info); }
.badge_pending { background: color-mix(in srgb, var(--color-pending) 12%, transparent); color: var(--color-pending); }
.badge_danger  { background: color-mix(in srgb, var(--color-danger)  12%, transparent); color: var(--color-danger); }
.badge_muted   { background: color-mix(in srgb, var(--color-status-neutral) 12%, transparent); color: var(--color-status-neutral); }
```

> Always use `var(--color-*)`; do not hard-code status colours as HEX — otherwise the lighter dark-mode values for info / pending never take effect.

> **Badges never have a border**: use only "status colour at 12% as a tint + status colour as text", and do not add a `border` (neither solid nor dashed). Pill labels for status, type, scope and the like all count as badges; non-status neutral labels use the `badge_muted` above. When a badge sits on something that lets the underlying image show through (such as a canvas), mix the tint with `var(--color-surface)` instead (`color-mix(in srgb, <colour> 12%, var(--color-surface))`) so it becomes opaque — still without a border.
> The selected outline of tabs, SegmentedControl and Stepper is not covered by this rule.

---

## SCSS variables (\_variables.scss)

### Spacing

```scss
$spacing-4: 4px   $spacing-8: 8px   $spacing-16: 16px
$spacing-24: 24px  $spacing-32: 32px  $spacing-48: 48px
```

### Font sizes

```scss
$font-size-10: 10px   $font-size-12: 12px   $font-size-14: 14px   $font-size-16: 16px
$font-size-18: 18px   $font-size-24: 24px   $font-size-28: 28px   $font-size-32: 32px
```

> `$font-size-10` is **restricted to data-dense areas** (dense grids, card meta rows) and only for secondary labels; regular body and helper text is `$font-size-12` at the smallest.

### Font weights

```scss
$font-weight-400: 400   $font-weight-500: 500   $font-weight-700: 700
```

### Border radii

```scss
$radius-8: 8px   $radius-12: 12px   $radius-16: 16px   $radius-pill: 999px
```

### Transitions

```scss
$transition-base: 0.2s ease   $transition-slow: 0.3s ease
```

### Breakpoints

```scss
$breakpoint-sm: 576px   $breakpoint-md: 768px
$breakpoint-lg: 992px   $breakpoint-xl: 1200px
```

---

## Mixins (\_mixins.scss)

### Flex layout

```scss
@include flex-center;    // display:flex; align-items:center; justify-content:center
@include flex-between;   // display:flex; align-items:center; justify-content:space-between
@include flex-column;    // display:flex; flex-direction:column
```

### Text truncation

```scss
@include text-truncate;     // single-line truncation with an ellipsis
@include text-clamp(3);     // multi-line clamp (defaults to 2 lines)
```

### Container

```scss
@include container;   // max-width: 1200px; margin-inline: auto; padding-inline: 16px
```

### Frosted-glass surface

```scss
@include glass-surface;                                // default glass shadow var(--shadow-glass)
@include glass-surface($shadow: var(--shadow-sm));    // different shadow
@include glass-surface($shadow: none);                // no box-shadow emitted
@include glass-surface(8px, 1.2);                     // fixed filter parameters (ignores style switching; special cases only)
```

The backdrop-filter of a glass surface always goes through `var(--glass-backdrop-filter)`
(the sidebar uses `var(--sidebar-backdrop-filter)`), so that style variants such as
"liquid glass" can swap the filter globally — **do not hard-code `blur(12px) saturate(1.4)` inside a component**.

### Responsive breakpoints

```scss
@include respond-to(md) {
  // applied at min-width: 768px
}
```

---

## Icon rules

**Every icon goes through the `MIcon` component, which defaults to the outlined style**; when you need a filled icon pass the `filled` prop instead of swapping the class yourself.

```jsx
import MIcon from "../components/MIcon";

<MIcon name="search" size={16} />          {/* material-icons-outlined (default) */}
<MIcon name="star" size={16} filled />     {/* material-icons (filled, only for special emphasis) */}
```

- Look up icon names at [Material Symbols](https://fonts.google.com/icons) (outlined and filled share the same name)
- Never use `<span className="material-icons">`, `material-icons-outlined` or any other icon library directly
- Never mix in inline SVG, emoji or any other icon system

---

## Naming conventions

### CSS Modules class names

Use **camelCase**:

```scss
.cardHeader { }
.statusDot  { }
.headerBtn  { }
```

### BEM-style sub-variants

Separate variants with an underscore `_` rather than BEM's `--`:

```scss
.badge_success { }
.badge_danger  { }
.dot_connected { }
.dot_error     { }
```

### Animation / state suffixes

| Suffix | Purpose |
|------|------|
| `Out` | Exit animation of an element (e.g. `.powerMenuOut`) |
| `Active` | Actively selected state (e.g. `.menuBtnActive`) |
| `Disabled` | Disabled styling (prefer the CSS `:disabled` pseudo-class) |

---

## Page layout

**The page root container (`.page`) is always full width**: no `max-width`, no `margin: 0 auto` centring; let the content fill the DashboardLayout content area. This keeps the page margins consistent across the whole site, so wide screens never show a mix of "some pages centred with side gaps, some pages full width".

The standard form:

```scss
.page {
  @include flex-column;
  gap: $spacing-24;
  padding: $spacing-8 $spacing-16;
  flex: 1;

  @include respond-to(md) { padding: 0; }
}
```

> **Exception**: narrow form-type pages (such as `AccountSettingsPage` with `max-width: 640px`) may constrain their width — input fields stretched across a wide screen are actually harder to use. This exception is limited to "single-column form" pages; regular content pages stay full width.

---

## Component style conventions

### Card

```scss
.card {
  @include glass-surface;
  border-radius: $radius-16;
  @include flex-column;
  overflow: hidden;
}
```

- A clickable card's hover only gets a light tint (`background: var(--color-hover)`) or a shadow / lift, with a `transition` for fade-in and fade-out; **do not change the border colour** — a blue border is reserved for the "selected / current" state (such as an active tab or the current step of a flow)
- Do not build the tint with a `linear-gradient` layer: gradients cannot transition, so the hover colour would jump instantly
- Content blocks inside a glass card (info fields, note boxes, `pre` blocks for code / logs / keys, list rows, stat cells) always use `background: var(--color-surface)` (white) plus `1px solid var(--color-border)`; do not fill them with primary-tinted light blues such as `--color-hover` or `--color-bg-base`. Those follow the primary colour, so when the user changes the background colour they blur into the glass that lets the background through. Light blue is reserved for hover / selected states, buttons, badges and callout boxes
- The topology canvas (React Flow) is not covered by the white-background rule: its background, border and shadow always use the canvas-specific tokens `background: var(--color-flow-bg)`, `1px solid var(--color-flow-border)`, `box-shadow: 0 4px 24px var(--color-flow-shadow)`; the firewall page, the course environment editor, resource details and the read-only topology of the class session environment all share this one set

### Dialog / Modal

New dialogs **always use the shared `<Modal>`** (`components/Modal/Modal`); do not hand-write the backdrop and card again. It takes care of
portalling to body, the backdrop and the enter / exit animations, `role` / `aria-modal` / `aria-labelledby`, closing on Esc and backdrop click (not while `busy`; with two stacked dialogs only the top one closes),
moving focus into the dialog on open and restoring it after close, trapping Tab inside the dialog, and locking scroll of the page underneath:

```jsx
const presence = useDialogPresence(show);
{presence.open && (
  <Modal
    as="form" onSubmit={submit}          // only for form-type dialogs
    closing={presence.closing} onClose={() => setShow(false)} busy={saving}
    title="重設密碼" description="新密碼只會顯示一次"
    size="sm"                            // sm 400 / md 640 / lg 1100 / xl 1280; use log (560) for wide content such as error logs
    closeButton                          // forms with many fields: × in the title bar, the body scrolls on its own, the button row is pinned to the bottom
    actions={<><button …>取消</button><button type="submit" …>送出</button></>}
  >
    …fields…
  </Modal>
)}
```

- Compact card (default): confirmation boxes, naming boxes, small forms; the `closeButton` variant: forms with many fields (such as the connection dialog) — a reminder whose body is only a sentence or two (such as the session warning) can add `dividers={false}` to drop the rules under the title bar and above the button row; `bare`: only the outer frame, you lay out the title bar and body yourself
- Screens the AI cannot read, such as terminals and VNC, use `layer="screen"` (usually together with `bare`): they cover the AI assistant instead of making room for it; the keyboard is handed entirely to the screen, Esc does not close and Tab is not trapped (so Tab completion and vim's Esc work). Grab the dialog body with a `ref` when you need fullscreen
- Guide attributes such as `data-guide` are passed straight to `Modal` (attached to the dialog body); for the × and the button row use `closeProps` and `actionsProps` respectively
- Migrated in batches since 2026-09-26: the dialogs on the resource details page, `useConfirm`, `ConnectionDialog`, terminal / VNC, convert to template, request error log, session reminder, background task details, the system administration side (users, nodes, PVE connections, quotas, domains, subnets, mining incidents), template management (create / edit, clone, user manual), reverse proxy rules, classroom watch, AI services (one-time key, quick start, request a key, disable a key, request review), AI checks (delete script, add check, run once, adjust weeks) and the timetable editor have been converted; the remaining dialogs still use the old approach — convert them when you touch them
- Four dialog width tiers: confirmation / naming boxes `max-width: 400px`; small single-column forms `max-width: 640px`; regular `max-width: 1100px`; wide (such as VNC) `1280px`
- Height: `height: 88vh`
- Fullscreen: use the `:fullscreen` pseudo-class and set `max-width: 100%; height: 100%; border-radius: 0`
- Backdrop: `position: fixed; inset: 0; background: var(--color-overlay); backdrop-filter: blur(4px); z-index: 300`
- The backdrop is always `createPortal`-ed to `document.body`: when an ancestor has `backdrop-filter` / `transform`, `position: fixed` gets trapped inside that layer and cannot cover the whole screen
- Card shadow: confirmation boxes use the default `glass-surface` shadow; form-type dialogs such as create / edit may use `glass-surface($shadow: var(--shadow-lg))` so they stand out more against the backdrop
- Accessibility: the dialog container gets `role="dialog"`, `aria-modal="true"` and `aria-labelledby` pointing at the title (id generated with `useId()`), so screen readers announce "dialog: title"

#### Confirmation dialog (unified site-wide)

Destructive actions and either/or confirmations **always use the shared `useConfirm()`** (`components/ConfirmDialog/ConfirmProvider`);
do not build a local ConfirmModal inside a page (everything was consolidated on 2026-09-09; the multi-action dialog in AiJudgePanel is the only exception):

```jsx
const confirm = useConfirm();
if (!(await confirm({ title, message, confirmText, danger: true }))) return;
// …the dialog closes as soon as confirm is pressed; show progress with a disabled button + toast, not a spinner inside the dialog
```

Baseline card style (the same frosted-glass card as the error log modal in "My Requests"):

```scss
.dialog {
  width: 100%;
  max-width: 400px;          // the wide variant for content such as error logs may go up to 560px
  @include glass-surface;    // frosted-glass base, not a solid surface + border
  border-radius: $radius-16;
  padding: $spacing-24;
  @include flex-column;
  gap: $spacing-16;          // spacing between title / body / button row is all handled by gap, not margin
}
```

- Title: `$font-size-16` / `$font-weight-700`; danger dialogs carry a red `warning` icon, regular ones a `help` icon
- Body: `$font-size-14` in the secondary colour, **must include `overflow-wrap: anywhere`** (long unbroken strings such as hostnames are often inserted)
- Enter / exit: backdrop fadeIn 0.15s, card slideUp 0.18s, Esc closes

### Buttons

Buttons always use the button mixin family (six variants) from `_mixins.scss`; **do not copy the whole set of styles into a page**
(consolidated site-wide on 2026-09-15; do not let the old hand-copied duplicates come back):

```scss
.btnPrimary       { @include btn-primary; }        // primary action: at most one per screen
.btnSecondary     { @include btn-secondary; }      // regular actions placed side by side, back
.btnDanger        { @include btn-danger; }         // only in confirmation dialogs / the final confirmation step
.btnDangerOutline { @include btn-danger-outline; } // the "entry point" of a destructive action on a page; clicking it opens useConfirm
.btnGhost         { @include btn-ghost; }          // low-emphasis helper action: tinted text button (has the --color-hover tint at rest, one step darker on hover)
.btnGhostDanger   { @include btn-ghost($danger: true); }  // light-red tint with red text (delete in tight spaces)
.iconBtn          { @include btn-icon; }           // 32×32 icon button; the JSX must carry aria-label
.iconBtnDanger    { @include btn-icon($danger: true); }   // text is red even before hover
.menuBtn          { @include btn-icon-secondary; } // action menu button in table rows (white with outline); the icon is always more_vert (⋮)
.dialogClose      { @include btn-dialog-close; }   // dialog-only top-right close button: same as btn-icon($danger: true) but with no background at rest
```

- Shared base (built into the mixins): 36px tall, `$radius-8` corners, 14px / 500 text,
  hover always scoped to `:not(:disabled)`, disabled always `opacity: 0.5; cursor: not-allowed`
- Page-specific differences (width, margin, grid placement) go after the `@include`; if you want something shorter or smaller, first ask whether it is really needed
- Existing "modifier class" families on some pages (such as `.actionBtn.actionBtnDanger` in table rows) keep the base + modifier form,
  but a danger modifier **must show red text before hover**, and its hover must also include `:not(:disabled)`
- Danger items inside dropdown menus (PowerMenu, the ⋯ menu in page headers) are menu styles and are not among these six variants

### Forms

Form fields always use the form mixin family from `_mixins.scss`; **do not invent another set of font sizes and paddings inside a page**:

```scss
.field { @include form-field; }                              // vertical container for label + control
.field input, .field select, .field textarea { @include form-control; }
.fieldInvalid.fieldInvalid { @include form-control-invalid; } // fields left empty at submit
```

```jsx
<label className={styles.field}>
  <span>班級名稱</span>
  <input className={invalid ? styles.fieldInvalid : undefined} aria-invalid={invalid} … />
</label>
```

> **Rule 1**: controls are fixed at 36px tall with 14px text, deliberately matching `.btnPrimary` / `.btnSecondary`
> so that fields and buttons on the same row line up. If you want a shorter or smaller form, first ask whether it is really needed;
> do not override `min-height` or `font-size` inside a page.
> The height is computed the same way as for buttons: site-wide line-height 1.6 (14px text → 22.4) + vertical padding 6×2 + border 1×2 ≈ 36px.
> Writing 8px padding makes it 40px; a search box whose border sits on the container uses `min-height: 36px`, not 38px.
> The shared SegmentedControl is also fixed at 36px, so it sits on the same row as fields and buttons without extra height locking.

> **Rule 2**: how fields are arranged (how many columns, which one spans) lives in the page's own grid (`.formGrid`,
> `.createFormGrid`, `.fieldFull`); the mixins are only responsible for what a single field looks like.

> **Rule 3**: a "from–to" pair of values is **one** field, not two. Use a paired control such as `.timePair`,
> label it "Class time", and do not split it into two `.field`s labelled "Start time" and "End time".

> **Rule 4**: textareas always have a **fixed height** — `_reset.scss` already sets `resize: none` globally,
> and the height comes from `rows` in the JSX or `height` / `min-height` in the page CSS; **do not write
> `resize` again** inside a component. Only in special cases (a long-text editor that genuinely needs to be user-resizable) should a page explicitly set
> `resize: vertical` back, so that the exception is visible.

### Switch and checkbox

Enable/disable toggles that **take effect immediately always use the shared `Switch`** (`components/Switch/Switch`); do not use a toggle-icon button (`toggle_on` / `toggle_off`) or a checkbox in their place:

```jsx
<Switch checked={rule.enable !== 0} onChange={(next) => toggle(rule, next)} disabled={busy} ariaLabel={t("…enableSwitch")} title={t("…enableSwitch")} />
```

- **Division of labour**: sends as soon as it flips → `Switch`; a form field that only takes effect after pressing "Save" → checkbox (`.checkRow`). A switch tells users the change is immediate, so do not mix the two
- Look: a 36×20 pill track with a 16px white knob, on = `--color-primary`, off = grey; it is a single `button role="switch"`
- In tables and list rows **show the switch alone, without an "Enabled" caption** (the column header or the row already says what it controls), and pass `ariaLabel` + `title` instead; only pass a visible `label` when it is genuinely needed, saying what the switch controls and not changing with the state
- The current state is shown by the knob position and colour; in a list, a disabled row **swaps its status badge for a red "Disabled"** (e.g. a firewall rule's "Allow" becomes "Disabled") instead of adding a separate "Disabled" badge next to it
- A disabled row dims only its content; **the actions cell holding the switch is not dimmed**, so it is clear it can be turned back on
- Currently used by: the firewall rules in resource details › advanced settings, the rules panel on the firewall page, and the node list in PVE management

### Tables

List-page tables always use the table mixin family from `_mixins.scss`; **do not copy the whole set of styles into a page**:

```scss
.tableWrap { @include table-wrap; }        // glass container + rounded corners + horizontal scrolling
.table     { @include table-base; min-width: 720px; }  // set min-width to fit the content so it scrolls
.th        { @include table-th; }
.tr        { @include table-tr; }          // the base has no hover, see the rule below
.td        { @include table-td; }
```

- Page-specific differences such as column widths, alignment and special cells (`.thRight`, `.tdNowrap`, …) go after the `@include`
- The responsive behaviour is uniformly **horizontal scrolling of the container** (`table-wrap` has `overflow-x: auto` built in); tables are not turned into cards
- When a table is embedded inside an existing card you may use only `table-base` / `table-th` / `table-tr` / `table-td` and omit the outer `table-wrap`

#### Rule 1: a whole-row hover colour means the row is clickable

A whole-row hover colour is an **interaction signal, not decoration**. When the row itself is not clickable (all interaction is on buttons inside the cells),
do not colour the whole row, otherwise it is a false hint that the row can be clicked.

```scss
.tr          { @include table-tr; }            // not clickable: divider only, no hover
.trClickable { @include table-tr-clickable; }  // clickable: cursor + hover together
```

- There is exactly one criterion: **whether the `<tr>` itself has an `onClick`** (or `role` / `tabIndex`)
- The same table may mix both (monitoring page: node rows are clickable, VM rows are not), so the two mixins are stacked rather than taking a boolean parameter
- Do not write `cursor: pointer` or `&:hover { background: … }` yourself — the cursor and the hover would drift apart
- When a different hover colour is needed (for example a group row that already has its own background), override `background` after the `@include`

#### Rule 2: tables whose row count changes need fixed column widths

The default `table-layout: auto` computes column widths from **the content of every row**. As soon as expanding / collapsing adds or removes
**rows with the full set of columns**, the content of every column changes and the whole table jumps.

```scss
.table { @include table-base; table-layout: fixed; min-width: 1080px; }

.colStatus { width: 100px; }   // declare column widths in one place, paired with <colgroup> in the JSX
```

- This is only unnecessary when the expanded row is a **full-width details panel in a `<td colSpan={N}>`** —
  a colspan cell does not take part in individual column width calculation and does not cause jumping
- After switching to `fixed`, remove any `min-width` on cells that was set to "fight auto layout for width";
  it would only make the content overflow the column
- The one column without a width absorbs the remaining space (usually the name column)

#### Rule 3: cell icons must carry information the text does not

`MIcon` is always `aria-hidden`, so screen readers cannot read it. If the information the icon encodes
is already written in the adjacent text (type, category), it is just layout habit — remove it and let the text speak.

### Dropdown menus

- `position: absolute; bottom: calc(100% + 6px); right: 0` (opens upward)
- The parent element needs `position: relative`
- The close animation uses `setTimeout` (130ms) + a CSS `transition`, not `onAnimationEnd`

### Stepper

Flow tabs and setup steps **always use the shared `Stepper`** (`components/Stepper/Stepper`); do not hand-craft chevron segments or draw your own dots.
Currently used in the class workspace, the course environment editor and one-click class creation (`ClassSetupPage`).

```jsx
<Stepper
  ariaLabel={t("...")}
  steps={[{ key, label, done, disabled }]} // dots are numbered 1, 2, 3… in order and become ✓ when done; disabled = a step the wizard cannot jump to yet
  extras={[{ key, label, icon }]}         // optional: tabs that are not steps, placed after the divider
  activeKey={tab}
  onSelect={(key) => ...}
/>
```

- Visually it is a row of "dot + label" placed directly on the page (no background fill, no card), with connecting lines between steps; the first dot aligns with the left edge of the page content
- State is expressed by the dot alone: not done = white with a light-blue border and a number, done = white with a primary border and ✓, current = solid primary with a glow; labels only distinguish current (bold, dark) from the rest
- A connecting line turns primary only when both of its ends have been "reached" (done or current); otherwise it uses the light primary
- The stepper sits directly on the gradient background, **do not use the light grey `--color-border` or a pale solid fill**: it blurs into the background and becomes invisible; always use white with blue-family borders
- Wizard flows: completed steps and the first pending step are clickable (you can go back, fix something and jump straight back), later steps are `disabled`: only blocks clicks, no dimming, and never a button that silently does nothing. Whether a step is reachable or ticked depends on **the actual progress saved to the backend**, not on "whether you walked past it"; when the URL points at a step that cannot be reached yet, fall back to the last reachable step
- Switching wizard steps does not auto-save: when the current step has unsaved changes, call `confirmLeave()` first and discard after confirmation before switching (one-click class creation `ClassSetupPage`)
- Wizard layout (`ClassSetupPage`): below the stepper is "the current step's card | summary column on the right". Every step is the same card (title, body, bottom button row); the button row uses `position: sticky; bottom: 0`, so the card uses `overflow: clip` (`hidden` would turn it into a scroll container and the button row would no longer stick); the card height follows the content, with no `min-height`. Whether to use two columns depends on the actual page width (`container: wizard / inline-size`, two columns only at ≥760px); when there is not enough room the summary collapses into an expandable row above the card; the overview step is itself the summary and does not get the right column
- Tabs that are not steps (class progress after the class is activated, AI) go into `extras`: the dot becomes an icon and has no connecting line; do not force them in as steps 5 and 6
- On phones only the current step keeps its label, the other steps are reduced to dots (labels remain for screen readers); when it still does not fit, scroll horizontally

---

## Animation rules

### Enter animations

```scss
@keyframes slideUp {
  from { opacity: 0; transform: translateY(12px); }
  to   { opacity: 1; transform: translateY(0); }
}
// usage: animation: slideUp 0.18s cubic-bezier(0.25, 0.8, 0.25, 1);
```

```scss
@keyframes fadeIn {
  from { opacity: 0; }
  to   { opacity: 1; }
}
// usage: animation: fadeIn 0.15s ease;
```

### Dialog / popup enter and exit (the standard approach)

Dialogs always enter with "backdrop `fadeIn` + content `slideUp`"; the exit is handled by the shared hook `hooks/useDialogPresence.js` — on close it keeps the DOM for 150ms with the `Out` class applied to play the fade-out, then unmounts:

```jsx
import useDialogPresence from "../hooks/useDialogPresence";

const dialog = useDialogPresence(editTarget);   // a boolean or a data object both work
// while closing, dialog.item keeps the last data so the content does not flicker
{dialog.open && (
  <div className={`${styles.modalOverlay} ${dialog.closing ? styles.modalOverlayOut : ""}`}>
    <EditModal target={dialog.item} … />
  </div>
)}
```

```scss
.modalOverlay {
  /* …positioning and backdrop… */
  animation: fadeIn 0.15s ease;
  transition: opacity 0.15s ease;
}
.modalOverlayOut {
  animation: none;   // override the enter animation so the transition takes over
  opacity: 0;
  pointer-events: none;
}
```

Shared dialog components (such as `ConnectionDialog`, `ReverseProxyRuleModal`) accept a `closing` prop that applies the Out class, controlled by the parent's `useDialogPresence`. Self-contained dialogs (such as `VncDialog`, `TerminalDialog`) call `setClosing(true)` internally and then `setTimeout(onClose, 150)`.

### Exit animations (closing)

Prefer **`setTimeout` + a CSS `transition`**; do not use `onAnimationEnd` (it has known edge-case problems):

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
  animation: none;   // override the enter animation so the transition takes over
  opacity: 0;
  transform: translateY(6px);
  pointer-events: none;
}
```

---

## Dark mode

Theme switching is implemented through the `body.dark` class; every colour already has a light and a dark value defined in `_themes.scss`.

Component SCSS always uses the CSS custom properties, **so you do not need to write your own `body.dark &` overrides**.

If a component has special dark-mode needs:

```scss
// use the data-theme attribute (some components already use this approach)
[data-theme="dark"] & {
  color: #xxx;
}

// or use body.dark
:global(body.dark) & {
  color: #xxx;
}
```

---

## z-index layers

| Layer | Value | Purpose |
|------|-----|------|
| Base card | 1 | Regular cards |
| Card hover / menus | 50 | Dropdown menus inside a container |
| Sticky header | 100 | Page-top navigation bar |
| Portalled floating menus | 150 | Dropdowns portalled to body (such as `components/PowerMenu`) |
| Dialog / Modal | 300 | Full-page overlay dialogs |
| Toast / Tooltip | 400 | Notifications, hints |
| UserGuide tour | 3000–3199 | The tour spotlight layer must sit above everything, dialogs included (overlay 3100, demo window 3099; there is also the floating help button at 90, between menus and the sticky header). This range is reserved for UserGuide; regular components must not use it |

> ⚠️ Note: elements with `backdrop-filter` or `transform` create a new stacking context, so the `z-index` of their children cannot escape to the outside. If a dropdown is hidden behind another card, check whether the parent has one of these properties.
>
> A glass surface (`glass-surface`) combined with an `overflow: hidden` container will also **clip** an absolutely positioned menu that overflows. If a floating layer may extend beyond its container, `createPortal` it to `document.body` and position it with `position: fixed` (example: `components/PowerMenu`), and give it an opaque `var(--color-surface)` background, otherwise the list content underneath shows through.
