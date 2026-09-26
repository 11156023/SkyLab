import MIcon from "../../../components/MIcon";
import styles from "./HomeCard.module.scss";

/**
 * 首頁卡片外殼（機器、快速練習共用）：玻璃外框上方一條標籤列放狀態，
 * 下面是白底內頁，類型圖示磚半壓在兩層交界的右上角。
 * 構圖取自複刻的 pin-card（外框＋內頁＋浮出圖示磚），外觀回到全站的玻璃卡語言。
 * as="button" 時整張可點（快速練習），內層改用 span 以符合 button 的內容規範。
 */
export default function HomeCard({ as: Tag = "article", band, icon, children, className = "", ...rest }) {
  const clickable = Tag === "button";
  const Inner = clickable ? "span" : "div";
  return (
    <Tag
      className={`${styles.card} ${clickable ? styles.clickable : ""} ${className}`}
      {...(clickable ? { type: "button" } : {})}
      {...rest}
    >
      <Inner className={styles.band}>{band}</Inner>
      <Inner className={styles.sheet}>
        {icon && (
          <span className={styles.icon} aria-hidden="true">
            <MIcon name={icon} size={20} />
          </span>
        )}
        {children}
      </Inner>
    </Tag>
  );
}
