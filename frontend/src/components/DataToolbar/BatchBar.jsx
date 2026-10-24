import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import MIcon from "../MIcon";
import SelectCheckbox from "./SelectCheckbox";
import styles from "./DataToolbar.module.scss";

/**
 * 批量列：DataToolbar 有勾選時換上的內容。「已選 N 筆 ✕」在左、動作靠右；
 * 送出時整條換成進度（第幾筆／共幾筆）。按 Esc 清除選取（有對話框開著時不搶它的 Esc）。
 *
 * 動作的慣例：
 * - 只對適用的那幾筆送出（例如停用只送啟用中的），全都不適用就停用按鈕，title 說明會略過幾筆
 * - 做完靜默刷新；失敗的保留勾選（useRowSelection 的 replace），可以直接再試
 * - 回不去的動作（刪除、移除、封存）用 danger、放在 BatchDivider 後面的最右邊，先確認並列出名稱
 *
 * @param {string}   countLabel 「已選 3 台」這類文字（單位各頁自己決定）
 * @param {Function} onClear    清除選取
 * @param {{ checked, indeterminate, onToggle }} selectAll 清單沒有表頭時才給：數量前面放全選框
 * @param {{ text: string, done: number, total: number }} progress 送出中；給了就整條換成進度
 * @param {boolean}  escapeToClear 頁面自己的選單開著時傳 false，Esc 留給選單
 * @param {ReactNode} children 動作（BatchAction、BatchDivider）
 */
export default function BatchBar({ countLabel, onClear, selectAll, progress, escapeToClear = true, children }) {
  const { t } = useTranslation("common");

  useEffect(() => {
    if (progress || !escapeToClear) return undefined;
    const onKey = (event) => {
      /* 選單等元件已經用這個 Esc 關掉自己（defaultPrevented）就不清除選取 */
      if (event.key !== "Escape" || event.defaultPrevented || document.querySelector('[aria-modal="true"]')) return;
      onClear();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [progress, escapeToClear, onClear]);

  const pct = progress ? Math.round((progress.done / Math.max(progress.total, 1)) * 100) : 0;

  return (
    <div className={styles.batchBar} role="region" aria-label={countLabel}>
      {progress ? (
        <div className={styles.batchProgress} role="status">
          <MIcon name="autorenew" size={16} spin />
          <span>{progress.text}</span>
          <span className={styles.batchProgressTrack} aria-hidden="true">
            <span style={{ width: `${pct}%` }} />
          </span>
        </div>
      ) : (
        <>
          {selectAll && (
            <SelectCheckbox
              checked={selectAll.checked}
              indeterminate={selectAll.indeterminate}
              onToggle={selectAll.onToggle}
              ariaLabel={t("DataToolbar.selectAll")}
            />
          )}
          <span className={styles.batchCount}>{countLabel}</span>
          <button
            type="button"
            className={styles.batchClear}
            onClick={onClear}
            title={t("DataToolbar.clearSelection")}
            aria-label={t("DataToolbar.clearSelection")}
          >
            <MIcon name="close" size={16} />
          </button>
          <span className={styles.batchSpacer} />
          {children}
        </>
      )}
    </div>
  );
}

/**
 * 批量列上的動作鈕。danger＝回不去的動作（紅框）。
 * React 19：ref 直接當 prop 傳（給「更多」這類要錨定選單的按鈕）。
 */
export function BatchAction({ icon, label, danger = false, ref, ...props }) {
  return (
    <button ref={ref} type="button" className={danger ? styles.actionDanger : styles.action} {...props}>
      {icon && <MIcon name={icon} size={16} />}
      {label}
    </button>
  );
}

/** 一般動作與回不去的動作之間的分隔線 */
export function BatchDivider() {
  return <span className={styles.batchDivider} aria-hidden="true" />;
}
