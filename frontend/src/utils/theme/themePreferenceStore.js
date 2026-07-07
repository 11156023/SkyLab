/**
 * themePreferenceStore.js
 * 主題偏好（明暗模式 / 主色 / 風格）的讀寫封裝。
 *
 * 目前實作為 localStorage；之後若要改存後端資料庫，
 * 只需改寫此模組內部（例如 load / save 改為 API 呼叫），
 * 呼叫端（ThemeContext）的介面不需變動。
 */

const KEY_MODE = "SkyLab-theme";
const KEY_PRIMARY = "SkyLab-theme-primary";
const KEY_STYLE = "SkyLab-theme-style";
const KEY_BACKGROUND = "SkyLab-theme-background";
const KEY_BACKGROUND_COLOR = "SkyLab-theme-background-color";

export const THEME_DEFAULTS = Object.freeze({
  mode: "system",
  primaryColor: "#5471bf",
  style: "glass",
  backgroundId: "auto-gradient",
  backgroundColor: "", // 空字串 = 跟隨主色
});

function read(key, fallback) {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

export const themePreferenceStore = {
  /** 讀取全部偏好，缺漏的欄位以預設值補上 */
  load() {
    return {
      mode: read(KEY_MODE, THEME_DEFAULTS.mode),
      primaryColor: read(KEY_PRIMARY, THEME_DEFAULTS.primaryColor),
      style: read(KEY_STYLE, THEME_DEFAULTS.style),
      backgroundId: read(KEY_BACKGROUND, THEME_DEFAULTS.backgroundId),
      backgroundColor: read(KEY_BACKGROUND_COLOR, THEME_DEFAULTS.backgroundColor),
    };
  },

  /** 局部更新，只寫入有傳的欄位 */
  save({ mode, primaryColor, style, backgroundId, backgroundColor } = {}) {
    try {
      if (mode !== undefined) localStorage.setItem(KEY_MODE, mode);
      if (primaryColor !== undefined) localStorage.setItem(KEY_PRIMARY, primaryColor);
      if (style !== undefined) localStorage.setItem(KEY_STYLE, style);
      if (backgroundId !== undefined) localStorage.setItem(KEY_BACKGROUND, backgroundId);
      if (backgroundColor !== undefined) localStorage.setItem(KEY_BACKGROUND_COLOR, backgroundColor);
    } catch {
      /* localStorage 不可用（如隱私模式）時靜默略過 */
    }
  },
};
