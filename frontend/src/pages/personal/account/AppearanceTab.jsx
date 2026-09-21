import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import MIcon from "../../../components/MIcon";
import {
  useTheme,
  THEME_OPTIONS,
  STYLE_OPTIONS,
  BACKGROUND_OPTIONS,
  THEME_DEFAULTS,
} from "../../../contexts/ThemeContext";
import FileDropzone from "../../../components/FileDropzone/FileDropzone";
import { useToast } from "../../../hooks/useToast";
import { downscaleImage } from "../../../utils/image/downscaleImage";
import { normalizeHex } from "../../../utils/theme/derivePrimaryShades";
import styles from "./AccountSettingsPage.module.scss";

/** 背景圖 data URL 上限：留在 localStorage 配額（約 5MB）內 */
const BG_IMAGE_MAX_CHARS = 3 * 1024 * 1024;

/* ── 外觀 ───────────────────────────────────────────── */

/** 未自訂時玻璃質感「跟隨主色」實際呈現的是原始三色暈染，縮圖同步顯示 */
const CLASSIC_PREVIEW =
  "linear-gradient(135deg, var(--color-bg-gradient-blue), var(--color-bg-gradient-yellow) 55%, var(--color-bg-gradient-green))";

/** THEME_OPTIONS / STYLE_OPTIONS / BACKGROUND_OPTIONS 定義在 ThemeContext（跨頁共用，非本 namespace 範圍），
 *  這裡依 key/id 對照翻譯 key，在渲染時覆蓋其原始中文 label */
