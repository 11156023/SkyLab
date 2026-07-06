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
  const shades = { primary, primaryLight: light, primaryDark: dark };
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
    },
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
    },
  };
}
