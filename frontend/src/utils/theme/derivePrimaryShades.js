/**
 * derivePrimaryShades.js
 * 由主色自動衍生 light / dark 色階：
 * 先轉成 HSL，調整 lightness 後轉回 hex。
 * 極亮 / 極暗的輸入色以 clamp 保證色階仍落在可視範圍，
 * 且 light 色階恆比 dark 色階亮。
 */

const LIGHT_SHIFT = 14; // light 色階提高的 lightness（%）
const DARK_SHIFT = 16; // dark 色階降低的 lightness（%）
const MIN_LIGHTNESS = 6;
const MAX_LIGHTNESS = 94;

const HEX_RE = /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i;

/** 正規化為小寫 #rrggbb，不合法的色碼丟出 TypeError */
export function normalizeHex(hex) {
  if (typeof hex !== "string" || !HEX_RE.test(hex.trim())) {
    throw new TypeError(`無效的色碼: ${hex}`);
  }
  let value = hex.trim().slice(1).toLowerCase();
  if (value.length === 3) {
    value = value.split("").map((c) => c + c).join("");
  }
  return `#${value}`;
}

/** #rrggbb → { h: 0-360, s: 0-100, l: 0-100 } */
export function hexToHsl(hex) {
  const value = normalizeHex(hex);
  const r = parseInt(value.slice(1, 3), 16) / 255;
  const g = parseInt(value.slice(3, 5), 16) / 255;
  const b = parseInt(value.slice(5, 7), 16) / 255;

  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  const d = max - min;

  let h = 0;
  let s = 0;
  if (d !== 0) {
    s = d / (1 - Math.abs(2 * l - 1));
    switch (max) {
      case r: h = ((g - b) / d) % 6; break;
      case g: h = (b - r) / d + 2; break;
      default: h = (r - g) / d + 4;
    }
    h *= 60;
    if (h < 0) h += 360;
  }

  return { h, s: s * 100, l: l * 100 };
}

/** { h: 0-360, s: 0-100, l: 0-100 } → #rrggbb */
export function hslToHex(h, s, l) {
  const sn = s / 100;
  const ln = l / 100;
  const k = (n) => (n + h / 30) % 12;
  const a = sn * Math.min(ln, 1 - ln);
  const f = (n) =>
    ln - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  const toHex = (v) => Math.round(v * 255).toString(16).padStart(2, "0");
  return `#${toHex(f(0))}${toHex(f(8))}${toHex(f(4))}`;
}

/**
 * 主色 → { primary, light, dark } 三個 hex 色碼。
 * light / dark 供 --color-primary-light / --color-primary-dark 使用。
 */
export function derivePrimaryShades(hex) {
  const primary = normalizeHex(hex);
  const { h, s, l } = hexToHsl(primary);
  const clamp = (v) => Math.min(MAX_LIGHTNESS, Math.max(MIN_LIGHTNESS, v));
  return {
    primary,
    light: hslToHex(h, s, clamp(l + LIGHT_SHIFT)),
    dark: hslToHex(h, s, clamp(l - DARK_SHIFT)),
  };
}

/**
 * 基準色 → 柔和雙色 / 對角三色 背景花色（明暗各一套）。
 * 沿用基準色的色相做旋轉（雙色 -70°、三色 ±120°），
 * 淺色套組壓成粉彩（高亮度、限飽和），深色套組壓暗。
 */
export function deriveBackgroundPalettes(hex) {
  const { h, s } = hexToHsl(normalizeHex(hex));
  const pastel = (hue) => hslToHex((hue + 360) % 360, Math.min(s, 70), 93);
  const deep = (hue) => hslToHex((hue + 360) % 360, Math.min(s, 40), 13);
  const duoHues = [h, h - 70];
  const triHues = [h + 120, h, h - 120];
  return {
    duo: { light: duoHues.map(pastel), dark: duoHues.map(deep) },
    tri: { light: triHues.map(pastel), dark: triHues.map(deep) },
  };
}

