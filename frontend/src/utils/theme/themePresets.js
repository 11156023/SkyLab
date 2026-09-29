/**
 * themePresets.js
 * 系統配好的配色主題：一組主題 = 主色 + 背景色 + 背景花色，
 * 最前面是「預設」（系統原本的藍色三色暈染，選了主題想回原樣時用），後面四個系列。
 * 不帶風格（毛玻璃／液態玻璃／白底）與明暗模式——那是使用者的裝置偏好，套主題不覆寫。
 *
 * 主色一律挑白字對比 ≥ 4.5:1 的深度：主色除了當按鈕底色（上面放白字），
 * 也會當白底卡片上的文字與圖示色，太亮的主色兩邊都會看不清楚（測試會把關）。
 * backgroundColor 空字串 = 背景跟隨主色（同 THEME_DEFAULTS）。
 */
import { derivePrimaryShades, deriveBackgroundPalettes, derivePrimaryTheme } from "./derivePrimaryShades";
import { THEME_DEFAULTS } from "./themePreferenceStore";

/** 預設外觀的三色暈染（與 _themes.scss 的 --color-bg-gradient-blue / -yellow / -green 相同，明暗各一套） */
const CLASSIC_STOPS = {
  light: ["#c1daff", "#feffed", "#edfff6"],
  dark: ["#1a2a48", "#2a2618", "#0f2a22"],
};

export const THEME_PRESET_FAMILIES = [
  {
    id: "default",
    labelKey: "AppearanceTab.presetFamilyDefault",
    presets: [
      {
        id: "default-classic",
        labelKey: "AppearanceTab.presetDefaultClassic",
        primaryColor: THEME_DEFAULTS.primaryColor,
        backgroundColor: THEME_DEFAULTS.backgroundColor,
        backgroundId: THEME_DEFAULTS.backgroundId,
        // 主色、背景都沒自訂時，背景畫的是原始三色暈染而不是主色漸層
        classic: true,
      },
    ],
  },
  {
    id: "morandi",
    labelKey: "AppearanceTab.presetFamilyMorandi",
    presets: [
      { id: "morandi-pink", labelKey: "AppearanceTab.presetMorandiPink", primaryColor: "#926a71", backgroundColor: "#c7b2a8", backgroundId: "preset-2" },
      { id: "morandi-blue", labelKey: "AppearanceTab.presetMorandiBlue", primaryColor: "#67768e", backgroundColor: "#c7b7a8", backgroundId: "preset-2" },
      { id: "morandi-green", labelKey: "AppearanceTab.presetMorandiGreen", primaryColor: "#5a7c6b", backgroundColor: "#c7c7a8", backgroundId: "preset-2" },
      { id: "morandi-purple", labelKey: "AppearanceTab.presetMorandiPurple", primaryColor: "#836b94", backgroundColor: "#c7a8b8", backgroundId: "preset-2" },
      { id: "morandi-beige", labelKey: "AppearanceTab.presetMorandiBeige", primaryColor: "#84715f", backgroundColor: "#c7bda8", backgroundId: "preset-2" },
    ],
  },
  {
    id: "candy",
    labelKey: "AppearanceTab.presetFamilyCandy",
    presets: [
      { id: "candy-strawberry", labelKey: "AppearanceTab.presetCandyStrawberry", primaryColor: "#c84376", backgroundColor: "", backgroundId: "preset-3" },
      { id: "candy-soda", labelKey: "AppearanceTab.presetCandySoda", primaryColor: "#327aae", backgroundColor: "", backgroundId: "preset-3" },
      { id: "candy-mint", labelKey: "AppearanceTab.presetCandyMint", primaryColor: "#268264", backgroundColor: "", backgroundId: "preset-3" },
      { id: "candy-grape", labelKey: "AppearanceTab.presetCandyGrape", primaryColor: "#9449ca", backgroundColor: "", backgroundId: "preset-3" },
      { id: "candy-tangerine", labelKey: "AppearanceTab.presetCandyTangerine", primaryColor: "#ae6032", backgroundColor: "", backgroundId: "preset-3" },
    ],
  },
  {
    id: "nature",
    labelKey: "AppearanceTab.presetFamilyNature",
    presets: [
      { id: "nature-forest", labelKey: "AppearanceTab.presetNatureForest", primaryColor: "#3b7657", backgroundColor: "", backgroundId: "preset-2" },
      { id: "nature-ocean", labelKey: "AppearanceTab.presetNatureOcean", primaryColor: "#1f6f8b", backgroundColor: "#5aa7d1", backgroundId: "auto-gradient" },
      { id: "nature-sunset", labelKey: "AppearanceTab.presetNatureSunset", primaryColor: "#b9542a", backgroundColor: "", backgroundId: "preset-2" },
      { id: "nature-lavender", labelKey: "AppearanceTab.presetNatureLavender", primaryColor: "#7157b0", backgroundColor: "", backgroundId: "preset-2" },
    ],
  },
  {
    id: "calm",
    labelKey: "AppearanceTab.presetFamilyCalm",
    presets: [
      { id: "calm-graphite", labelKey: "AppearanceTab.presetCalmGraphite", primaryColor: "#4b5563", backgroundColor: "#9aa3ad", backgroundId: "auto-gradient" },
      { id: "calm-milk-tea", labelKey: "AppearanceTab.presetCalmMilkTea", primaryColor: "#8a6547", backgroundColor: "#d4b896", backgroundId: "auto-gradient" },
      { id: "calm-midnight", labelKey: "AppearanceTab.presetCalmMidnight", primaryColor: "#2a3f6f", backgroundColor: "#8fa3c7", backgroundId: "auto-gradient" },
      { id: "calm-burgundy", labelKey: "AppearanceTab.presetCalmBurgundy", primaryColor: "#7e303f", backgroundColor: "#c9a3ab", backgroundId: "auto-gradient" },
    ],
  },
];

