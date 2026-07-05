import { useEffect, useState } from "react";
import MIcon from "../../../components/MIcon";
import {
  useTheme,
  THEME_OPTIONS,
  STYLE_OPTIONS,
  BACKGROUND_OPTIONS,
} from "../../../contexts/ThemeContext";
import { normalizeHex } from "../../../utils/theme/derivePrimaryShades";
import styles from "./AccountSettingsPage.module.scss";

/* ── 外觀 ───────────────────────────────────────────── */

/** 可直接輸入 HEX 色碼的欄位，失焦或 Enter 時套用（無效輸入還原） */
function HexInput({ value, onChange, ariaLabel }) {
  const [draft, setDraft] = useState(value);
  const [focused, setFocused] = useState(false);

  useEffect(() => {
    if (!focused) setDraft(value);
  }, [value, focused]);

  function commit() {
    try {
      onChange(normalizeHex(draft));
    } catch {
      setDraft(value);
    }
  }

  return (
    <input
      type="text"
      className={styles.hexInput}
      value={draft}
      onFocus={() => setFocused(true)}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        setFocused(false);
        commit();
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit();
      }}
      maxLength={7}
      spellCheck={false}
      aria-label={ariaLabel}
    />
  );
}

function OptionGroup({ label, options, value, onSelect }) {
  return (
    <div className={styles.field}>
      <span>{label}</span>
      <div className={styles.optionRow}>
        {options.map((opt) => (
          <button
            key={opt.key}
            type="button"
            className={value === opt.key ? styles.optionBtnActive : styles.optionBtn}
            onClick={() => onSelect(opt.key)}
          >
            <MIcon name={opt.icon} size={16} />
            {opt.label}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function AppearanceTab() {
  const {
    theme,
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
  } = useTheme();

  // 白底僅限淺色模式、黑底僅限深色模式，不符目前明暗的直接不顯示
  // （theme 為實際套用的明暗，系統模式下是解析後的結果）
  const visibleStyleOptions = STYLE_OPTIONS.filter(
    (opt) =>
      !(opt.key === "white" && theme === "dark") &&
      !(opt.key === "black" && theme === "light")
  );

  return (
    <div className={styles.card}>
      <h2 className={styles.cardTitle}>外觀</h2>

      <div className={styles.form}>
        <div className={styles.field}>
          <span>主色</span>
          <div className={styles.colorRow}>
            <input
              type="color"
              value={primaryColor}
              onChange={(e) => setPrimaryColor(e.target.value)}
              aria-label="選擇主色"
            />
            <HexInput value={primaryColor} onChange={setPrimaryColor} ariaLabel="主色 HEX 色碼" />
          </div>
          <div className={styles.shadeRow}>
            <span className={styles.shadeLight}>淺</span>
            <span className={styles.shadeBase}>主</span>
            <span className={styles.shadeDark}>深</span>
          </div>
          <p className={styles.rowMeta}>淺色與深色色階會依主色的明度自動推算</p>
        </div>

        <OptionGroup label="風格" options={visibleStyleOptions} value={style} onSelect={setStyle} />

        {/* 背景：只顯示目前風格對應的那組花色 */}
        <div className={styles.field}>
          <span>背景</span>

          {/* 漸層背景的基準色可與主色分開設定，清空即回到跟隨主色。
              玻璃質感三種花色都由基準色衍生，白底/黑底只有跟隨主色會用到 */}
          {(style === "glass" || backgroundId === "auto-gradient") && (
            <div className={styles.colorRow}>
              <input
                type="color"
                value={backgroundColor || primaryColor}
                onChange={(e) => setBackgroundColor(e.target.value)}
                aria-label="選擇背景顏色"
              />
              <HexInput
                value={backgroundColor || primaryColor}
                onChange={setBackgroundColor}
                ariaLabel="背景 HEX 色碼"
              />
              {!backgroundColor && (
                <span className={styles.rowMeta}>跟隨主色中，可另選背景色</span>
              )}
            </div>
          )}
          
          <div className={styles.bgGallery}>
            {BACKGROUND_OPTIONS[style].map((opt) => (
              <button
                key={opt.id}
                type="button"
                className={backgroundId === opt.id ? styles.bgThumbActive : styles.bgThumb}
                style={{ background: opt.preview }}
                onClick={() => setBackgroundId(opt.id)}
              >
                <span className={styles.bgThumbLabel}>
                  {opt.id === "auto-gradient" && backgroundColor ? "自訂顏色" : opt.label}
                </span>
              </button>
            ))}
          </div>
        </div>

        <OptionGroup label="明暗模式" options={THEME_OPTIONS} value={mode} onSelect={setMode} />

        <div className={styles.formActions}>
          <button type="button" className={styles.btnSecondary} onClick={resetToDefaults}>
            <MIcon name="refresh" size={16} />
            重設為預設值
          </button>
        </div>
      </div>
    </div>
  );
}
