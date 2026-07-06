import { createContext, useContext, useEffect, useState } from "react";
import { themePreferenceStore, THEME_DEFAULTS } from "../utils/theme/themePreferenceStore";
import {
  deriveBackgroundPalettes,
  derivePrimaryShades,
  derivePrimaryTheme,
} from "../utils/theme/derivePrimaryShades";

const ThemeContext = createContext(null);

/** 明暗模式選項（統一在此 export，供 Sidebar / AppearanceTab 等共用） */
export const THEME_OPTIONS = [
  { key: "light",  label: "淺色", icon: "light_mode" },
  { key: "dark",   label: "深色", icon: "dark_mode" },
  { key: "system", label: "系統", icon: "monitor" },
];

/** 介面風格選項：玻璃質感為預設 */
export const STYLE_OPTIONS = [
  { key: "glass",  label: "毛玻璃質感", icon: "blur_on" },
  { key: "liquid", label: "液態玻璃",   icon: "opacity" },
  { key: "white",  label: "白底",     icon: "panorama_fish_eye" },
  { key: "black",  label: "黑底",     icon: "lens" },
];

/**
 * 背景花色選項，與風格完全無關的一組；第一個「跟隨主色」(auto-gradient) 為預設。
 * 色碼由基準色衍生、明暗模式自動切換（_backgrounds.scss 的別名處理），
 * preview 為縮圖用的 CSS background，直接吃 :root / body 上的衍生變數。
 */
export const BACKGROUND_OPTIONS = [
  {
    id: "auto-gradient",
    label: "跟隨主色",
    preview: "linear-gradient(135deg, var(--color-bg-primary-soft), var(--color-bg-primary-tint))",
  },
  {
    id: "preset-2",
    label: "柔和雙色",
    preview: "linear-gradient(135deg, var(--color-bg-duo-1), var(--color-bg-duo-2))",
  },
  {
    id: "preset-3",
    label: "對角三色",
    preview:
      "linear-gradient(135deg, var(--color-bg-tri-1), var(--color-bg-tri-2) 50%, var(--color-bg-tri-3))",
  },
];

export { THEME_DEFAULTS };

const PRIMARY_STYLE_TAG_ID = "skylab-primary-theme";

