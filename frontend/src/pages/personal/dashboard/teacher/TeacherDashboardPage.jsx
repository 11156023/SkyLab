import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import MIcon from "../../../../components/MIcon";
import { useAuth } from "../../../../contexts/AuthContext";
import { CourseAdminService } from "../../../../services/courses";
import { TeachingClassesService } from "../../../../services/teachingClasses";
import styles from "./TeacherDashboardPage.module.scss";
import PageHeader from "../../../../components/PageHeader/PageHeader";
import EmptyState from "../../../../components/EmptyState/EmptyState";
import LoadingState from "../../../../components/LoadingState/LoadingState";
import { formatMonthDay } from "../../../../utils/formatDate";

const CLASS_STATUS_KEYS = {
  planning: "TeacherDashboardPage.statusPlanning",
  pending_review: "TeacherDashboardPage.statusPendingReview",
  provisioning: "TeacherDashboardPage.statusProvisioning",
  partial_failed: "TeacherDashboardPage.statusPartialFailed",
  active: "TeacherDashboardPage.statusActive",
  archived: "TeacherDashboardPage.statusArchived",
};

function dateKey(date) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit",
  }).format(date);
}

function weekdayFromDateKey(value) {
  const [year, month, day] = value.split("-").map(Number);
  return (new Date(Date.UTC(year, month - 1, day)).getUTCDay() + 6) % 7;
}

function addDaysToDateKey(value, days) {
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day + days));
  return date.toISOString().slice(0, 10);
}

function taipeiDateTime(date, time = "00:00") {
  return new Date(`${date}T${time.slice(0, 5)}:00+08:00`);
}

export function nextClassSession(item, now = new Date()) {
  if (!item?.start_date || !item?.end_date) return null;
  const start = taipeiDateTime(item.start_date);
  const end = new Date(`${item.end_date}T23:59:59+08:00`);
  if (now > end) return null;
  const targetWeekday = Number(item.weekday ?? 0);
  const baseDate = now < start ? item.start_date : dateKey(now);
  const daysUntilTarget = (targetWeekday - weekdayFromDateKey(baseDate) + 7) % 7;
  let sessionDate = addDaysToDateKey(baseDate, daysUntilTarget);
  let session = taipeiDateTime(sessionDate, String(item.start_time ?? "00:00"));
  if (session < start || session < now) {
    sessionDate = addDaysToDateKey(sessionDate, 7);
    session = taipeiDateTime(sessionDate, String(item.start_time ?? "00:00"));
  }
  return session <= end ? session : null;
}

export function summarizeCheckpointReports(rows) {
  const summary = rows.reduce((acc, row) => {
    const students = row.report?.students ?? [];
    const completed = students.reduce((sum, student) => sum + Number(student.completed_questions ?? 0), 0);
    const possible = students.reduce((sum, student) => sum + Number(student.total_questions ?? 0), 0);
    acc.completed += completed;
    acc.possible += possible;
    acc.students += students.length;
    return acc;
  }, { completed: 0, possible: 0, students: 0 });
  return {
    ...summary,
    percent: summary.possible ? Math.round(summary.completed / summary.possible * 100) : 0,
  };
}

function normalizeClass(item) {
  return {
    ...item,
    id: String(item.id),
    students: item.member_count ?? item.students?.length ?? 0,
    nodes: item.machine_nodes ?? [],
    readyMachines: item.ready_machines ?? 0,
    totalMachines: item.total_machines ?? 0,
  };
}

function CheckpointRow({ item, onOpen }) {
  const { t } = useTranslation("personal");
  const students = item.report?.students ?? [];
  const completed = students.reduce((sum, student) => sum + Number(student.completed_questions ?? 0), 0);
  const possible = students.reduce((sum, student) => sum + Number(student.total_questions ?? 0), 0);
  const percent = possible ? Math.round(completed / possible * 100) : 0;
  const fullyCompleted = students.filter((student) => Number(student.progress_percent) >= 100).length;
  return <button type="button" className={styles.checkpointRow} onClick={onOpen}>
    <span className={styles.courseIcon}><MIcon name="checklist" size={19} /></span>
    <span className={styles.checkpointMain}>
      <span><strong>{item.path.title}</strong><small>{students.length ? t("CheckpointRow.completedCount", { fullyCompleted, total: students.length }) : t("CheckpointRow.noRecords")}</small></span>
      <span className={styles.progressTrack}><i style={{ width: `${percent}%` }} /></span>
    </span>
    <span className={styles.checkpointMetric}><strong>{percent}%</strong><small>{completed}/{possible || item.report?.total_questions || 0}</small></span>
    <MIcon name="chevron_right" size={19} />
  </button>;
}

