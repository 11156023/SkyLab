import { useTranslation } from "react-i18next";
import MIcon from "../../../components/MIcon";
import { toPercent } from "../dashboard/student/studentDashboard";
import styles from "./StudentCoursesPage.module.scss";

export default function CourseCard({ path, onOpen, demo = false }) {
  const { t } = useTranslation("personal");
  const progress = toPercent(path.progress_percent);
  const inClass = path.schedule?.state === "now";
  return (
    <button type="button"
      className={`${styles.courseCard} ${demo ? styles.guideDemoCard : ""} ${inClass ? styles.activeCard : ""}`}
      onClick={onOpen} data-guide={demo ? "course-demo-card" : "course-card"}
      data-guide-demo={demo ? "true" : undefined}>
      <span className={styles.cardHeader}>
        <span className={styles.courseIcon}><MIcon name={demo ? "terminal" : "school"} size={24} /></span>
        <strong className={styles.courseTitle}>{path.title}</strong>
        <span className={inClass || demo ? styles.liveStatus : styles.courseStatus}>
          {demo ? t("StudentCoursesPage.guideDemoBadge") : inClass ? t("StudentCoursesPage.inClass") : path.schedule?.label ?? t("StudentCoursesPage.available")}
        </span>
      </span>
      <span className={styles.courseBody}>
        <span className={styles.courseMeta}>
          <span><MIcon name="menu_book" size={16} />{t("StudentCoursesPage.roomCount", { count: path.room_count ?? 0 })}</span>
          {path.schedule?.teacher && <span><MIcon name="person" size={16} />{path.schedule.teacher}</span>}
          {path.schedule?.time && <span><MIcon name="schedule" size={16} />{path.schedule.time}</span>}
          {path.schedule?.place && <span><MIcon name="location_on" size={16} />{path.schedule.place}</span>}
        </span>
      </span>
      <span className={styles.cardFooter}>
        <span className={styles.progressMeta}>
          <span>{t("StudentCoursesPage.progress", { percent: progress })}</span>
          <span>{t("StudentCoursesPage.questions", { completed: path.completed_questions ?? 0, total: path.total_questions ?? 0 })}</span>
        </span>
        <span className={styles.progressTrack} aria-hidden="true"><span style={{ width: `${progress}%` }} /></span>
        <span className={styles.cardAction} data-guide={demo ? "course-demo-open" : undefined}>
          {t("StudentCoursesPage.openCourse")}<MIcon name="arrow_forward" size={18} />
        </span>
      </span>
    </button>
  );
}
