import MIcon from "../MIcon";
import styles from "./EmptyState.module.scss";

/**
 * 全站共用的「無資料 / 空狀態」呈現。
 * 預設為置中圖示搭配簡短說明文字。
 *
 * @param {string}  icon        MIcon 圖示名稱（如 "school"、"inbox"）
 * @param {number}  iconSize    圖示大小，預設 44
 * @param {string}  title       主要訊息（單行）
 * @param {string}  description 次要說明（可選，多一行）
 * @param {node}    action      行動按鈕等（可選）
 * @param {string}  className   額外樣式
 */
export default function EmptyState({
  icon = "inbox",
  iconSize = 44,
  title,
  description,
  action,
  className,
}) {
  return (
    <div className={`${styles.empty}${className ? ` ${className}` : ""}`} data-empty-state="true">
      {icon && <MIcon name={icon} size={iconSize} className={styles.icon} />}
      {title && <p className={styles.title}>{title}</p>}
      {description && <p className={styles.description}>{description}</p>}
      {action && <div className={styles.action}>{action}</div>}
    </div>
  );
}
