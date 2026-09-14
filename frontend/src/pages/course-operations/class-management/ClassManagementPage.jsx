import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import LoadingState from "../../../components/LoadingState/LoadingState";
import MIcon from "../../../components/MIcon";
import EmptyState from "../../../components/EmptyState/EmptyState";
import { TeachingClassesService } from "../../../services/teachingClasses";
import { useToast } from "../../../hooks/useToast";
import styles from "../CourseOperations.module.scss";
import PageHeader from "../../../components/PageHeader/PageHeader";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";

const STATUS_KEYS = {
  planning: "ClassManagementPage.statusPlanning",
  pending_review: "ClassManagementPage.statusPendingReview",
  provisioning: "ClassManagementPage.statusProvisioning",
  partial_failed: "ClassManagementPage.statusPartialFailed",
  active: "ClassManagementPage.statusActive",
  archived: "ClassManagementPage.statusArchived",
};

// 分頁是導航（我要找哪一批），徽章才是細節（這一班現在怎樣），所以粒度不同：
// 「等待審核」與「正在建立」對老師都是「等，什麼都不能做」，合併成一個桶。
// 「需要處理」是告警不是分類，count 為 0 時不佔位置。
const FILTER_GROUPS = [
  { key: "all", labelKey: "ClassManagementPage.filterAll", statuses: null },
  { key: "planning", labelKey: "ClassManagementPage.filterPlanning", statuses: ["planning"] },
  { key: "building", labelKey: "ClassManagementPage.filterBuilding", statuses: ["pending_review", "provisioning"] },
  { key: "partial_failed", labelKey: "ClassManagementPage.filterPartialFailed", statuses: ["partial_failed"], alertOnly: true },
  { key: "active", labelKey: "ClassManagementPage.filterActive", statuses: ["active"] },
];

const SETUP_STEP_COUNT = 3;

const WEEKDAY_KEYS = [
  "ClassManagementPage.weekdayMon",
  "ClassManagementPage.weekdayTue",
  "ClassManagementPage.weekdayWed",
  "ClassManagementPage.weekdayThu",
  "ClassManagementPage.weekdayFri",
  "ClassManagementPage.weekdaySat",
  "ClassManagementPage.weekdaySun",
];

const STATUS_HINT_KEYS = {
  planning: "ClassManagementPage.hintPlanning",
  pending_review: "ClassManagementPage.hintPendingReview",
  provisioning: "ClassManagementPage.hintProvisioning",
  partial_failed: "ClassManagementPage.hintPartialFailed",
  active: "ClassManagementPage.hintActive",
  archived: "ClassManagementPage.hintArchived",
};

function normalizeClass(item) {
  return {
    ...item,
    id: String(item.id),
    startDate: item.start_date,
    endDate: item.end_date,
    startTime: String(item.start_time ?? "").slice(0, 5),
    endTime: String(item.end_time ?? "").slice(0, 5),
    bootLeadMinutes: item.boot_lead_minutes,
    students: item.member_count ?? item.students?.length ?? 0,
    weeks: item.weeks ?? [],
    nodes: item.machine_nodes ?? [],
    readyMachines: item.ready_machines ?? 0,
    totalMachines: item.total_machines ?? 0,
  };
}

export function setupChecklist(item) {
  return [
    item.students > 0,
    Boolean(item.course_environment) && (item.nodes?.length ?? 0) > 0,
    (item.weeks ?? []).some((week) => String(week.title ?? "").trim()),
  ];
}

export function classSetupResumeStep(item) {
  const [hasStudents, hasEnvironment, hasTasks] = setupChecklist(item);
  if (!hasStudents) return 2;
  if (!hasEnvironment) return 3;
  if (!hasTasks) return 4;
  return 5;
}

function nextAction(item, t) {
  if (item.status === "planning") {
    const step = classSetupResumeStep(item);
    const labels = {
      2: t("ClassManagementPage.actionContinueStudents"),
      3: t("ClassManagementPage.actionContinueEnv"),
      4: t("ClassManagementPage.actionContinueTasks"),
      5: t("ClassManagementPage.actionConfirmSubmit"),
    };
    return [labels[step], `/class-setup?classId=${item.id}&step=${step}`];
  }
  if (item.status === "partial_failed") return [t("ClassManagementPage.actionViewFailed"), `/class-management/${item.id}`];
  if (item.status === "active") return [t("ClassManagementPage.actionEnterClass"), `/class-management/${item.id}`];
  if (item.status === "archived") return [t("ClassManagementPage.actionViewClass"), `/class-management/${item.id}`];
  return [t("ClassManagementPage.actionViewProgress"), `/class-management/${item.id}`];
}