export default function TeacherDashboardPage() {
  const { t } = useTranslation("personal");
  const navigate = useNavigate();
  const { user } = useAuth();
  const [classes, setClasses] = useState([]);
  const [reports, setReports] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    async function load() {
      setLoading(true);
      setError("");
      try {
        const [classRows, pathRows] = await Promise.all([
          TeachingClassesService.list(),
          CourseAdminService.listPaths(),
        ]);
        if (!active) return;
        setClasses((classRows?.data ?? classRows ?? []).map(normalizeClass));
        const ownedPaths = (pathRows ?? []).filter((path) => !path.created_by || String(path.created_by) === String(user?.id));
        const settled = await Promise.allSettled(
          ownedPaths.slice(0, 6).map(async (path) => ({ path, report: await CourseAdminService.getPathProgress(path.id) })),
        );
        if (active) setReports(settled.filter((result) => result.status === "fulfilled").map((result) => result.value));
      } catch (reason) {
        if (active) setError(reason?.message ?? t("TeacherDashboardPage.loadError"));
      } finally {
        if (active) setLoading(false);
      }
    }
    load();
    return () => { active = false; };
  }, [user?.id]);

  const checkpointSummary = useMemo(() => summarizeCheckpointReports(reports), [reports]);
  const upcoming = useMemo(() => classes
    .map((item) => ({ item, session: nextClassSession(item) }))
    .filter((row) => row.session)
    .sort((a, b) => a.session - b.session)
    .slice(0, 4), [classes]);
  const incompleteStudents = useMemo(() => reports.flatMap(({ path, report }) => (report?.students ?? [])
    .filter((student) => Number(student.progress_percent) < 100)
    .map((student) => ({ ...student, pathTitle: path.title, pathId: path.id })))
    .sort((a, b) => Number(a.progress_percent) - Number(b.progress_percent))
    .slice(0, 5), [reports]);
  const firstName = user?.full_name?.trim()?.split(/\s+/)[0] ?? user?.email?.split("@")[0] ?? t("TeacherDashboardPage.defaultTeacherName");

  return <div className={styles.page}>
    <PageHeader title={t("TeacherDashboardPage.greeting", { name: firstName })} subtitle={t("TeacherDashboardPage.subtitle")}>
      <button type="button" className={styles.btnPrimary} onClick={() => navigate("/class-setup")}><MIcon name="add" size={18} />{t("TeacherDashboardPage.createClass")}</button>
    </PageHeader>

    {error && <div className={styles.error}><MIcon name="error_outline" size={18} />{error}</div>}

    <section className={styles.metricGrid} aria-label={t("TeacherDashboardPage.summaryAriaLabel")}>
      <article><span className={styles.metricIcon}><MIcon name="task_alt" size={20} /></span><div><small>{t("TeacherDashboardPage.metricCheckpointRate")}</small><strong>{loading ? "—" : `${checkpointSummary.percent}%`}</strong><p>{t("TeacherDashboardPage.metricCheckpointDetail", { completed: checkpointSummary.completed, possible: checkpointSummary.possible })}</p></div></article>
      <article><span className={styles.metricIcon}><MIcon name="groups" size={20} /></span><div><small>{t("TeacherDashboardPage.metricHasRecords")}</small><strong>{loading ? "—" : checkpointSummary.students}</strong><p>{t("TeacherDashboardPage.metricAcrossPaths", { count: reports.length })}</p></div></article>
      <article><span className={styles.metricIcon}><MIcon name="school" size={20} /></span><div><small>{t("TeacherDashboardPage.metricActiveClasses")}</small><strong>{loading ? "—" : classes.filter((item) => item.status !== "archived").length}</strong><p>{t("TeacherDashboardPage.metricStillPreparing", { count: classes.filter((item) => item.status === "planning").length })}</p></div></article>
      <article><span className={styles.metricIcon}><MIcon name="calendar_today" size={20} /></span><div><small>{t("TeacherDashboardPage.metricNextClass")}</small><strong>{upcoming[0] ? formatMonthDay(upcoming[0].session) : "—"}</strong><p>{upcoming[0]?.item.name ?? t("TeacherDashboardPage.noUpcomingClasses")}</p></div></article>
    </section>

    <div className={styles.mainGrid}>
      <section className={styles.panel}>
        <div className={styles.panelHeader}><div><span className={styles.eyebrow}>{t("TeacherDashboardPage.eyebrowLearningOutcomes")}</span><h2>{t("TeacherDashboardPage.checkpointPanelTitle")}</h2><p>{t("TeacherDashboardPage.checkpointPanelDesc")}</p></div><button type="button" className={styles.textButton} onClick={() => navigate("/course-cms?tab=progress")}>{t("TeacherDashboardPage.fullProgress")}<MIcon name="arrow_forward" size={16} /></button></div>
        <div className={styles.checkpointList}>{loading ? <LoadingState text={t("TeacherDashboardPage.loadingCheckpoints")} /> : reports.length ? reports.map((item) => <CheckpointRow key={item.path.id} item={item} onOpen={() => navigate(`/course-cms?tab=progress&pathId=${item.path.id}`)} />) : <EmptyState icon="checklist" title={t("TeacherDashboardPage.noCheckpointData")} action={<button type="button" className={styles.btnSecondary} onClick={() => navigate("/course-cms")}>{t("TeacherDashboardPage.createContent")}</button>} />}</div>
      </section>

      <aside className={styles.panel}>
        <div className={styles.panelHeader}><div><span className={styles.eyebrow}>{t("TeacherDashboardPage.eyebrowFollowUpStudents")}</span><h2>{t("TeacherDashboardPage.incompleteCheckpointTitle")}</h2><p>{t("TeacherDashboardPage.incompleteCheckpointDesc")}</p></div></div>
        <div className={styles.studentList}>{loading ? <LoadingState text={t("TeacherDashboardPage.loadingStudentProgress")} /> : incompleteStudents.length ? incompleteStudents.map((student) => <button key={`${student.pathId}-${student.user_id}`} type="button" onClick={() => navigate(`/course-cms?tab=progress&pathId=${student.pathId}`)}><span className={styles.studentAvatar}>{(student.user_name ?? student.user_email ?? t("TeacherDashboardPage.avatarFallback")).slice(0, 1)}</span><span><strong>{student.user_name ?? student.user_email}</strong><small>{student.pathTitle} · {student.completed_questions}/{student.total_questions}</small></span><em>{Math.round(student.progress_percent)}%</em></button>) : <EmptyState icon="verified" title={reports.length ? t("TeacherDashboardPage.allStudentsComplete") : t("TeacherDashboardPage.noStudentProgress")} />}</div>
      </aside>
    </div>

    <section className={styles.panel}>
      <div className={styles.panelHeader}><div><span className={styles.eyebrow}>{t("TeacherDashboardPage.eyebrowClassSchedule")}</span><h2>{t("TeacherDashboardPage.upcomingClassesTitle")}</h2><p>{t("TeacherDashboardPage.upcomingClassesDesc")}</p></div><button type="button" className={styles.textButton} onClick={() => navigate("/class-management")}>{t("TeacherDashboardPage.allClasses")}<MIcon name="arrow_forward" size={16} /></button></div>
      <div className={styles.classList}>{loading ? <LoadingState text={t("TeacherDashboardPage.loadingClasses")} /> : upcoming.length ? upcoming.map(({ item, session }) => {
        const ready = item.totalMachines ? Math.round(item.readyMachines / item.totalMachines * 100) : 0;
        return <button type="button" key={item.id} className={styles.classRow} onClick={() => navigate(`/class-management/${item.id}`)}><span className={styles.classDate}><strong>{formatMonthDay(session)}</strong><small>{String(item.start_time ?? "").slice(0, 5)}</small></span><span className={styles.classMain}><strong>{item.name}</strong><small>{t("TeacherDashboardPage.classMemberSummary", { count: item.students, nodes: item.nodes.length })}</small></span><span className={styles.classState}><em className={styles[`status_${item.status}`]}>{t(CLASS_STATUS_KEYS[item.status] ?? item.status)}</em><small>{item.status === "active" ? t("TeacherDashboardPage.machinesReady", { percent: ready }) : item.status === "planning" ? t("TeacherDashboardPage.continueClassSetup") : t("TeacherDashboardPage.viewProgress")}</small></span><MIcon name="chevron_right" size={19} /></button>;
      }) : <EmptyState icon="event_available" title={t("TeacherDashboardPage.noUpcomingClasses")} action={<button type="button" className={styles.btnPrimary} onClick={() => navigate("/class-setup")}>{t("TeacherDashboardPage.createClass")}</button>} />}</div>
    </section>
  </div>;
}
