import MIcon from "../MIcon";
import styles from "./SegmentedControl.module.scss";

/**
 * 全站共用的分段切換器（segmented control）。
 * 用於互斥選項的即時切換，例如日期區間、狀態篩選。
 * 超過 4 個選項時，請在 className 加上 max-width:100% + overflow-x:auto 讓窄螢幕可橫向捲動。
 *
 * @param {Array}    options   [{ value, label, icon?, badge? }]，icon 為可選的 MIcon 名稱，badge 為可選的數量徽章
 * @param {string}   value     目前選中的 value
 * @param {Function} onChange  (value) => void
 * @param {string}   ariaLabel 群組的無障礙名稱
 * @param {string}   className 額外樣式
 */
export default function SegmentedControl({
  options,
  value,
  onChange,
  ariaLabel,
  className,
}) {
  return (
    <div
      className={`${styles.segment}${className ? ` ${className}` : ""}`}
      role="group"
      aria-label={ariaLabel}
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          className={`${styles.segmentBtn} ${value === option.value ? styles.segmentActive : ""}`}
          onClick={() => onChange(option.value)}
          aria-pressed={value === option.value}
        >
          {option.icon ? <MIcon name={option.icon} size={14} /> : null}
          {option.label}
          {option.badge != null ? <span className={styles.segmentBadge}>{option.badge}</span> : null}
        </button>
      ))}
    </div>
  );
}
