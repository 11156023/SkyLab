import styles from "./Switch.module.scss";

/**
 * 全站共用的開關（switch）：點下去「立刻生效」的啟用／停用，例如防火牆規則、PVE 節點。
 * 要按「儲存」才生效的表單欄位仍用勾選框，兩者別混用——看到開關就知道一切換就送出。
 *
 * 表格、清單列裡只放開關本體（欄名或列內容已交代控制什麼），用 ariaLabel＋title 說明「啟用」；
 * 需要可見文字時才給 label（整顆是同一個 button，點文字也會切換），寫「控制什麼」、不隨狀態改字。
 * 目前狀態由軌道位置與顏色表達；列表上停用的那一列，動作徽章換成紅色「停用」、操作欄以外淡化。
 *
 * @param {boolean}  checked   目前是否開啟
 * @param {Function} onChange  (next: boolean) => void
 * @param {string}   label     軌道右側的可見文字（選填）
 * @param {string}   ariaLabel 沒有可見文字、或需要更完整說明時的無障礙名稱
 * @param {boolean}  disabled
 * @param {string}   title     滑鼠移上去的補充說明（選填）
 * @param {string}   className 額外樣式
 */
export default function Switch({
  checked,
  onChange,
  label,
  ariaLabel,
  disabled = false,
  title,
  className,
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      title={title}
      disabled={disabled}
      className={`${styles.switch}${className ? ` ${className}` : ""}`}
      onClick={() => onChange(!checked)}
    >
      <span className={styles.track} aria-hidden="true" />
      {label ? <span className={styles.label}>{label}</span> : null}
    </button>
  );
}
