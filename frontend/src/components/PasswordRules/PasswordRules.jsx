import { useTranslation } from "react-i18next";
import MIcon from "../MIcon";
import { PASSWORD_MIN_LENGTH, PASSWORD_RULES } from "../../utils/passwordPolicy";
import styles from "./PasswordRules.module.scss";

/**
 * 帳號密碼複雜度的即時檢查清單，放在「新密碼」欄位正下方。
 *
 * 規則來源預設是 utils/passwordPolicy（與後端同一套）；這裡只負責呈現。
 * `rules`：換成別套規則（例如 utils/windowsPassword 的 Windows 機器密碼），
 * 每條規則的 `params` 會帶進語系字串。規則若有 `describe(password)`，顯示文字
 * 改由它依目前輸入決定：回 `null` 不顯示該條，`options` 是語系 key 清單，
 * 翻譯後以「或」串起來帶進 `{{options}}`。
 * `invalid`：送出時仍有未滿足的規則，把未滿足的項目標成紅色。
 *
 * 各頁的欄位多半包在 <label> 裡，label 內只能放行內內容，所以用 span + role
 * 組清單而不是 ul/li。
 */
export default function PasswordRules({
  password,
  invalid = false,
  className,
  rules = PASSWORD_RULES,
}) {
  const { t, i18n } = useTranslation("common");
  const value = password ?? "";
  const formatOptions = (keys) =>
    new Intl.ListFormat(i18n.language, { type: "disjunction" }).format(
      keys.map((key) => t(`PasswordRules.${key}`)),
    );

  return (
    <span
      role="list"
      aria-label={t("PasswordRules.label")}
      className={`${styles.rules} ${className ?? ""}`.trim()}
    >
      {rules.map((rule) => {
        const shown = rule.describe ? rule.describe(value) : rule;
        if (!shown) return null;
        const met = rule.test(value);
        const text = t(`PasswordRules.${shown.key}`, {
          min: PASSWORD_MIN_LENGTH,
          ...shown.params,
          ...(shown.options && { options: formatOptions(shown.options) }),
        });
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
