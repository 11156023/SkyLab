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

/* 「準備中」＝還不能上課的班級：設定中、等待審核、建立中、部分建立失敗 */
const PREPARING_STATUSES = ["planning", "pending_review", "provisioning", "partial_failed"];

const DEMO_STUDENT_NAMES = [
  "王小明", "陳怡君", "林志豪", "張雅婷", "劉冠廷", "黃郁雯", "郭家豪", "李欣儒",
  "周柏翰", "吳思妤", "許庭瑋", "鄭凱文", "蔡佳蓉", "楊承恩", "謝宜庭", "何俊傑",
];

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

function demoStudents(prefix, count, totalQuestions, fullyCompleted, inProgress) {
  return Array.from({ length: count }, (_, index) => {
    const completedQuestions = index < fullyCompleted
      ? totalQuestions
      : index < fullyCompleted + inProgress
        ? Math.max(1, totalQuestions - 1 - (index % Math.max(1, totalQuestions - 1)))
        : 0;
    const name = DEMO_STUDENT_NAMES[index % DEMO_STUDENT_NAMES.length];
    return {
      user_id: `${prefix}-student-${index + 1}`,
      user_name: index < DEMO_STUDENT_NAMES.length ? name : `${name}${Math.floor(index / DEMO_STUDENT_NAMES.length) + 1}`,
      user_email: `${prefix}.student${String(index + 1).padStart(2, "0")}@example.com`,
      completed_questions: completedQuestions,
      total_questions: totalQuestions,
      progress_percent: Math.round(completedQuestions / totalQuestions * 100),
    };
  });
}

