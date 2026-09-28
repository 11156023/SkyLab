/**
 * 精靈共用零件：首次安裝初始化精靈（SetupPage）與首次登入引導精靈（OnboardingPage）共用。
 *
 * 刻意獨立成小模組、只依賴 SetupPage.module.scss 與 MIcon：
 * 若從 SetupPage.jsx 匯出，會把延遲載入的 SetupPage chunk 拉進 OnboardingPage 的 bundle。
 */

import { useTranslation } from "react-i18next";
import MIcon from "../../components/MIcon";
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

export function Stepper({ current, steps }) {
  return (
    <ol className={styles.stepper} aria-label="steps">
      {steps.map((label, index) => {
        const state = index < current ? "done" : index === current ? "active" : "todo";
        return (
          <li
            key={label}
            className={`${styles.step} ${styles[`step_${state}`]}`}
            aria-current={state === "active" ? "step" : undefined}
          >
            <span className={styles.stepIndex}>
              {state === "done" ? <MIcon name="check" size={16} /> : index + 1}
            </span>
            <span className={styles.stepLabel}>{label}</span>
          </li>
        );
      })}
    </ol>
  );
}

export function Notice({ icon = "info", tone = "info", children }) {
  return (
    <div className={`${styles.notice} ${styles[`notice_${tone}`]}`}>
      <MIcon name={icon} size={20} />
      <div>{children}</div>
    </div>
  );
}

/** 語言單選清單：點選即切換介面語言（存在瀏覽器） */
export function LanguagePicker({ ariaLabel }) {
  const current = useCurrentLanguage();
  return (
    <div className={styles.langList} role="radiogroup" aria-label={ariaLabel}>
      {LANG_OPTIONS.map((option) => (
        <button
          key={option.key}
          type="button"
          role="radio"
          aria-checked={current === option.key}
          lang={option.key}
          className={`${styles.langBtn} ${current === option.key ? styles.langBtnActive : ""}`}
          onClick={() => setLanguage(option.key)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
