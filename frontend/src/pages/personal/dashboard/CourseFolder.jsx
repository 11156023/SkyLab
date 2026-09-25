import { useTranslation } from "react-i18next";
import { toPercent } from "./student/studentDashboard";
import styles from "./CourseFolder.module.scss";

/**
 * 首頁的課堂資料夾：左上標籤頁寫課堂狀態，後板裡露出一張白紙寫學習進度，
 * 前蓋寫課名與上課資訊。構圖取自複刻的 folder-card（後板＋探出的紙＋前蓋），
 * 外觀改成扁平的淡主色，與全站玻璃卡同一套；滑入或鍵盤聚焦時紙張微微上探。
 */
export default function CourseFolder({ path, onOpen }) {
  const { t } = useTranslation("personal");
  const progress = toPercent(path.progress_percent);
  const inClass = path.schedule?.state === "now";
  const statusLabel = inClass
    ? t("StudentCoursesPage.inClass")
    : path.schedule?.label ?? t("StudentCoursesPage.available");
  const details = [
    t("StudentCoursesPage.roomCount", { count: path.room_count ?? 0 }),
    path.schedule?.time,
    path.schedule?.teacher,
  ].filter(Boolean);

  return (
    <button type="button" className={styles.folder} onClick={onOpen}>
      <span className={`${styles.tab} ${inClass ? styles.tabLive : ""}`}>{statusLabel}</span>
      <span className={styles.paper}>
        <span className={styles.paperHead}>
          <span>{t("StudentCoursesPage.progress", { percent: progress })}</span>
          <span>{t("StudentCoursesPage.questions", { completed: path.completed_questions ?? 0, total: path.total_questions ?? 0 })}</span>
        </span>
        <span className={styles.track} aria-hidden="true"><span style={{ width: `${progress}%` }} /></span>
      </span>
      <span className={styles.front}>
        <strong className={styles.title}>{path.title}</strong>
        <span className={styles.meta}>
          {details.map((item) => <span key={item}>{item}</span>)}
        </span>
      </span>
    </button>
  );
}
