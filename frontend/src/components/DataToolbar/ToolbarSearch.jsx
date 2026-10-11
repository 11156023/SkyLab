import MIcon from "../MIcon";
import styles from "./DataToolbar.module.scss";

/**
 * 工具列上的搜尋框（36px，跟按鈕、分段切換同高）。按 Esc 清掉搜尋字。
 * placeholder 同時當無障礙名稱，寫「搜尋什麼」，例如「搜尋名稱、VMID、IP 或擁有者」。
 *
 * @param {string}   value
 * @param {Function} onChange    (value: string) => void
 * @param {string}   placeholder
 * @param {string}   className   額外樣式（例如窄螢幕撐滿一行）
 */
export default function ToolbarSearch({ value, onChange, placeholder, className }) {
  return (
    <label className={`${styles.search}${className ? ` ${className}` : ""}`}>
      <MIcon name="search" size={18} />
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => { if (event.key === "Escape" && value) onChange(""); }}
        placeholder={placeholder}
        aria-label={placeholder}
      />
    </label>
  );
}