/* ── 深色元件色票（快速練習資料夾、首頁機器卡的終端畫面）──
   _themes.scss 原本寫死一組深藍；換主色時改沿用主色色相，
   每個色票都調到跟原本那格深藍「一樣的相對亮度」——不是一樣的 HSL 亮度：
   綠、橘這類色相同樣 L 看起來亮很多，照 L 算會讓資料夾後片比前蓋還亮、白字看不清。
   飽和度依「主色飽和度 ÷ 預設主色飽和度」等比例縮放（上限 1.3 倍，避免過艷），
   灰調主色（石墨、莫蘭迪）就得到灰調的資料夾與終端。有文字的色票再用對比度把關。 */

/** 預設主色 #5471bf 的飽和度：原本那組深藍的飽和度是以它為基準 */
const DEEP_BASE_SATURATION = 45.5;

/** 原本寫死的預設值（與 _themes.scss 相同），當作各色票的亮度與飽和度基準 */
const DEEP_BASE = {
  folder: {
    light: { backTop: "#34549f", backBottom: "#22407f", frontLight: "#5a74bf", frontDark: "#3b58a4", ink: "#2b4d98", shadow: "#142654" },
    dark: { backTop: "#2a4585", backBottom: "#1b2f5e", frontLight: "#5270bb", frontDark: "#334f96", paper: "#dfe6f4", ink: "#253f7c" },
  },
  terminal: { bg: "#1a2a48", bgOff: "#262e40", text: "#d9e8ff", textOff: "#b4bdd0", key: "#89a5e0", dim: "#8196c4" },
};