const MODE_LABEL_KEYS = {
  light: "AppearanceTab.modeLight",
  dark: "AppearanceTab.modeDark",
  system: "AppearanceTab.modeSystem",
};
const STYLE_LABEL_KEYS = {
  glass: "AppearanceTab.styleGlass",
  liquid: "AppearanceTab.styleLiquid",
  white: "AppearanceTab.styleWhite",
  black: "AppearanceTab.styleBlack",
};
const BACKGROUND_LABEL_KEYS = {
  "auto-gradient": "AppearanceTab.bgAutoGradient",
  "preset-2": "AppearanceTab.bgPreset2",
  "preset-3": "AppearanceTab.bgPreset3",
};

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
  const { t } = useTranslation("personal");
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
    backgroundImage,
    setBackgroundImage,
    resetToDefaults,
  } = useTheme();
  const toast = useToast();
  // 大圖縮圖要一兩秒，期間上傳區塊顯示載入動畫
  const [processing, setProcessing] = useState(false);

  async function handleBackgroundFile(file) {
    // 拖放不受 accept 限制，非圖片先擋下，免得縮圖時才冒出看不懂的錯誤
    if (!file.type.startsWith("image/")) {
      toast.error(t("common:FileDropzone.notAnImage"));
      return;
    }
    setProcessing(true);
    try {
      const { dataUrl } = await downscaleImage(file, { maxSize: 1920, quality: 0.82 });
      if (dataUrl.length > BG_IMAGE_MAX_CHARS) {
        toast.error(t("AppearanceTab.imageTooLarge"));
        return;
      }
      setBackgroundImage(dataUrl);
      setBackgroundId("custom-image");
      toast.success(t("AppearanceTab.backgroundApplied"));
    } catch (err) {
      toast.error(err?.message ?? t("AppearanceTab.backgroundReadFailed"));
    } finally {
      setProcessing(false);
    }
  }

  function removeBackgroundImage() {
    setBackgroundImage("");
    // 花色守衛會自動退回預設，這裡直接切掉避免一瞬間的空背景
    if (backgroundId === "custom-image") setBackgroundId(THEME_DEFAULTS.backgroundId);
  }

  // 有上傳圖時，背景 gallery 多一個「自訂圖片」選項
  const backgroundOptions = backgroundImage
    ? [
        ...BACKGROUND_OPTIONS,
        {
          id: "custom-image",
          label: t("AppearanceTab.bgCustomImage"),
          preview: `url("${backgroundImage}") center / cover no-repeat`,
        },
      ]
    : BACKGROUND_OPTIONS;

  // 白底僅限淺色模式、黑底僅限深色模式，不符目前明暗的直接不顯示
  // （theme 為實際套用的明暗，系統模式下是解析後的結果）
  const visibleStyleOptions = STYLE_OPTIONS.filter(
    (opt) =>
      !(opt.key === "white" && theme === "dark") &&
      !(opt.key === "black" && theme === "light")
  );

  // 主色與背景色都未自訂：「跟隨主色」呈現原始三色暈染
  const untouchedAuto =
    !backgroundColor && primaryColor.toLowerCase() === THEME_DEFAULTS.primaryColor;

  function thumbPreview(opt) {
    if (opt.id === "auto-gradient" && untouchedAuto) return CLASSIC_PREVIEW;
    return opt.preview;
  }

  const translatedStyleOptions = visibleStyleOptions.map((opt) => ({
    ...opt,
    label: t(STYLE_LABEL_KEYS[opt.key] ?? opt.key),
  }));
  const translatedThemeOptions = THEME_OPTIONS.map((opt) => ({
    ...opt,
    label: t(MODE_LABEL_KEYS[opt.key] ?? opt.key),
  }));

  return (
    <div className={styles.card}>
      <h2 className={styles.cardTitle}>{t("AppearanceTab.title")}</h2>

      <div className={styles.form}>
        <div className={styles.field}>
          <span>{t("AppearanceTab.primaryColor")}</span>
          <div className={styles.colorRow}>
            <input
              type="color"
              value={primaryColor}
              onChange={(e) => setPrimaryColor(e.target.value)}
              aria-label={t("AppearanceTab.selectPrimaryColor")}
            />
            <HexInput value={primaryColor} onChange={setPrimaryColor} ariaLabel={t("AppearanceTab.primaryColorHex")} />
          </div>
          <div className={styles.shadeRow}>
            <span className={styles.shadeLight}>{t("AppearanceTab.shadeLight")}</span>
            <span className={styles.shadeBase}>{t("AppearanceTab.shadeBase")}</span>
            <span className={styles.shadeDark}>{t("AppearanceTab.shadeDark")}</span>
          </div>
          <p className={styles.rowMeta}>{t("AppearanceTab.shadeHint")}</p>
        </div>

        <OptionGroup label={t("AppearanceTab.style")} options={translatedStyleOptions} value={style} onSelect={setStyle} />

        {/* 背景：與風格無關的同一組花色 */}
        <div className={styles.field}>
          <span>{t("AppearanceTab.background")}</span>

          {/* 漸層背景的基準色可與主色分開設定；
              三種風格的所有花色都由基準色衍生，picker 永遠顯示 */}
          <div className={styles.colorRow}>
            <input
              type="color"
              value={backgroundColor || primaryColor}
              onChange={(e) => setBackgroundColor(e.target.value)}
              aria-label={t("AppearanceTab.selectBackgroundColor")}
            />
            <HexInput
              value={backgroundColor || primaryColor}
              onChange={setBackgroundColor}
              ariaLabel={t("AppearanceTab.backgroundColorHex")}
            />
            {!backgroundColor && (
              <span className={styles.rowMeta}>{t("AppearanceTab.followingPrimaryColor")}</span>
            )}
          </div>

          <div className={styles.bgGallery}>
            {backgroundOptions.map((opt) => (
              <button
                key={opt.id}
                type="button"
                className={backgroundId === opt.id ? styles.bgThumbActive : styles.bgThumb}
                style={{ background: thumbPreview(opt) }}
                onClick={() => setBackgroundId(opt.id)}
              >
                <span className={styles.bgThumbLabel}>
                  {opt.id === "auto-gradient" && backgroundColor
                    ? t("AppearanceTab.bgCustomColor")
                    : t(BACKGROUND_LABEL_KEYS[opt.id] ?? opt.label)}
                </span>
              </button>
            ))}
          </div>

          {/* 上傳自訂背景圖（存在瀏覽器本地，重設或移除即刪掉） */}
          <FileDropzone
            compact
            accept="image/*"
            uploading={processing}
            title={t("common:FileDropzone.titleImage")}
            onFiles={([file]) => handleBackgroundFile(file)}
          />
          {backgroundImage && (
            <div className={styles.formActions}>
              <button type="button" className={styles.btnSecondary} onClick={removeBackgroundImage}>
                <MIcon name="delete" size={16} />
                {t("AppearanceTab.removeBackgroundImage")}
              </button>
            </div>
          )}
        </div>

        <OptionGroup label={t("AppearanceTab.colorMode")} options={translatedThemeOptions} value={mode} onSelect={setMode} />

        <div className={styles.formActions}>
          <button type="button" className={styles.btnSecondary} onClick={resetToDefaults}>
            <MIcon name="refresh" size={16} />
            {t("AppearanceTab.resetToDefaults")}
          </button>
        </div>
      </div>
    </div>
  );
}
