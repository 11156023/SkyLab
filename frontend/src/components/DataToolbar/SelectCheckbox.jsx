import { useEffect, useRef } from "react";
import styles from "./DataToolbar.module.scss";

/**
 * 批量操作的勾選框：每列一個，全選也用它（indeterminate＝只勾了一部分）。
 * 外面包一層 label 放大點擊範圍；在 click 讀 Shift（change 事件不一定帶得到按鍵），交給 onToggle 連選一段。
 * 列本身可點（進詳情）時，列的 onClick 要略過 label（event.target.closest("label")）。
 *
 * @param {boolean}  checked
 * @param {boolean}  indeterminate 全選框：只勾了一部分
 * @param {boolean}  disabled
 * @param {Function} onToggle  (shiftKey: boolean) => void
 * @param {string}   ariaLabel 無障礙名稱，例如「選取 web-01」
 * @param {boolean}  cell      放在表格儲存格裡：整格都是點擊範圍
 * @param {string}   className 額外樣式
 */
export default function SelectCheckbox({
  checked,
  indeterminate = false,
  disabled = false,
  onToggle,
  ariaLabel,
  cell = false,
  className,
}) {
  const inputRef = useRef(null);

  useEffect(() => {
    if (inputRef.current) inputRef.current.indeterminate = indeterminate;
  }, [indeterminate]);

  return (
    <label
      className={`${styles.checkHit}${cell ? ` ${styles.checkHitCell}` : ""}${className ? ` ${className}` : ""}`}
      /* Shift＋點連選時不要順便反白一整段文字 */
      onMouseDown={(event) => { if (event.shiftKey) event.preventDefault(); }}
    >
      <input
        ref={inputRef}
        type="checkbox"
        className={styles.checkbox}
        checked={checked}
        disabled={disabled}
        onClick={(event) => onToggle?.(event.shiftKey)}
        onChange={() => {}}
        aria-label={ariaLabel}
      />
    </label>
  );
}
