import { useEffect, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import MIcon from "../../../../components/MIcon";
import { useAuth } from "../../../../contexts/AuthContext";
import { useTeachingClassesQuery } from "../../../../queries/teachingClasses";
import styles from "./TeacherDashboardPage.module.scss";
import PageHeader from "../../../../components/PageHeader/PageHeader";
import EmptyState from "../../../../components/EmptyState/EmptyState";
import LoadingState from "../../../../components/LoadingState/LoadingState";
import { formatMonthDay } from "../../../../utils/formatDate";
import { useToast } from "../../../../hooks/useToast";
import { useQueryView } from "../../../../hooks/useQueryLoadState";
import { taipeiDateKey } from "../taipeiDate";

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

export function buildTeacherDashboardDemo(now = new Date()) {
  const today = taipeiDateKey(now);
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
  return { classes: classRows };
}

export function nextClassSession(item, now = new Date()) {
  if (!item?.start_date || !item?.end_date) return null;
  const start = taipeiDateTime(item.start_date);
  const end = new Date(`${item.end_date}T23:59:59+08:00`);
  if (now > end) return null;
  const targetWeekday = Number(item.weekday ?? 0);
  const baseDate = now < start ? item.start_date : taipeiDateKey(now);
  const daysUntilTarget = (targetWeekday - weekdayFromDateKey(baseDate) + 7) % 7;
  let sessionDate = addDaysToDateKey(baseDate, daysUntilTarget);
  let session = taipeiDateTime(sessionDate, String(item.start_time ?? "00:00"));
  if (session < start || session < now) {
    sessionDate = addDaysToDateKey(sessionDate, 7);
    session = taipeiDateTime(sessionDate, String(item.start_time ?? "00:00"));
  }
  return session <= end ? session : null;
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

/* 模組層級的 select：結果才會被快取，資料沒變時不重算 */
function selectDashboardClasses(classRows) {
  return (classRows?.data ?? classRows ?? []).map(normalizeClass);
}

const EMPTY = [];

/** 問候語用的稱呼：姓名第一段；沒填姓名（或全空白）時回 null，由呼叫端改用不帶名字的問候。
 *  不拿 email 帳號湊名字；用 || 而非 ??：全空白的 full_name trim 後是空字串，不能拿來當名字。 */
export function teacherDisplayName(user) {
  return user?.full_name?.trim().split(/\s+/)[0] || null;
}

export default function TeacherDashboardPage() {
  const { t } = useTranslation("personal");
  const navigate = useNavigate();
  const { user } = useAuth();
  const toast = useToast();
  const demoMode = import.meta.env.DEV
    && new URLSearchParams(window.location.search).get("teacherDemo") === "1";
  const demoClasses = useMemo(
    () => (demoMode ? buildTeacherDashboardDemo().classes.map(normalizeClass) : null),
    [demoMode],
  );

  /* 班級清單與班級管理、課程 CMS 共用同一份快取（背景 30 秒刷新）；示範模式不打 API */
  const classesQuery = useTeachingClassesQuery({ enabled: !demoMode, select: selectDashboardClasses });
  const classes = demoClasses ?? classesQuery.data ?? EMPTY;
  /* 首載失敗後背景輪詢重試期間維持失敗畫面，不閃回整頁載入中（見 useQueryView） */
  const classesView = useQueryView(classesQuery);
  const loading = !demoMode && classesView.loading;
  // 載入失敗時統計卡一律「—」，不顯示看起來像真資料的 0
  const failed = !demoMode && Boolean(classesView.error);
  /* 依訊息字串觸發：還沒有資料時輪詢重試又失敗，訊息相同就不重複提示；背景刷新失敗沿用上一份、不提示。
     錯誤取自 useQueryView：輪詢重試期間不會先清空再變回來，才不會每輪重跳 */
  const loadFailedMessage = failed
    ? classesView.error.message ?? t("TeacherDashboardPage.loadError")
    : null;
  useEffect(() => {
    if (loadFailedMessage) toast.error(loadFailedMessage);
  }, [loadFailedMessage, toast]);

  const upcoming = useMemo(() => classes
    .map((item) => ({ item, session: nextClassSession(item) }))
    .filter((row) => row.session)
    .sort((a, b) => a.session - b.session)
    .slice(0, 4), [classes]);
  // 沒填姓名時不拿 email 帳號湊名字，直接打招呼
  const firstName = teacherDisplayName(user);

  // 資料是同一次請求抓回來的，整頁一個載入動畫就好
  if (loading) return <LoadingState fullPage />;

  const nextClass = upcoming[0];
  const metrics = [
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

    <section className={styles.metricGrid} aria-label={t("TeacherDashboardPage.summaryAriaLabel")} data-guide="teacher-home-metrics">
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

    <section className={styles.panel} data-guide="teacher-home-classes">
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
