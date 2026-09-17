import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import EmptyState from "../../../components/EmptyState/EmptyState";
import LoadingState from "../../../components/LoadingState/LoadingState";
import MIcon from "../../../components/MIcon";
import PageHeader from "../../../components/PageHeader/PageHeader";
import { CoursesService } from "../../../services/courses";
import CourseCard from "./CourseCard";
import { normalizeSchedule } from "../dashboard/student/studentDashboard";
import styles from "./StudentCoursesPage.module.scss";

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
        ? scheduleResult.value.map(normalizeSchedule)
        : [];
      const scheduleByPathId = new Map(
        schedules.map((schedule) => [String(schedule.id), schedule.schedule]),
      );

      setView({
        loading: false,
        hasError: pathsResult.status === "rejected",
        paths: paths.map((path) => ({
          ...path,
          schedule: scheduleByPathId.get(String(path.id)) ?? null,
        })),
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
          <span>{t("StudentCoursesPage.loadFailed")}</span>
        </div>
      )}

      {view.paths.length > 0 || guideDemo ? (
        <section className={styles.courseGrid} aria-label={t("StudentCoursesPage.listAria")}>
          {guideDemo && (
            <CourseCard demo path={{
              title: t("StudentCoursesPage.guideDemoTitle"),
              description: t("StudentCoursesPage.guideDemoDescription"),
              room_count: 8, progress_percent: 50, completed_questions: 4, total_questions: 8,
            }} onOpen={() => navigate("/courses/demo", { state: { from: "/courses" } })} />
          )}
          {view.paths.map((path) => (
            <CourseCard key={path.id} path={path}
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
