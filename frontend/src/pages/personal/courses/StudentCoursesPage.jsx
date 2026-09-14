import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import EmptyState from "../../../components/EmptyState/EmptyState";
import LoadingState from "../../../components/LoadingState/LoadingState";
import MIcon from "../../../components/MIcon";
import PageHeader from "../../../components/PageHeader/PageHeader";
import { CoursesService } from "../../../services/courses";
import { normalizeSchedule, toPercent } from "../dashboard/student/studentDashboard";
import styles from "./StudentCoursesPage.module.scss";

export default function StudentCoursesPage() {
  const { t } = useTranslation("personal");
  const navigate = useNavigate();
  const [view, setView] = useState({ loading: true, hasError: false, paths: [] });

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

  if (view.loading) {
    return (
      <div className={styles.page}>
        <LoadingState fullPage text={t("StudentCoursesPage.loading")} />
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <PageHeader
        eyebrow={t("StudentCoursesPage.eyebrow")}
        title={t("StudentCoursesPage.title")}
        subtitle={t("StudentCoursesPage.subtitle")}
      />

      {view.hasError && (
        <div className={styles.notice} role="alert">
          <MIcon name="cloud_off" size={20} />
          <span>{t("StudentCoursesPage.loadFailed")}</span>
        </div>
      )}

      {view.paths.length > 0 ? (
        <section className={styles.courseGrid} aria-label={t("StudentCoursesPage.listAria")}>
          {view.paths.map((path) => {
            const progress = toPercent(path.progress_percent);
            const inClass = path.schedule?.state === "now";
            return (
              <button
                type="button"
                className={styles.courseCard}
                key={path.id}
                onClick={() => navigate(`/courses/${path.id}`, { state: { from: "/courses" } })}
              >
                <span className={styles.courseIcon}>
                  <MIcon name="school" size={25} />
                </span>
                <span className={styles.courseBody}>
                  <span className={styles.courseTopline}>
                    <span className={inClass ? styles.liveStatus : styles.courseStatus}>
                      {inClass ? t("StudentCoursesPage.inClass") : path.schedule?.label ?? t("StudentCoursesPage.available")}
                    </span>
                    <span>{t("StudentCoursesPage.roomCount", { count: path.room_count })}</span>
                  </span>
                  <strong className={styles.courseTitle}>{path.title}</strong>
                  <span className={styles.courseDescription}>
                    {path.description || t("StudentCoursesPage.noDescription")}
                  </span>
                  <span className={styles.progressMeta}>
                    <span>{t("StudentCoursesPage.progress", { percent: Math.round(progress) })}</span>
                    <span>{t("StudentCoursesPage.questions", { completed: path.completed_questions, total: path.total_questions })}</span>
                  </span>
                  <span className={styles.progressTrack} aria-hidden="true">
                    <span style={{ width: `${progress}%` }} />
                  </span>
                </span>
                <MIcon name="arrow_forward" size={20} />
              </button>
            );
          })}
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
