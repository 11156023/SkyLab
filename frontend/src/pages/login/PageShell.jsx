import styles from "./LoginPage.module.scss";

/**
 * 登入類頁面的外框：三色暈染上的光暈層 + 毛玻璃卡片。
 * 登入頁各 view 與強制綁定兩步驟驗證頁共用；cardClassName 追加在卡片上（例如加寬）。
 */
export default function PageShell({ children, cardClassName = "" }) {
  return (
    <div className={styles.page}>
      <div className={styles.glow} aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
      <div className={cardClassName ? `${styles.card} ${cardClassName}` : styles.card}>
        {children}
      </div>
    </div>
  );
}
