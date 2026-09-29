import { useTranslation } from "react-i18next";
import MIcon from "../../../components/MIcon";
import { useTheme } from "../../../contexts/ThemeContext";
import { THEME_PRESET_FAMILIES, findActivePreset, presetColors } from "../../../utils/theme/themePresets";
import styles from "./AccountSettingsPage.module.scss";

/**
 * ThemePresetList — 配色主題清單（帳號設定的主題卡片與首次登入引導共用）。
 * 四個系列全部攤開（不藏在分頁裡），每顆按鈕就是那組主題的縮影：
 * 底色＝主題背景花色、字色＝主題文字色、圓點＝主色，跟著目前明暗模式換成對應的一套。
 * 點一下同時套用主色、背景色、背景花色（帳號設定上方外觀卡片的值跟著變）；
 * 設定剛好等於某組時該組呈選中，自己改過任何一項就都不選中。
 */
export function ThemePresetList() {
  const { t } = useTranslation("personal");
  const {
    theme,
    primaryColor,
    setPrimaryColor,
    backgroundColor,
    setBackgroundColor,
    backgroundId,
    setBackgroundId,
  } = useTheme();
  const active = findActivePreset({ primaryColor, backgroundColor, backgroundId });

  function applyPreset(preset) {
    setPrimaryColor(preset.primaryColor);
    setBackgroundColor(preset.backgroundColor);
    setBackgroundId(preset.backgroundId);
  }

  return (
    <div className={styles.presetFamilies}>
      {THEME_PRESET_FAMILIES.map((family) => (
        <section key={family.id} className={styles.presetFamily}>
          <h3 className={styles.groupTitle}>{t(family.labelKey)}</h3>
          <div className={styles.presetRow} role="group" aria-label={t(family.labelKey)}>
            {family.presets.map((preset) => {
              const selected = active?.preset.id === preset.id;
              const colors = presetColors(preset, theme);
              return (
                <button
                  key={preset.id}
                  type="button"
                  className={`${styles.presetBtn} ${selected ? styles.presetBtnActive : ""}`}
                  style={{
                    "--preset-bg": colors.background,
                    "--preset-text": colors.text,
                    "--preset-primary": colors.primary,
                  }}
                  aria-pressed={selected}
                  onClick={() => applyPreset(preset)}
                >
                  {selected ? (
                    <MIcon name="check_circle" size={16} className={styles.presetCheck} />
                  ) : (
                    <i className={styles.presetDot} aria-hidden="true" />
                  )}
                  {t(preset.labelKey)}
                </button>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}

/** 帳號設定「外觀」分頁下方的配色主題卡片 */
export default function ThemePresetsCard() {
  const { t } = useTranslation("personal");
  return (
    <div className={styles.card}>
      <h2 className={styles.cardTitle}>{t("AppearanceTab.groupPresets")}</h2>
      <ThemePresetList />
    </div>
  );
}