/** 目前設定剛好等於哪一組主題（主色、背景色、花色三項都相同）；自己改過任何一項就回傳 null */
export function findActivePreset({ primaryColor, backgroundColor, backgroundId }) {
  const primary = (primaryColor ?? "").toLowerCase();
  const background = (backgroundColor ?? "").toLowerCase();
  for (const family of THEME_PRESET_FAMILIES) {
    for (const preset of family.presets) {
      if (
        preset.primaryColor === primary &&
        preset.backgroundColor === background &&
        preset.backgroundId === backgroundId
      ) {
        return { family, preset };
      }
    }
  }
  return null;
}

/**
 * 主題按鈕的配色：底色＝該主題的背景花色（淺色／深色模式各一套），
 * 字色＝該主題的主要文字色，圓點＝主色——按鈕本身就是這組主題的縮影。
 * 花色色碼與 ThemeContext 寫進 :root 的算法相同（背景基準色＝背景色，未設定則為主色），
 * 漸層幾何比照 BACKGROUND_OPTIONS 的 preview。
 */
export function presetColors(preset, theme = "light") {
  const base = preset.backgroundColor || preset.primaryColor;
  const mode = theme === "dark" ? "dark" : "light";
  let stops;
  if (preset.classic) {
    stops = CLASSIC_STOPS[mode];
  } else if (preset.backgroundId === "preset-2") {
    stops = deriveBackgroundPalettes(base).duo[mode];
  } else if (preset.backgroundId === "preset-3") {
    stops = deriveBackgroundPalettes(base).tri[mode];
  } else {
    const shades = derivePrimaryShades(base);
    stops =
      mode === "dark"
        ? ["#0d1117", derivePrimaryShades(shades.dark).dark]
        : [derivePrimaryShades(shades.light).light, shades.light];
  }
  const background =
    stops.length === 3
      ? `linear-gradient(135deg, ${stops[0]}, ${stops[1]} 50%, ${stops[2]})`
      : `linear-gradient(135deg, ${stops[0]}, ${stops[1]})`;
  return {
    background,
    stops,
    text: derivePrimaryTheme(preset.primaryColor)[mode].textPrimary,
    primary: preset.primaryColor,
  };
}
