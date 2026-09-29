import AppearanceSettings from "./AppearanceSettings";
import ThemePresetsCard from "./ThemePresetsCard";
import styles from "./AccountSettingsPage.module.scss";

/* ── 外觀 ───────────────────────────────────────────── */
/* 表單本體在 AppearanceSettings（區塊與首次登入引導共用），這裡只放卡片外框；
   不放卡片標題——上方分頁已寫「外觀」，卡片內直接從「色彩／介面」分組開始。
   「重設為系統預設值」放在頁面的分頁列右側（AccountSettingsPage），表單底部不重複放。
   配色主題另開一張卡片放在下方，四個系列攤開一次看完 */
export default function AppearanceTab() {
  return (
    <>
      <div className={styles.card}>
        <AppearanceSettings />
      </div>
      <ThemePresetsCard />
    </>
  );
}