export default function ClassManagementPage() {
  const { t } = useTranslation("teaching");
  const navigate = useNavigate();
  const location = useLocation();
  const toast = useToast();
  const [classes, setClasses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [showArchived, setShowArchived] = useState(false);

  useEffect(() => {
    let active = true;
    TeachingClassesService.list()
      .then((rows) => active && setClasses((rows?.data ?? rows ?? []).map(normalizeClass)))
      .catch((reason) => active && toast.error(reason?.message ?? t("ClassManagementPage.loadFailed")))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [toast, t]);

  // 導頁帶來的一次性成功訊息改用 toast，吐完即清掉 state 免得重整重播。
  useEffect(() => {
    if (!location.state?.message) return;
    toast.success(location.state.message);
    navigate(location.pathname, { replace: true, state: {} });
  }, [location.state?.message, location.pathname, navigate, toast]);

  const archivedCount = useMemo(
    () => classes.filter((item) => item.status === "archived").length,
    [classes],
  );

  const visible = useMemo(
    () => classes.filter((item) => showArchived || item.status !== "archived"),
    [classes, showArchived],
  );

  const statusCounts = useMemo(() => {
    const counts = { all: visible.length };
    FILTER_GROUPS.forEach((group) => {
      if (group.statuses) counts[group.key] = visible.filter((item) => group.statuses.includes(item.status)).length;
    });
    return counts;
  }, [visible]);

  const tabs = useMemo(
    () => FILTER_GROUPS.filter((group) => !group.alertOnly || statusCounts[group.key] > 0 || status === group.key),
    [statusCounts, status],
  );

  const rows = useMemo(() => {
    const group = FILTER_GROUPS.find((entry) => entry.key === status);
    return visible.filter((item) => (!group?.statuses || group.statuses.includes(item.status))
      && item.name.toLowerCase().includes(query.toLowerCase()));
  }, [visible, query, status]);

  return <div className={`${styles.page} ${styles.listPage}`}>
    <PageHeader title={t("ClassManagementPage.title")} subtitle={t("ClassManagementPage.subtitle")}>
      <button type="button" className={styles.btnPrimary} onClick={() => navigate("/class-setup")}>
        <MIcon name="add" size={17} />{t("ClassManagementPage.createClass")}
      </button>
    </PageHeader>

    <div className={styles.classToolbar}>
      <SegmentedControl
        options={tabs.map((group) => ({
          value: group.key,
          label: t(group.labelKey),
          icon: group.alertOnly ? "warning" : undefined,
          badge: statusCounts[group.key] ?? 0,
        }))}
        value={status}
        onChange={setStatus}
        ariaLabel={t("ClassManagementPage.filterAriaLabel")}
      />
      <label className={styles.searchInput}><MIcon name="search" size={18} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("ClassManagementPage.searchPlaceholder")} /></label>
      {archivedCount > 0 && <label className={styles.archivedToggle}><input type="checkbox" checked={showArchived} onChange={(event) => { setShowArchived(event.target.checked); if (!event.target.checked) setStatus("all"); }} />{t("ClassManagementPage.showArchived", { count: archivedCount })}</label>}
    </div>

    {loading ? <LoadingState fullPage text={t("ClassManagementPage.loadingText")} /> : rows.length ? <section className={styles.classCardGrid}>
      {rows.map((item) => {
        const setupReady = setupChecklist(item).filter(Boolean).length;
        const progress = item.status === "planning" ? setupReady / SETUP_STEP_COUNT * 100 : item.totalMachines ? item.readyMachines / item.totalMachines * 100 : 0;
        const [action, target] = nextAction(item, t);
        return <article className={`${styles.classCard}${item.status === "partial_failed" ? ` ${styles.classCardAlert}` : ""}`} key={item.id}>
          <button type="button" className={styles.classCardMain} onClick={() => navigate(`/class-management/${item.id}`)}>
            <div className={styles.classCardTop}>
              <div><span>{item.term}</span><h2>{item.name}</h2></div>
              <span className={`${styles.statusBadge} ${styles[`status_${item.status}`]}`}>{STATUS_KEYS[item.status] ? t(STATUS_KEYS[item.status]) : item.status}</span>
            </div>
            <div className={styles.classMeta}>
              <strong><MIcon name="schedule" size={16} />{t("ClassManagementPage.weeklyPrefix")}{t(WEEKDAY_KEYS[item.weekday])} {item.startTime}–{item.endTime}</strong>
              <span><MIcon name="calendar_today" size={15} />{item.startDate} {t("ClassManagementPage.dateTo")} {item.endDate}</span>
              <span><MIcon name="power_settings_new" size={15} />{t("ClassManagementPage.bootLeadLabel", { minutes: item.bootLeadMinutes })}</span>
            </div>
            <div className={styles.classProgress}><div><span>{item.status === "planning" ? t("ClassManagementPage.progressSetup") : t("ClassManagementPage.progressBuild")}</span><strong>{item.status === "planning" ? `${setupReady}/${SETUP_STEP_COUNT}` : `${item.readyMachines}/${item.totalMachines}`}</strong></div><i><b style={{ width: `${progress}%` }} /></i></div>
          </button>
          <div className={styles.classStats}>
            <div><strong>{item.students}</strong><span>{t("ClassManagementPage.unitStudents")}</span></div>
            <div><strong>{item.weeks.length}</strong><span>{t("ClassManagementPage.unitSessions")}</span></div>
            <div><strong>{item.nodes.length}</strong><span>{t("ClassManagementPage.unitMachinesPerPerson")}</span></div>
          </div>
          <div className={styles.classCardAction}><span>{STATUS_HINT_KEYS[item.status] ? t(STATUS_HINT_KEYS[item.status]) : ""}</span><button type="button" onClick={() => navigate(target)}>{action}<MIcon name="arrow_forward" size={16} /></button></div>
        </article>;
      })}
    </section> : <EmptyState
      icon="school"
      title={classes.length ? t("ClassManagementPage.emptyFilteredTitle") : t("ClassManagementPage.emptyTitle")}
      description={classes.length ? undefined : t("ClassManagementPage.emptyDescription")}
    />}
  </div>;
}
