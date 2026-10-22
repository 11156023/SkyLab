import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import LoadingState from "../../../components/LoadingState/LoadingState";
import MIcon from "../../../components/MIcon";
import EmptyState from "../../../components/EmptyState/EmptyState";
import { TeachingClassesService } from "../../../services/teachingClasses";
import { useTeachingClassCacheUpdater, useTeachingClassesQuery } from "../../../queries/teachingClasses";
import { useToast } from "../../../hooks/useToast";
import useRouteMessageToast from "../../../hooks/useRouteMessageToast";
import { useQueryView } from "../../../hooks/useQueryLoadState";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import styles from "../CourseOperations.module.scss";
import PageHeader from "../../../components/PageHeader/PageHeader";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import DataToolbar from "../../../components/DataToolbar/DataToolbar";
import BatchBar, { BatchAction } from "../../../components/DataToolbar/BatchBar";
import SelectCheckbox from "../../../components/DataToolbar/SelectCheckbox";
import ToolbarSearch from "../../../components/DataToolbar/ToolbarSearch";
import useRowSelection from "../../../hooks/useRowSelection";
import { joinList } from "../../../utils/joinList";
import { runBatch } from "../../../utils/runBatch";
import ClassTicket from "./ClassTicket";

/* 批量封存的確認框裡最多列出幾班的名字，其餘寫「等 N 班」 */
const BATCH_NAME_PREVIEW = 5;

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


const EMPTY = Object.freeze([]);

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