export function buildTeacherDashboardDemo(now = new Date()) {
  const today = dateKey(now);
  const endDate = addDaysToDateKey(today, 120);
  const classRows = [
    {
      id: "demo-class-linux-a",
      name: "Linux 系統管理－資工二甲",
      status: "active",
      start_date: today,
      end_date: endDate,
      weekday: weekdayFromDateKey(addDaysToDateKey(today, 1)),
      start_time: "13:10:00",
      member_count: 28,
      machine_nodes: [{ node_key: "linux" }],
      ready_machines: 26,
      total_machines: 28,
    },
    {
      id: "demo-class-linux-b",
      name: "Linux 系統管理－資工二乙",
      status: "active",
      start_date: today,
      end_date: endDate,
      weekday: weekdayFromDateKey(addDaysToDateKey(today, 2)),
      start_time: "09:10:00",
      member_count: 30,
      machine_nodes: [{ node_key: "linux" }],
      ready_machines: 30,
      total_machines: 30,
    },
    {
      id: "demo-class-container",
      name: "容器與自動化－夜間班",
      status: "planning",
      start_date: today,
      end_date: endDate,
      weekday: weekdayFromDateKey(addDaysToDateKey(today, 4)),
      start_time: "18:30:00",
      member_count: 20,
      machine_nodes: [{ node_key: "docker" }, { node_key: "gateway" }],
      ready_machines: 36,
      total_machines: 40,
    },
  ];
  const reportRows = [
    {
      path: { id: "demo-path-linux-a", title: "資工二甲｜第 4 週：使用者與權限" },
      report: {
        total_questions: 3,
        students: demoStudents("linux-a", 28, 3, 16, 8),
      },
    },
    {
      path: { id: "demo-path-linux-b", title: "資工二乙｜第 5 週：網路診斷" },
      report: {
        total_questions: 4,
        students: demoStudents("linux-b", 30, 4, 21, 6),
      },
    },
    {
      path: { id: "demo-path-container", title: "夜間班｜第 3 週：Docker 基礎" },
      report: {
        total_questions: 2,
        students: demoStudents("container", 20, 2, 12, 5),
      },
    },
  ];
  return { classes: classRows, reports: reportRows };
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
  // 同一位學生可能出現在多條學習路徑，人數依 user_id 去重
  const studentIds = new Set();
  const summary = rows.reduce((acc, row) => {
    const students = row.report?.students ?? [];
    acc.completed += students.reduce((sum, student) => sum + Number(student.completed_questions ?? 0), 0);
    acc.possible += students.reduce((sum, student) => sum + Number(student.total_questions ?? 0), 0);
    students.forEach((student) => studentIds.add(String(student.user_id ?? student.user_email)));
    return acc;
  }, { completed: 0, possible: 0 });
  return {
    ...summary,
    students: studentIds.size,
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
  // 還沒有作答紀錄時不顯示 0%／空進度條，免得看起來像「全班都沒做」
  const hasRecords = possible > 0;
  return <button type="button" className={styles.checkpointRow} onClick={onOpen}>
    <span className={styles.courseIcon}><MIcon name="checklist" size={19} /></span>
    <span className={styles.checkpointMain}>
      <span><strong>{item.path.title}</strong><small>{students.length ? t("CheckpointRow.completedCount", { fullyCompleted, total: students.length }) : t("CheckpointRow.noRecords")}</small></span>
      {hasRecords && <span className={styles.progressTrack}><i style={{ width: `${percent}%` }} /></span>}
    </span>
    {/* 沒有紀錄時整格留空（「尚無作答紀錄」已經說明了），保留欄位讓箭頭位置不跳動 */}
    <span className={styles.checkpointMetric}>{hasRecords && <><strong>{percent}%</strong><small>{completed}/{possible}</small></>}</span>
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
  const demoMode = import.meta.env.DEV
    && new URLSearchParams(window.location.search).get("teacherDemo") === "1";

  useEffect(() => {
    let active = true;
    async function load() {
      setLoading(true);
      setError("");
      if (demoMode) {
        const demo = buildTeacherDashboardDemo();
        setClasses(demo.classes.map(normalizeClass));
        setReports(demo.reports);
        setLoading(false);
        return;
      }
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
  }, [demoMode, user?.id]);

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
  // 沒填姓名時不拿 email 帳號湊名字，直接打招呼
  const firstName = user?.full_name?.trim()?.split(/\s+/)[0];

  // 資料是同一次請求抓回來的，整頁一個載入動畫就好
  if (loading) return <LoadingState fullPage />;

  // 載入失敗時統計卡一律「—」，不顯示看起來像真資料的 0
  const failed = Boolean(error);
  const hasCheckpoints = checkpointSummary.possible > 0;
  const nextClass = upcoming[0];
  const metrics = [
    {
      key: "checkpoint",
      icon: "task_alt",
      label: t("TeacherDashboardPage.metricCheckpointRate"),
      value: hasCheckpoints ? `${checkpointSummary.percent}%` : "—",
      detail: hasCheckpoints ? t("TeacherDashboardPage.metricCheckpointDetail", { completed: checkpointSummary.completed, possible: checkpointSummary.possible }) : t("CheckpointRow.noRecords"),
      path: "/course-cms?tab=progress",
    },
    {
      key: "students",
      icon: "groups",
      label: t("TeacherDashboardPage.metricHasRecords"),
      value: t("TeacherDashboardPage.metricStudentsValue", { count: checkpointSummary.students }),
      detail: t("TeacherDashboardPage.metricAcrossPaths", { count: reports.length }),
      path: "/course-cms?tab=progress",
    },
    {
      key: "classes",
      icon: "school",
      label: t("TeacherDashboardPage.metricActiveClasses"),
      value: classes.filter((item) => item.status === "active").length,
      detail: t("TeacherDashboardPage.metricStillPreparing", { count: classes.filter((item) => PREPARING_STATUSES.includes(item.status)).length }),
      path: "/class-management",
    },
    {
      key: "next",
      icon: "calendar_today",
      label: t("TeacherDashboardPage.metricNextClass"),
      value: nextClass ? formatMonthDay(nextClass.session) : "—",
      detail: nextClass?.item.name ?? t("TeacherDashboardPage.noUpcomingClasses"),
      path: nextClass ? `/class-management/${nextClass.item.id}` : "/class-management",
    },
  ];

  return <div className={styles.page}>
    <PageHeader title={firstName ? t("TeacherDashboardPage.greeting", { name: firstName }) : t("TeacherDashboardPage.greetingNoName")}>
      <button type="button" className={styles.btnPrimary} onClick={() => navigate("/class-setup")}><MIcon name="add" size={18} />{t("TeacherDashboardPage.createClass")}</button>
    </PageHeader>

    {error && <div className={styles.error}><MIcon name="error_outline" size={18} />{error}</div>}

    <section className={styles.metricGrid} aria-label={t("TeacherDashboardPage.summaryAriaLabel")}>
      {metrics.map((metric) => <button type="button" key={metric.key} className={styles.metricCard} onClick={() => navigate(metric.path)}>
        <span className={styles.metricIcon}><MIcon name={metric.icon} size={20} /></span>
        <span className={styles.metricContent}>
          <small>{metric.label}</small>
          <strong>{failed ? "—" : metric.value}</strong>
          {!failed && <span className={styles.metricDetail}>{metric.detail}</span>}
        </span>
        <MIcon name="chevron_right" size={17} className={styles.metricArrow} />
      </button>)}
    </section>

    <div className={styles.mainGrid}>
      <section className={styles.panel}>
        <div className={styles.panelHeader}><div><h2>{t("TeacherDashboardPage.checkpointPanelTitle")}</h2></div><button type="button" className={styles.textButton} onClick={() => navigate("/course-cms?tab=progress")}>{t("TeacherDashboardPage.fullProgress")}<MIcon name="arrow_forward" size={16} /></button></div>
        <div className={styles.checkpointList}>{reports.length ? reports.map((item) => <CheckpointRow key={item.path.id} item={item} onOpen={() => navigate(`/course-cms?tab=progress&pathId=${item.path.id}`)} />) : <EmptyState icon="checklist" title={t("TeacherDashboardPage.noCheckpointData")} action={<button type="button" className={styles.btnSecondary} onClick={() => navigate("/course-cms")}>{t("TeacherDashboardPage.createContent")}</button>} />}</div>
      </section>

      <aside className={styles.panel}>
        <div className={styles.panelHeader}><div><h2>{t("TeacherDashboardPage.incompleteCheckpointTitle")}</h2><p>{t("TeacherDashboardPage.incompleteCheckpointDesc")}</p></div></div>
        {/* 有學生紀錄、且全部完成才說「都已完成」；連一筆紀錄都沒有時是「尚無學生進度」 */}
        <div className={styles.studentList}>{incompleteStudents.length ? incompleteStudents.map((student) => <button key={`${student.pathId}-${student.user_id}`} type="button" onClick={() => navigate(`/course-cms?tab=progress&pathId=${student.pathId}`)}><span className={styles.studentAvatar}>{(student.user_name ?? student.user_email ?? t("TeacherDashboardPage.avatarFallback")).slice(0, 1)}</span><span><strong>{student.user_name ?? student.user_email}</strong><small>{student.pathTitle} · {student.completed_questions}/{student.total_questions}</small></span><em>{Math.round(student.progress_percent)}%</em></button>) : <EmptyState icon="verified" title={checkpointSummary.students ? t("TeacherDashboardPage.allStudentsComplete") : t("TeacherDashboardPage.noStudentProgress")} />}</div>
      </aside>
    </div>

    <section className={styles.panel}>
      <div className={styles.panelHeader}><div><h2>{t("TeacherDashboardPage.upcomingClassesTitle")}</h2></div><button type="button" className={styles.textButton} onClick={() => navigate("/class-management")}>{t("TeacherDashboardPage.allClasses")}<MIcon name="arrow_forward" size={16} /></button></div>
      {/* 頁首已有「建立班級」，空狀態不再放第二顆主要按鈕 */}
      <div className={styles.classList}>{upcoming.length ? upcoming.map(({ item, session }) => {
        const ready = item.totalMachines ? Math.round(item.readyMachines / item.totalMachines * 100) : 0;
        const hint = item.status === "active" ? t("TeacherDashboardPage.machinesReady", { percent: ready }) : item.status === "planning" ? t("TeacherDashboardPage.continueClassSetup") : null;
        return <button type="button" key={item.id} className={styles.classRow} onClick={() => navigate(`/class-management/${item.id}`)}><span className={styles.classDate}><strong>{formatMonthDay(session)}</strong><small>{String(item.start_time ?? "").slice(0, 5)}</small></span><span className={styles.classMain}><strong>{item.name}</strong><small>{t("TeacherDashboardPage.classMemberSummary", { count: item.students, nodes: item.nodes.length })}</small></span><span className={styles.classState}><em className={styles[`status_${item.status}`]}>{t(CLASS_STATUS_KEYS[item.status] ?? item.status)}</em>{hint && <small>{hint}</small>}</span><MIcon name="chevron_right" size={19} /></button>;
      }) : <EmptyState icon="event_available" title={t("TeacherDashboardPage.noUpcomingClasses")} />}</div>
    </section>
  </div>;
}
