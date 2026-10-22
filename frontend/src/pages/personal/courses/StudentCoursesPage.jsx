import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import EmptyState from "../../../components/EmptyState/EmptyState";
import LoadingState from "../../../components/LoadingState/LoadingState";
import MIcon from "../../../components/MIcon";
import PageHeader from "../../../components/PageHeader/PageHeader";
import { useQueryView } from "../../../hooks/useQueryLoadState";
import { useCoursePathsQuery, useCourseScheduleQuery } from "../../../queries/courses";
import CourseTicket from "../dashboard/CourseTicket";
import styles from "./StudentCoursesPage.module.scss";

/* 票券要的時間地點欄位只在課表裡；題數等進度沿用課程目錄的值 */
const SCHEDULE_FIELDS = ["state", "session_date", "start_at", "end_at", "location", "teacher"];

const EMPTY = [];

/** 課程目錄併上課表的時間地點；沒排課的課程標成 unscheduled */
function mergeSchedule(paths, schedules) {
  const scheduleByPathId = new Map(schedules.map((row) => [String(row.id), row]));
  return paths.map((path) => {
    const row = scheduleByPathId.get(String(path.id));
    const scheduleFields = row
      ? Object.fromEntries(SCHEDULE_FIELDS.map((key) => [key, row[key]]))
      : { state: "unscheduled" };
    return { ...path, ...scheduleFields };
  });
}

export default function StudentCoursesPage() {
  const { t } = useTranslation("personal");
  const navigate = useNavigate();
  const [guideDemo, setGuideDemo] = useState(false);

  /* 課程清單與課表跟首頁、課程頁共用快取；課表載不到時課程照樣列出（只是沒有時間地點） */
  const pathsQuery = useCoursePathsQuery();
  const scheduleQuery = useCourseScheduleQuery();
  /* 首次載入還沒有資料才算載入中／載入失敗；背景刷新失敗沿用上一份。
     首載失敗後背景重試期間維持失敗畫面，不閃回載入中（見 useQueryView） */
  const pathsView = useQueryView(pathsQuery);
  const scheduleView = useQueryView(scheduleQuery);
  const view = {
    loading: pathsView.loading || scheduleView.loading,
    hasError: Boolean(pathsView.error),
    paths: mergeSchedule(
      Array.isArray(pathsQuery.data) ? pathsQuery.data : EMPTY,
      Array.isArray(scheduleQuery.data) ? scheduleQuery.data : EMPTY,
    ),
  };

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
