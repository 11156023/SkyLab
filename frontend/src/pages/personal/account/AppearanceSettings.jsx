import { useState } from "react";
import { useTranslation } from "react-i18next";
import MIcon from "../../../components/MIcon";
import {
  useTheme,
  THEME_OPTIONS,
  STYLE_OPTIONS,
  BACKGROUND_OPTIONS,
  THEME_DEFAULTS,
} from "../../../contexts/ThemeContext";
import ColorPicker from "../../../components/ColorPicker/ColorPicker";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import { useToast } from "../../../hooks/useToast";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { downscaleImage } from "../../../utils/image/downscaleImage";
import { ThemePresetList } from "./ThemePresetsCard";
import styles from "./AccountSettingsPage.module.scss";

/** 背景圖 data URL 上限：留在 localStorage 配額（約 5MB）內 */
const BG_IMAGE_MAX_CHARS = 3 * 1024 * 1024;

/**
 * 外觀設定的各個區塊。帳號設定與首次登入引導用同一批元件，兩邊的選法才不會各改各的：
 *   - AppearanceSettings（預設匯出）：帳號設定「外觀」分頁的完整表單（色彩＋介面）
 *   - AppearanceQuickSettings：首次登入引導用的精簡版（配色主題＋介面），細調留到帳號設定
 *   - InterfaceOptions：介面（風格＋明暗模式）兩組選項鈕，上面兩者共用
 *   - AppearanceResetButton：重設為系統預設值（帳號設定放在分頁列右側）
 * 只負責內容，外框（卡片、標題）由使用端決定。
 */

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

/* 選項鈕一組：風格、明暗模式、背景花色都用同一種；children 接在選項後面（花色的「上傳圖片」） */
function OptionGroup({ label, options, value, onSelect, children }) {
  return (
    <div className={styles.field}>
      <span>{label}</span>
      <div className={styles.optionRow} role="group" aria-label={label}>
        {options.map((opt) => (
          <button
            key={opt.key}
            type="button"
            className={value === opt.key ? styles.optionBtnActive : styles.optionBtn}
            aria-pressed={value === opt.key}
            onClick={() => onSelect(opt.key)}
          >
            {opt.swatch ? <i className={styles.patternSwatch} style={{ background: opt.swatch }} aria-hidden="true" /> : <MIcon name={opt.icon} size={16} />}
            {opt.label}
          </button>
        ))}
        {children}
      </div>
    </div>
  );
}

/** 介面：風格＋明暗模式 */
export function InterfaceOptions() {
  const { t } = useTranslation("personal");
  const { theme, mode, setMode, style, setStyle } = useTheme();

  // 白底僅限淺色模式、黑底僅限深色模式，不符目前明暗的直接不顯示
  // （theme 為實際套用的明暗，系統模式下是解析後的結果）
  const styleOptions = STYLE_OPTIONS.filter(
    (opt) =>
      !(opt.key === "white" && theme === "dark") &&
      !(opt.key === "black" && theme === "light")
  ).map((opt) => ({ ...opt, label: t(STYLE_LABEL_KEYS[opt.key] ?? opt.key) }));
  const modeOptions = THEME_OPTIONS.map((opt) => ({
    ...opt,
    label: t(MODE_LABEL_KEYS[opt.key] ?? opt.key),
  }));

  return (
    <>
      <OptionGroup label={t("AppearanceTab.style")} options={styleOptions} value={style} onSelect={setStyle} />
      <OptionGroup label={t("AppearanceTab.colorMode")} options={modeOptions} value={mode} onSelect={setMode} />
    </>
  );
}

/** 首次登入引導的外觀步驟：只放配色主題與介面，挑一組主題就好看；主色、背景等細調留到帳號設定 */
export function AppearanceQuickSettings() {
  const { t } = useTranslation("personal");
  return (
    <div className={styles.appearanceGroups}>
      <section className={styles.appearanceGroup}>
        <h3 className={styles.groupTitle}>{t("AppearanceTab.groupPresets")}</h3>
        <ThemePresetList />
      </section>
      <section className={styles.appearanceGroup}>
        <h3 className={styles.groupTitle}>{t("AppearanceTab.groupInterface")}</h3>
        <InterfaceOptions />
      </section>
    </div>
  );
}

/** 「重設為系統預設值」：帳號設定放在分頁列右側。
 *  放在顯眼處容易誤按，先跳共用確認框；有上傳背景圖時會一併刪掉、無法復原，改用危險樣式並寫明 */
export function AppearanceResetButton() {
  const { t } = useTranslation("personal");
  const { resetToDefaults, backgroundImage } = useTheme();
  const confirm = useConfirm();
  const toast = useToast();

  async function handleReset() {
    const hasImage = Boolean(backgroundImage);
    const ok = await confirm({
      title: t("AppearanceTab.resetConfirmTitle"),
      message: t(hasImage ? "AppearanceTab.resetConfirmMessageWithImage" : "AppearanceTab.resetConfirmMessage"),
      confirmText: t("AppearanceTab.resetConfirmBtn"),
      danger: hasImage,
    });
    if (!ok) return;
    resetToDefaults();
    toast.success(t("AppearanceTab.resetDone"));
  }

  return (
    <button type="button" className={styles.btnSecondary} onClick={handleReset}>
      <MIcon name="refresh" size={16} />
      {t("AppearanceTab.resetToDefaults")}
    </button>
  );
}