function getSystemTheme() {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function paletteToCss(p) {
  return [
    `--color-primary: ${p.primary};`,
    `--color-primary-light: ${p.primaryLight};`,
    `--color-primary-dark: ${p.primaryDark};`,
    `--color-text: ${p.text};`,
    `--color-text-primary: ${p.textPrimary};`,
    `--color-text-secondary: ${p.textSecondary};`,
    `--color-text-muted: ${p.textMuted};`,
    `--color-hover: ${p.hover};`,
    `--color-border: ${p.border};`,
    `--color-divider: ${p.divider};`,
    `--color-bg-base: ${p.bgBase};`,
    `--color-info: ${p.primary};`,
    `--color-flow-bg: ${p.flowBg};`,
  ].join(" ");
}

/**
 * 非預設主色時注入 <style>，讓整個介面帶主色色調的用色一起換：
 * primary 色階、文字、hover、邊框、分隔線、頁面底色、info、
 * 流程畫布底（狀態語意色 綠/黃/紅 與白黑基底不動）：
 * - body:      淺色模式配色
 * - body.dark: 深色模式配色（高亮度、帶主色色調的文字）
 * 預設主色則移除 <style>，讓 _themes.scss 的原始配色生效。
 */
function applyPrimaryTheme(primaryColor) {
  const existing = document.getElementById(PRIMARY_STYLE_TAG_ID);
  let theme = null;
  if (primaryColor && primaryColor.toLowerCase() !== THEME_DEFAULTS.primaryColor) {
    try {
      theme = derivePrimaryTheme(primaryColor);
    } catch {
      theme = null;
    }
  }
  if (!theme) {
    existing?.remove();
    return;
  }
  const tag = existing ?? document.createElement("style");
  tag.id = PRIMARY_STYLE_TAG_ID;
  tag.textContent = [
    `body { ${paletteToCss(theme.light)} }`,
    `body.dark { ${paletteToCss(theme.dark)} }`,
  ].join("\n");
  // 每次都 append 到最後，確保排在所有 stylesheet 之後（同特異度靠順序勝出）
  document.head.appendChild(tag);
}

export function ThemeProvider({ children }) {
  const [initial] = useState(() =>
    typeof window === "undefined" ? THEME_DEFAULTS : themePreferenceStore.load()
  );
  const [mode, setMode] = useState(initial.mode);
  const [primaryColor, setPrimaryColor] = useState(initial.primaryColor);
  const [style, setStyle] = useState(initial.style);
  const [backgroundId, setBackgroundId] = useState(initial.backgroundId);
  // 背景漸層基準色：空字串 = 跟隨主色，有值時與主色脫鉤
  const [backgroundColor, setBackgroundColor] = useState(initial.backgroundColor);

  // 解析出實際套用的 theme（light / dark）
  const [resolvedTheme, setResolvedTheme] = useState(() => {
    if (typeof window === "undefined") return "light";
    return initial.mode === "system" ? getSystemTheme() : initial.mode;
  });

  useEffect(() => {
    themePreferenceStore.save({ mode });
    if (mode === "system") {
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      const handler = (e) => {
        const t = e.matches ? "dark" : "light";
        setResolvedTheme(t);
        document.body.classList.toggle("dark", e.matches);
      };
      const initialTheme = mq.matches ? "dark" : "light";
      setResolvedTheme(initialTheme);
      document.body.classList.toggle("dark", mq.matches);
      mq.addEventListener("change", handler);
      return () => mq.removeEventListener("change", handler);
    }
    setResolvedTheme(mode);
    document.body.classList.toggle("dark", mode === "dark");
  }, [mode]);

  // 主色：非預設色時注入樣式覆寫 primary 色階與文字用色
  useEffect(() => {
    themePreferenceStore.save({ primaryColor });
    applyPrimaryTheme(primaryColor);
  }, [primaryColor]);

  // 背景花色的衍生色階，全部寫入 :root 供 _backgrounds.scss 使用。
  // 基準色預設跟隨主色，backgroundColor 有值時改用它（與主色分開設定）：
  // - 跟隨主色：淡階、最淡階（淡階再衍生一次）、最深階（深階再衍生一次）
  // - 柔和雙色 / 對角三色：色相旋轉出的粉彩／壓暗套組（明暗模式各一套，
  //   body.dark 下由 _backgrounds.scss 的別名切換）
  useEffect(() => {
    themePreferenceStore.save({ backgroundColor });
    let base = backgroundColor || primaryColor;
    let shades;
    try {
      shades = derivePrimaryShades(base);
    } catch {
      base = THEME_DEFAULTS.primaryColor;
      shades = derivePrimaryShades(base);
    }
    const palettes = deriveBackgroundPalettes(base);
    const root = document.documentElement.style;
    root.setProperty("--color-bg-primary-tint", shades.light);
    root.setProperty("--color-bg-primary-soft", derivePrimaryShades(shades.light).light);
    root.setProperty("--color-bg-primary-deep", derivePrimaryShades(shades.dark).dark);
    palettes.duo.light.forEach((c, i) => root.setProperty(`--color-bg-duo-light-${i + 1}`, c));
    palettes.duo.dark.forEach((c, i) => root.setProperty(`--color-bg-duo-dark-${i + 1}`, c));
    palettes.tri.light.forEach((c, i) => root.setProperty(`--color-bg-tri-light-${i + 1}`, c));
    palettes.tri.dark.forEach((c, i) => root.setProperty(`--color-bg-tri-dark-${i + 1}`, c));
  }, [primaryColor, backgroundColor]);

  // 風格：以 data-style 套用（glass 也明確標上，供背景花色選擇器組合）
  useEffect(() => {
    themePreferenceStore.save({ style });
    document.body.setAttribute("data-style", style);
  }, [style]);

  // 背景：與風格無關，直接以 data-bg 套用
  // （實際花色集中定義在 _backgrounds.scss）。
  // 完全未自訂時（跟隨主色 + 預設主色 + 未另選背景色）
  // 不掛 data-bg，讓 global.scss 的原始三色暈染呈現 ——
  // 系統預設外觀維持最初的樣子
  useEffect(() => {
    if (!BACKGROUND_OPTIONS.some((opt) => opt.id === backgroundId)) {
      setBackgroundId(THEME_DEFAULTS.backgroundId);
      return;
    }
    themePreferenceStore.save({ backgroundId });
    const untouched =
      backgroundId === "auto-gradient" &&
      !backgroundColor &&
      primaryColor.toLowerCase() === THEME_DEFAULTS.primaryColor;
    if (untouched) document.body.removeAttribute("data-bg");
    else document.body.setAttribute("data-bg", backgroundId);
  }, [backgroundId, primaryColor, backgroundColor]);

  // 白底只能配淺色模式、黑底只能配深色模式；
  // 明暗模式切換（含系統模式跟隨 OS）時自動換成對應的底
  useEffect(() => {
    if (style === "white" && resolvedTheme === "dark") setStyle("black");
    else if (style === "black" && resolvedTheme === "light") setStyle("white");
  }, [style, resolvedTheme]);

  function resetToDefaults() {
    setMode(THEME_DEFAULTS.mode);
    setPrimaryColor(THEME_DEFAULTS.primaryColor);
    setStyle(THEME_DEFAULTS.style);
    setBackgroundId(THEME_DEFAULTS.backgroundId);
    setBackgroundColor(THEME_DEFAULTS.backgroundColor);
  }

  return (
    <ThemeContext.Provider
      value={{
        theme: resolvedTheme,
        mode,
        setMode,
        primaryColor,
        setPrimaryColor,
        style,
        setStyle,
        backgroundId,
        setBackgroundId,
        backgroundColor,
        setBackgroundColor,
        resetToDefaults,
      }}
    >
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}