/* 後端回 {data: [...]} 或純陣列都接；模組層級的函式讓 useQuery 的 select 結果能被快取 */
function selectClasses(rows) {
  return (rows?.data ?? rows ?? []).map(normalizeClass);
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

/* 點票券去的地方：準備中的班級回到下一個還沒完成的設定步驟，其餘進入班級 */
function ticketTarget(item) {
  if (item.status === "planning") return `/class-setup?classId=${item.id}&step=${classSetupResumeStep(item)}`;
  return `/class-management/${item.id}`;
}

export default function ClassManagementPage() {
  const { t } = useTranslation("teaching");
  const navigate = useNavigate();
  const toast = useToast();
  const confirm = useConfirm();
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [showArchived, setShowArchived] = useState(false);

  /* 班級清單走 TanStack Query（老師首頁、課程 CMS 共用同一份快取）：
     首次載不到才提示，背景刷新失敗沿用上一份 */
  const classesQuery = useTeachingClassesQuery({ select: selectClasses });
  const classCache = useTeachingClassCacheUpdater();
  const classes = classesQuery.data ?? EMPTY;
  /* 沒資料時 v5 每次重抓都把 status 改回 pending、清掉 error：用 useQueryView，首載失敗後的輪詢重試
     期間仍算失敗，不閃回整頁載入中；錯誤訊息在重試期間不變，同一個錯誤只提示一次 */
  const classesView = useQueryView(classesQuery);
  const loading = classesView.loading;
  const loadFailedMessage = classesView.error
    ? classesView.error?.message ?? t("Error.generic", { ns: "common" })
    : null;
  useEffect(() => {
    if (loadFailedMessage) toast.error(loadFailedMessage);
  }, [loadFailedMessage, toast]);

  // 導頁帶來的一次性成功訊息（暫停設定、送出建機）改用 toast，吐完即清掉 state 免得重整重播。
  useRouteMessageToast();

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

  /* 批量封存並回收：已封存的不能再勾；換篩選或搜尋後看不到的自動取消勾選 */
  const selection = useRowSelection(rows.filter((item) => item.status !== "archived").map((item) => item.id));
  const selectedClasses = rows.filter((item) => selection.isSelected(item.id));
  const [batchProgress, setBatchProgress] = useState(null); // { done, total }

  async function archiveSelected() {
    const targets = selectedClasses;
    if (!targets.length || batchProgress) return;
    const names = targets.map((item) => item.name);
    const rest = names.length - BATCH_NAME_PREVIEW;
    const machines = targets.reduce((sum, item) => sum + (item.totalMachines || 0), 0);
    const ok = await confirm({
      title: t("ClassManagementPage.batchArchiveTitle", { count: targets.length }),
      message: `${t("ClassManagementPage.batchArchiveDesc", { machines })}\n${joinList(names.slice(0, BATCH_NAME_PREVIEW))}${rest > 0 ? ` ${t("ClassManagementPage.batchNamesMore", { count: rest })}` : ""}`,
      confirmText: t("ClassManagementPage.batchArchive"),
      danger: true,
    });
    if (!ok) return;
    setBatchProgress({ done: 0, total: targets.length });
    const res = await runBatch(targets, (item) => TeachingClassesService.archive(item.id), {
      concurrency: 2,
      onProgress: ({ done, total }) => setBatchProgress({ done, total }),
    });
    /* 封存成功但有機器沒送出回收：跟單班一樣提醒到班級裡按「重試回收」 */
    const reclaimFailed = res.results
      .filter((result) => result.ok)
      .reduce((sum, result) => sum + (result.value?.reclaim?.failed?.length ?? 0), 0);
    if (res.failed > 0) {
      toast.error(t("ClassManagementPage.batchArchiveFailedKeptToast", { succeeded: res.succeeded, failed: res.failed }));
    } else if (reclaimFailed > 0) {
      toast.error(t("ClassManagementPage.batchArchivedReclaimFailedToast", { count: res.succeeded, machines: reclaimFailed }));
    } else {
      toast.success(t("ClassManagementPage.batchArchivedToast", { count: res.succeeded }));
    }
    selection.replace(res.results.filter((result) => !result.ok).map((result) => result.item.id));
    setBatchProgress(null);
    /* 重抓失敗不會丟例外（沿用舊清單，下次進頁面再載入） */
    await classCache.invalidate();
  }

  return <div className={`${styles.page} ${styles.listPage}`}>
    <PageHeader title={t("ClassManagementPage.title")}>
      <button type="button" className={styles.btnPrimary} onClick={() => navigate("/class-setup")}>
        <MIcon name="add" size={17} />{t("ClassManagementPage.createClass")}
      </button>
    </PageHeader>

    {/* 平常是狀態切換＋搜尋；勾選班級後原地換成批量封存（同一個高度，票券不會跳） */}
    <DataToolbar
      data-guide="class-mgmt-toolbar"
      selecting={selectedClasses.length > 0}
      batch={(
        <BatchBar
          countLabel={t("ClassManagementPage.selectedCount", { count: selectedClasses.length })}
          onClear={selection.clear}
          selectAll={{
            checked: selection.allSelected,
            indeterminate: selection.someSelected,
            onToggle: selection.toggleAll,
          }}
          progress={batchProgress && {
            text: t("ClassManagementPage.batchArchiving", batchProgress),
            done: batchProgress.done,
            total: batchProgress.total,
          }}
        >
          <BatchAction icon="archive" label={t("ClassManagementPage.batchArchive")} danger onClick={archiveSelected} />
        </BatchBar>
      )}
    >
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
      <ToolbarSearch value={query} onChange={setQuery} placeholder={t("ClassManagementPage.searchPlaceholder")} />
      {archivedCount > 0 && <label className={styles.archivedToggle} data-guide="class-mgmt-archived"><input type="checkbox" checked={showArchived} onChange={(event) => { setShowArchived(event.target.checked); if (!event.target.checked) setStatus("all"); }} />{t("ClassManagementPage.showArchived", { count: archivedCount })}</label>}
    </DataToolbar>

    {loading ? <LoadingState fullPage text={t("ClassManagementPage.loadingText")} /> : rows.length ? <section className={styles.classCardGrid}>
      {rows.map((item) => {
        const setupReady = setupChecklist(item).filter(Boolean).length;
        const selectable = item.status !== "archived";
        const checked = selection.isSelected(item.id);
        /* 票券本身是一顆按鈕（點了進班級），勾選框疊在右上角、不能包進按鈕裡 */
        return <div key={item.id} className={styles.ticketSlot} data-guide="class-mgmt-ticket">
        <ClassTicket
          selectable={selectable}
          selected={checked}
          id={item.id}
          status={item.status}
          statusLabel={STATUS_KEYS[item.status] ? t(STATUS_KEYS[item.status]) : item.status}
          term={item.term}
          title={item.name}
          /* 其他狀態的說明只是把狀態換句話說（進度也已在票根），只有「需要處理」要講出原因 */
          hint={item.status === "partial_failed" ? t("ClassManagementPage.hintPartialFailed") : ""}
          fields={[
            {
              key: "schedule",
              label: t("ClassManagementPage.fieldSchedule"),
              value: `${t("ClassManagementPage.weeklyPrefix")}${t(WEEKDAY_KEYS[item.weekday])} ${item.startTime}–${item.endTime}`,
              note: t("ClassManagementPage.bootLeadNote", { minutes: item.bootLeadMinutes }),
            },
            { key: "period", label: t("ClassManagementPage.fieldPeriod"), value: `${item.startDate} ${t("ClassManagementPage.dateTo")} ${item.endDate}` },
          ]}
          progress={item.status === "planning"
            ? { label: t("ClassManagementPage.progressSetup"), done: setupReady, total: SETUP_STEP_COUNT }
            : { label: t("ClassManagementPage.progressBuild"), done: item.readyMachines, total: item.totalMachines }}
          /* 勾選狀態下點整張票改成勾選，不進班級 */
          onOpen={(event) => { if (!selection.rowClick(item.id, event)) navigate(ticketTarget(item)); }}
        />
        {selectable && <SelectCheckbox
          className={styles.ticketCheck}
          checked={checked}
          onToggle={(shiftKey) => selection.toggle(item.id, shiftKey)}
          ariaLabel={t("ClassManagementPage.selectClassAria", { name: item.name })}
        />}
        </div>;
      })}
    </section> : <EmptyState
      icon="school"
      title={classes.length ? t("ClassManagementPage.emptyFilteredTitle") : t("ClassManagementPage.emptyTitle")}
      description={classes.length ? undefined : t("ClassManagementPage.emptyDescription")}
      action={classes.length
        ? <button type="button" className={styles.btnSecondary} onClick={() => { setQuery(""); setStatus("all"); }}><MIcon name="filter_alt_off" size={16} />{t("EmptyState.clearFilters", { ns: "common" })}</button>
        : <button type="button" className={styles.btnPrimary} onClick={() => navigate("/class-setup")}><MIcon name="add" size={17} />{t("ClassManagementPage.createClass")}</button>}
    />}
  </div>;
}