export default function AppearanceSettings() {
  const { t } = useTranslation("personal");
  const {
    primaryColor,
    setPrimaryColor,
    backgroundId,
    setBackgroundId,
    backgroundColor,
    setBackgroundColor,
    backgroundImage,
    setBackgroundImage,
  } = useTheme();
  const toast = useToast();
  // 大圖縮圖要一兩秒，期間上傳區塊顯示載入動畫
  const [processing, setProcessing] = useState(false);

  async function handleBackgroundFile(file) {
    // accept 擋不住所有情況（例如選檔視窗切成「所有檔案」），非圖片先擋下，免得縮圖時才冒出看不懂的錯誤
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

  // 主色與背景色都未自訂：「基本漸層」呈現原始三色暈染，小色塊同步顯示
  const untouchedAuto =
    !backgroundColor && primaryColor.toLowerCase() === THEME_DEFAULTS.primaryColor;

  // 背景花色做成跟風格一樣的選項鈕，前面放小色塊預覽；有上傳圖時多一個「自訂圖片」
  const patternOptions = [
    ...BACKGROUND_OPTIONS.map((opt) => ({
      key: opt.id,
      label: t(BACKGROUND_LABEL_KEYS[opt.id] ?? opt.label),
      swatch: opt.id === "auto-gradient" && untouchedAuto ? CLASSIC_PREVIEW : opt.preview,
    })),
    ...(backgroundImage
      ? [{ key: "custom-image", label: t("AppearanceTab.bgCustomImage"), swatch: `url("${backgroundImage}") center / cover no-repeat` }]
      : []),
  ];

  return (
    <div className={`${styles.form} ${styles.appearanceForm}`}>
      {/* 色彩、介面兩組：表單夠寬時左右兩欄，窄時上下堆疊 */}
      <div className={styles.appearanceGroups}>
        {/* 色彩：主色、背景色、背景花色排在一起 */}
        <section className={styles.appearanceGroup}>
          <h3 className={styles.groupTitle}>{t("AppearanceTab.groupColor")}</h3>

          {/* 主色、背景色並排同一列（空間不夠時自動換行） */}
          <div className={styles.colorRow}>
            <div className={styles.field}>
              <span>{t("AppearanceTab.primaryColor")}</span>
              {/* 彩虹圈色點＋色碼：原生色票格加色碼框，不熟前端的人看不出是在選顏色 */}
              <ColorPicker value={primaryColor} onChange={setPrimaryColor} ariaLabel={t("AppearanceTab.selectPrimaryColor")} />
              {/* 色階只是預覽（跟著主色自動推算，不能點）：小色點＋淡字，不做成按鈕的樣子——
                  原本三塊大色塊曾被使用者誤認為可以點 */}
              <p className={styles.shadePreview}>
                <span>{t("AppearanceTab.shadeHint")}</span>
                <span className={styles.shadeSwatch}><i className={styles.shadeLight} aria-hidden="true" />{t("AppearanceTab.shadeLight")}</span>
                <span className={styles.shadeSwatch}><i className={styles.shadeBase} aria-hidden="true" />{t("AppearanceTab.shadeBase")}</span>
                <span className={styles.shadeSwatch}><i className={styles.shadeDark} aria-hidden="true" />{t("AppearanceTab.shadeDark")}</span>
              </p>
            </div>

            {/* 背景色：明講「跟隨主色／自訂」，選自訂才在同一行後面出現選色；不再靠一行說明文字暗示狀態 */}
            <div className={styles.field}>
              <span>{t("AppearanceTab.backgroundColor")}</span>
              <div className={styles.bgColorControls}>
                <SegmentedControl
                  ariaLabel={t("AppearanceTab.backgroundColor")}
                  value={backgroundColor ? "custom" : "follow"}
                  onChange={(next) => setBackgroundColor(next === "custom" ? primaryColor : "")}
                  options={[
                    { value: "follow", label: t("AppearanceTab.bgColorFollow") },
                    { value: "custom", label: t("AppearanceTab.bgColorCustom") },
                  ]}
                />
                {backgroundColor && (
                  <ColorPicker className={styles.pickerNoWrap} value={backgroundColor} onChange={setBackgroundColor} ariaLabel={t("AppearanceTab.selectBackgroundColor")} />
                )}
              </div>
            </div>
          </div>

          <OptionGroup label={t("AppearanceTab.backgroundPattern")} options={patternOptions} value={backgroundId} onSelect={setBackgroundId}>
            {/* 上傳自訂背景圖（存在瀏覽器本地，重設或移除即刪掉） */}
            <label className={`${styles.optionBtn} ${styles.uploadOption}`} aria-busy={processing}>
              <MIcon name={processing ? "autorenew" : "upload"} size={16} spin={processing} />
              {t("AppearanceTab.uploadImage")}
              <input
                type="file"
                accept="image/*"
                className={styles.fileInput}
                disabled={processing}
                onChange={(e) => {
                  const [file] = e.target.files ?? [];
                  e.target.value = "";
                  if (file) handleBackgroundFile(file);
                }}
              />
            </label>
          </OptionGroup>
          {backgroundImage && (
            <div className={styles.formActions}>
              <button type="button" className={styles.btnSecondary} onClick={removeBackgroundImage}>
                <MIcon name="delete" size={16} />
                {t("AppearanceTab.removeBackgroundImage")}
              </button>
            </div>
          )}
        </section>

        {/* 介面：風格、明暗模式 */}
        <section className={styles.appearanceGroup}>
          <h3 className={styles.groupTitle}>{t("AppearanceTab.groupInterface")}</h3>
          <InterfaceOptions />
        </section>
      </div>
    </div>
  );
}
