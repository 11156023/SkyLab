import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import EmptyState from "../../../components/EmptyState/EmptyState";
import LoadingState from "../../../components/LoadingState/LoadingState";
import MIcon from "../../../components/MIcon";
import PageHeader from "../../../components/PageHeader/PageHeader";
import { CoursesService } from "../../../services/courses";
import CourseTicket from "../dashboard/CourseTicket";
import styles from "./StudentCoursesPage.module.scss";

/* 票券要的時間地點欄位只在課表裡；題數等進度沿用課程目錄的值 */
const SCHEDULE_FIELDS = ["state", "session_date", "start_at", "end_at", "location", "teacher"];

export default function StudentCoursesPage() {
  const { t } = useTranslation("personal");
  const navigate = useNavigate();
  const [view, setView] = useState({ loading: true, hasError: false, paths: [] });
  const [guideDemo, setGuideDemo] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function loadCourses() {
      const [pathsResult, scheduleResult] = await Promise.allSettled([
        CoursesService.listPaths(),
        CoursesService.listSchedule(),
      ]);
      if (cancelled) return;

      const paths = pathsResult.status === "fulfilled" && Array.isArray(pathsResult.value)
        ? pathsResult.value
        : [];
      const schedules = scheduleResult.status === "fulfilled" && Array.isArray(scheduleResult.value)
        ? scheduleResult.value
        : [];
      const scheduleByPathId = new Map(schedules.map((row) => [String(row.id), row]));

      setView({
        loading: false,
        hasError: pathsResult.status === "rejected",
        paths: paths.map((path) => {
          const row = scheduleByPathId.get(String(path.id));
          const scheduleFields = row
            ? Object.fromEntries(SCHEDULE_FIELDS.map((key) => [key, row[key]]))
            : { state: "unscheduled" };
          return { ...path, ...scheduleFields };
        }),
      });
    }

    loadCourses();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const handleGuideState = (event) => {
      setGuideDemo(Boolean(event.detail?.open && event.detail?.id === "courses"));
    };
    window.addEventListener("skylab:user-guide-state", handleGuideState);
    return () => window.removeEventListener("skylab:user-guide-state", handleGuideState);
  }, []);
  if (view.loading) {
    return (
      <div className={styles.page}>
        <LoadingState fullPage text={t("StudentCoursesPage.loading")} />
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <PageHeader title={t("StudentCoursesPage.title")} />

      {view.hasError && (
        <div className={styles.notice} role="alert">
          <MIcon name="cloud_off" size={20} />
          <span>{t("Error.generic", { ns: "common" })}</span>
        </div>
      )}

      {view.paths.length > 0 || guideDemo ? (
        <section className={styles.courseGrid} aria-label={t("StudentCoursesPage.listAria")}>
          {guideDemo && (
            <CourseTicket demo guide="course-demo-card" openGuide="course-demo-open" path={{
              id: "demo",
              title: t("StudentCoursesPage.guideDemoTitle"),
              completed_questions: 4, total_questions: 8,
            }} onOpen={() => navigate("/courses/demo", { state: { from: "/courses" } })} />
          )}
          {view.paths.map((path) => (
            <CourseTicket key={path.id} path={path} guide="course-card"
              onOpen={() => navigate(`/courses/${path.id}`, { state: { from: "/courses" } })} />
          ))}
        </section>
      ) : (
        <EmptyState
          icon="school"
          title={t("StudentCoursesPage.emptyTitle")}
          description={t("StudentCoursesPage.emptyDescription")}
        />
      )}
    </div>
  );
}
