import styles from "./LoginPage.module.scss";

/**
 * 登入類頁面的外框：毛玻璃卡片。背景不另疊光暈色球，直接露出全站主題背景（body::before），
 * 跟服務檢查頁、系統內頁同一套，登入一路進到系統背景都不換。
 * 登入頁各 view 與強制綁定兩步驟驗證頁共用；cardClassName 追加在卡片上（例如加寬）。
 */
export default function PageShell({ children, cardClassName = "" }) {
  return (
    <div className={styles.page}>
      <div className={cardClassName ? `${styles.card} ${cardClassName}` : styles.card}>
        {children}
      </div>
    </div>
  );
}