function relativeLuminance(hex) {
  const value = normalizeHex(hex);
  const [r, g, b] = [1, 3, 5]
    .map((i) => parseInt(value.slice(i, i + 2), 16) / 255)
    .map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG 對比度 */
export function contrastRatio(a, b) {
  const [hi, lo] = [relativeLuminance(a), relativeLuminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** 固定色相與飽和度，找出相對亮度與 reference 相同的顏色（亮度對 L 單調，二分搜尋） */
function matchLuminance(h, s, reference) {
  const target = relativeLuminance(reference);
  let lo = 0;
  let hi = 100;
  for (let i = 0; i < 24; i += 1) {
    const mid = (lo + hi) / 2;
    if (relativeLuminance(hslToHex(h, s, mid)) < target) lo = mid;
    else hi = mid;
  }
  return hslToHex(h, s, (lo + hi) / 2);
}

/** 往 direction（-1 調深、+1 調亮）推，直到與 against 的對比達 ratio */
function withContrast(hex, against, ratio, direction) {
  const { h, s, l } = hexToHsl(hex);
  let lightness = l;
  let result = hex;
  while (contrastRatio(result, against) < ratio && lightness > 1 && lightness < 99) {
    lightness += direction * 0.5;
    result = hslToHex(h, s, lightness);
  }
  return result;
}

/**
 * 主色 → 資料夾與終端的色票（--color-folder-* / --color-terminal-*）。
 * 資料夾分明暗兩套（深色模式整體壓暗、紙張不用純白）；終端兩種模式都是深色螢幕，共用一套。
 * 陰影只在淺色模式帶色調，深色模式沿用 _themes.scss 的黑色陰影，所以 dark 不回傳 folderShadow。
 */
export function deriveDeepPalette(hex, mode = "light") {
  const { h, s } = hexToHsl(normalizeHex(hex));
  const k = Math.min(1.3, s / DEEP_BASE_SATURATION);
  // 沿用主色色相，飽和度照基準等比例縮放，亮度對齊基準色
  const like = (reference) => matchLuminance(h, Math.min(100, hexToHsl(reference).s * k), reference);
  const dark = mode === "dark";
  const folder = DEEP_BASE.folder[dark ? "dark" : "light"];
  const term = DEEP_BASE.terminal;

  const paper = dark ? like(folder.paper) : "#ffffff";
  const terminalBg = like(term.bg);
  const terminalBgOff = like(term.bgOff);
  const terminalKey = withContrast(like(term.key), terminalBg, 4.5, 1);

  return {
    folderBackTop: like(folder.backTop),
    folderBackBottom: like(folder.backBottom),
    // 前蓋上是白字
    folderFrontLight: withContrast(like(folder.frontLight), "#ffffff", 4.5, -1),
    folderFrontDark: like(folder.frontDark),
    folderPaper: paper,
    folderInk: withContrast(like(folder.ink), paper, 4.5, -1),
    ...(dark ? {} : { folderShadow: `color-mix(in srgb, ${like(folder.shadow)} 30%, transparent)` }),
    terminalBg,
    terminalBgOff,
    terminalText: withContrast(like(term.text), terminalBg, 7, 1),
    terminalTextOff: withContrast(like(term.textOff), terminalBgOff, 4.5, 1),
    terminalKey,
    terminalDim: withContrast(like(term.dim), terminalBg, 4.5, 1),
    terminalCell: `color-mix(in srgb, ${terminalKey} 26%, transparent)`,
  };
}

/**
 * 主色 → 明暗兩種模式的完整配色。
 * 除了 primary 色階與文字色，介面上所有帶主色色調的用色
 * （hover、邊框、分隔線、次要文字、頁面底色、流程畫布底）
 * 也一併沿用主色色相衍生——各項的飽和上限與亮度
 * 對齊 _themes.scss 原始藍色系的量測值。
 * 文字色只調 lightness：淺色模式壓在可讀範圍
 * （避免極亮主色產生看不見的文字），深色模式固定高亮度。
 */
export function derivePrimaryTheme(hex) {
  const { primary, light, dark } = derivePrimaryShades(hex);
  const { h, s, l } = hexToHsl(primary);
  const between = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const tone = (maxS, lig) => hslToHex(h, Math.min(s, maxS), lig);
  // 主色上的文字：極亮主色（如純白）配白字會看不見，
  // 依主色亮度自動選深墨色或白色，兩種明暗模式共用
  const textOnPrimary = l >= 62 ? tone(60, 16) : "#ffffff";
  const shades = { primary, primaryLight: light, primaryDark: dark, textOnPrimary };
  return {
    light: {
      ...shades,
      text: hslToHex(h, s, between(l, 25, 60)),
      textPrimary: hslToHex(h, s, between(l - 12, 18, 45)),
      textSecondary: hslToHex(h, s, between(l + 4, 30, 66)),
      textMuted: tone(20, 59),
      hover: tone(80, 95),
      border: tone(40, 93),
      divider: tone(45, 96),
      bgBase: tone(85, 95),
      flowBg: `color-mix(in srgb, ${tone(60, 84)} 32%, transparent)`,
      // 陰影色：預設主色時分別約為 #435a95 / #1f2c5a（_themes.scss 原本寫死的兩個深藍）
      shadow: tone(38, 42),
      shadowDeep: tone(49, 24),
      // 放在卡片上的主色文字／邊框（--color-primary-on-surface）：淺色模式用深階，對白底至少 4.5:1
      primaryOnSurface: withContrast(dark, "#ffffff", 4.5, -1),
      deep: deriveDeepPalette(hex, "light"),
    },
    // 深色模式的陰影是黑色，不帶主色色調
    dark: {
      ...shades,
      text: hslToHex(h, Math.min(s, 46), 88),
      textPrimary: hslToHex(h, s, 96),
      textSecondary: hslToHex(h, s, 92),
      textMuted: tone(18, 43),
      hover: tone(30, 17),
      border: tone(25, 21),
      divider: tone(25, 17),
      bgBase: tone(30, 7),
      flowBg: `color-mix(in srgb, ${tone(26, 28)} 47%, transparent)`,
      // 深色模式用淺階，對深色卡片底（#161b26）至少 4.5:1
      primaryOnSurface: withContrast(light, "#161b26", 4.5, 1),
      deep: deriveDeepPalette(hex, "dark"),
    },
  };
}
