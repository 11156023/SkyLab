import styles from "./DevStylePage.module.scss";

/**
 * 樣式對照頁的區塊外框：標題、對應的程式碼位置（元件路徑、mixin 名稱）與示範內容。
 * refs 以 `path:` / `mixin:` / `hook:` 這類前綴區分，頁面上一律顯示成程式碼晶片。
 */
export function Section({ id, title, refs = [], intro, children }) {
  return (
    <section id={id} className={styles.section} aria-labelledby={`${id}-title`}>
      <header className={styles.sectionHead}>
        <h2 id={`${id}-title`} className={styles.sectionTitle}>{title}</h2>
        {refs.length > 0 && (
          <ul className={styles.refList} aria-label="程式碼位置">
            {refs.map((ref) => (
              <li key={ref}><code>{ref}</code></li>
            ))}
          </ul>
        )}
        {intro && <p className={styles.sectionIntro}>{intro}</p>}
      </header>
      <div className={styles.sectionBody}>{children}</div>
    </section>
  );
}

/** 一格示範：小標＋（選填）來源晶片＋說明，下方放真元件 */
export function Demo({ label, refs = [], note, wide = false, plain = false, children }) {
  const classes = [styles.demo, wide && styles.demoWide, plain && styles.demoPlain].filter(Boolean).join(" ");
  return (
    <div className={classes}>
      <div className={styles.demoHead}>
        <span className={styles.demoLabel}>{label}</span>
        {refs.map((ref) => (
          <code key={ref} className={styles.refChip}>{ref}</code>
        ))}
      </div>
      <div className={styles.demoBody}>{children}</div>
      {note && <p className={styles.demoNote}>{note}</p>}
    </div>
  );
}

/** 區塊裡的補充規則清單（文字版規範，對應 style guide 的條目） */
export function RuleList({ items }) {
  return (
    <ul className={styles.ruleList}>
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  );
}
