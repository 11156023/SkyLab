/**
 * 精靈共用零件：首次安裝初始化精靈（SetupPage）與首次登入引導精靈（OnboardingPage）共用。
 *
 * 刻意獨立成小模組、只依賴 SetupPage.module.scss 與少數共用元件：
 * 若從 SetupPage.jsx 匯出，會把延遲載入的 SetupPage chunk 拉進 OnboardingPage 的 bundle。
 * 步驟列一律用共用的 components/Stepper，這裡不再另做一份。
 */

import { useTranslation } from "react-i18next";
import MIcon from "../../components/MIcon";
import SegmentedControl from "../../components/SegmentedControl/SegmentedControl";
import { currentLanguage, setLanguage } from "../../i18n";
import styles from "./SetupPage.module.scss";

/* 語言用原生名稱顯示，不翻譯 */
export const LANG_OPTIONS = [
  { key: "zh-TW", label: "繁體中文" },
  { key: "en", label: "English" },
  { key: "ja", label: "日本語" },
];

/** 目前介面語言；不在支援清單內時退回預設語言 */
export function useCurrentLanguage() {
  const { i18n } = useTranslation();
  return currentLanguage(i18n.language);
}

export function Notice({ icon = "info", tone = "info", children }) {
  return (
    <div className={`${styles.notice} ${styles[`notice_${tone}`]}`}>
      <MIcon name={icon} size={20} />
      <div>{children}</div>
    </div>
  );
}

/** 語言切換：互斥選項一律用共用 SegmentedControl，點選即切換介面語言（存在瀏覽器）。
 *  按鈕帶 lang，讀屏才會用對的語音唸出原生語言名稱 */
export function LanguagePicker({ ariaLabel, className }) {
  const current = useCurrentLanguage();
  return (
    <SegmentedControl
      className={className}
      ariaLabel={ariaLabel}
      value={current}
      onChange={setLanguage}
      options={LANG_OPTIONS.map((option) => ({ value: option.key, label: option.label, buttonProps: { lang: option.key } }))}
    />
  );
}
