import { useTranslation } from "react-i18next";
import MIcon from "../MIcon";
import { PASSWORD_MIN_LENGTH, PASSWORD_RULES } from "../../utils/passwordPolicy";
import styles from "./PasswordRules.module.scss";

/**
 * 帳號密碼複雜度的即時檢查清單，放在「新密碼」欄位正下方。
 *
 * 規則來源是 utils/passwordPolicy（與後端同一套）；這裡只負責呈現。
 * `invalid`：送出時仍有未滿足的規則，把未滿足的項目標成紅色。
 *
 * 各頁的欄位多半包在 <label> 裡，label 內只能放行內內容，所以用 span + role
 * 組清單而不是 ul/li。
 */
export default function PasswordRules({ password, invalid = false, className }) {
  const { t } = useTranslation("common");
  const value = password ?? "";

  return (
    <span
      role="list"
      aria-label={t("PasswordRules.label")}
      className={`${styles.rules} ${className ?? ""}`.trim()}
    >
      {PASSWORD_RULES.map((rule) => {
        const met = rule.test(value);
        const text = t(`PasswordRules.${rule.key}`, { min: PASSWORD_MIN_LENGTH });
        const state = met ? styles.met : invalid ? styles.unmetInvalid : "";
        return (
          <span
            key={rule.key}
            role="listitem"
            className={`${styles.rule} ${state}`.trim()}
            aria-label={t(met ? "PasswordRules.met" : "PasswordRules.unmet", { rule: text })}
          >
            <MIcon name={met ? "check_circle" : "radio_button_unchecked"} size={14} />
            <span>{text}</span>
          </span>
        );
      })}
    </span>
  );
}
