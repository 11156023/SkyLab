import styles from "./PageHeader.module.scss";

/**
 * 全站共用的頁面標題列：左側標題區（eyebrow / h1 / 副標），右側由 children 承接
 * 動作按鈕、tabs、篩選器等；leading 放標題左側的返回鍵。
 * 副標只給帶動態資料的頁面（人數、日期、版本），純複述標題的說明已移除。
 */
export default function PageHeader({ eyebrow, title, subtitle, leading, children }) {
  const heading = (
    <div className={styles.pageHeading}>
      {eyebrow && <p className={styles.eyebrow}>{eyebrow}</p>}
      <div className={styles.titleRow}>
        <h1 className={styles.pageTitle}>{title}</h1>
        {/* UserGuide 會把導覽入口鈕 portal 到這個 slot（有導覽的頁面才會出現） */}
        <span data-user-guide-slot="" />
      </div>
      {subtitle && <p className={styles.pageSubtitle}>{subtitle}</p>}
    </div>
  );

  return (
    <div className={styles.pageHeader}>
      {leading ? (
        <div className={styles.headingRow}>
          {leading}
          {heading}
        </div>
      ) : (
        heading
      )}
      {children}
    </div>
  );
}
