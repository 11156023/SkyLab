import { useEffect, useMemo, useRef, useState } from "react";
import { keepPreviousData } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { createColumnHelper, useTable } from "@tanstack/react-table";
import styles from "./JobsPage.module.scss";
import MIcon from "../../../components/MIcon";
import DataTable from "../../../components/DataTable/DataTable";
import { baseFeatures } from "../../../components/DataTable/features";
import LoadingState from "../../../components/LoadingState/LoadingState";
import SharedEmptyState from "../../../components/EmptyState/EmptyState";
import ErrorState from "../../../components/ErrorState/ErrorState";
import { useJobsQuery } from "../../../queries/jobs";
import JobDetailDialog from "../../../components/Jobs/JobDetailDialog";
import { useToast } from "../../../hooks/useToast";
import { useQueryView } from "../../../hooks/useQueryLoadState";
import PageHeader from "../../../components/PageHeader/PageHeader";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import { formatDateTime } from "../../../utils/formatDate";

/** 仍視為「進行中」的狀態；需與後端 schemas/jobs.py 的 ACTIVE_JOB_STATUSES 一致 */
const ACTIVE_STATUSES = new Set(["pending", "running", "blocked"]);

const EMPTY = Object.freeze([]);

/** 篩選 → JobsService.list 的參數（同一組篩選產生同一個 query key） */
function buildListParams(kind, status) {
  const params = { limit: 200 };
  if (kind !== "all") params.kinds = [kind];
  if (status === "active") params.activeOnly = true;
  else if (status !== "all") params.statuses = [status];
  return params;
}

function useKindLabels() {
  const { t } = useTranslation("system");
  return {
    vm_request:    t("JobsPage.kindVmRequest"),
    spec_change:   t("JobsPage.kindSpecChange"),
    deletion:      t("JobsPage.kindDeletion"),
    template:      t("JobsPage.kindTemplate"),
    resource_reset:  t("JobsPage.kindResourceReset"),
    batch_provision: t("JobsPage.kindBatchProvision"),
    resource_backup:  t("JobsPage.kindResourceBackup"),
    resource_restore: t("JobsPage.kindResourceRestore"),
  };
}

function useStatusLabels() {
  const { t } = useTranslation("system");
  return {
    pending:   t("JobsPage.statusPending"),
    running:   t("JobsPage.statusRunning"),
    completed: t("JobsPage.statusCompleted"),
    failed:    t("JobsPage.statusFailed"),
    blocked:   t("JobsPage.statusBlocked"),
    cancelled: t("JobsPage.statusCancelled"),
  };
}

function EmptyState({ onClear }) {
  const { t } = useTranslation("system");
  return (
    <SharedEmptyState icon="hourglass_empty" title={t("JobsPage.emptyNoResult")}
      action={onClear && <button type="button" className={styles.btnSecondary} onClick={onClear}><MIcon name="filter_alt_off" size={16} />{t("EmptyState.clearFilters", { ns: "common" })}</button>} />
  );
}

function StatusBadge({ status }) {
  const STATUS_LABELS = useStatusLabels();
  const label = STATUS_LABELS[status] ?? status ?? "—";
  return (
    <span className={`${styles.badge} ${styles[`badge_${status ?? "unknown"}`]}`}>
      <span className={styles.dot} />
      {label}
    </span>
  );
}

function Progress({ value }) {
  const v = Math.max(0, Math.min(100, value ?? 0));
  return (
    <div className={styles.progressWrap}>
      <div className={styles.progressBar}>
        <div className={styles.progressFill} style={{ width: `${v}%` }} />
      </div>
      <span className={styles.progressLabel}>{v}%</span>
    </div>
  );
}

/** 表頭文字（header 是元件，可以直接用 hook） */
function ColTitle({ name }) {
  const { t } = useTranslation("system");
  return t(`JobsPage.${name}`);
}

function KindCell({ kind }) {
  const KIND_LABELS = useKindLabels();
  return KIND_LABELS[kind] ?? kind;
}

const columnHelper = createColumnHelper();

/* 欄位定義放模組層級：FlexRender 把函式型的 header／cell 當元件掛載，render 裡新建會整格重新掛載 */
const jobColumns = columnHelper.columns([
  columnHelper.accessor("title", {
    header: () => <ColTitle name="colTask" />,
    cell: ({ row }) => {
      const j = row.original;
      return (
        <div className={styles.nameCell}>
          <div className={styles.nameIcon}>
            <MIcon name="task" size={18} />
          </div>
          <div>
            <div className={styles.namePrimary} title={j.title ?? j.id}>{j.title ?? j.id}</div>
            <div className={styles.nameSub}>{j.id}</div>
          </div>
        </div>
      );
    },
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.accessor("kind", {
    header: () => <ColTitle name="colKind" />,
    cell: ({ row }) => <KindCell kind={row.original.kind} />,
    meta: { thClassName: styles.th, tdClassName: `${styles.td} ${styles.tdNowrap}` },
  }),
  columnHelper.accessor("status", {
    header: () => <ColTitle name="colStatus" />,
    cell: ({ row }) => <StatusBadge status={row.original.status} />,
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.accessor("progress", {
    header: () => <ColTitle name="colProgress" />,
    cell: ({ row }) => <Progress value={row.original.progress} />,
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.accessor("created_at", {
    header: () => <ColTitle name="colCreatedAt" />,
    cell: ({ row }) => formatDateTime(row.original.created_at),
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.accessor("updated_at", {
    header: () => <ColTitle name="colUpdatedAt" />,
    cell: ({ row }) => formatDateTime(row.original.updated_at),
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.accessor("user_email", {
    header: () => <ColTitle name="colApplicant" />,
    cell: ({ row }) => row.original.user_email ?? "—",
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
]);

export default function JobsPage() {
  const { t } = useTranslation("system");
  const toast = useToast();
  const KIND_LABELS = useKindLabels();
  const STATUS_LABELS = useStatusLabels();
  const KIND_OPTIONS = [
    { value: "all", label: t("JobsPage.allKinds") },
    ...Object.entries(KIND_LABELS).map(([value, label]) => ({ value, label })),
  ];
  const STATUS_OPTIONS = [
    { value: "all",    label: t("JobsPage.allStatuses") },
    { value: "active", label: t("JobsPage.statusActive") },
    ...Object.entries(STATUS_LABELS).map(([value, label]) => ({ value, label })),
  ];
  const [kind, setKind] = useState("all");
  const [status, setStatus] = useState("all");
  const [focusJobId, setFocusJobId] = useState(null);

  /* 每組篩選各自一份快取（key 不同），快速切換時較晚回來的舊條件結果只會寫進它自己的 key，
     不會蓋掉目前篩選的清單。換篩選時先留著上一份清單（keepPreviousData），不閃回載入畫面；
     背景 30 秒刷新、分頁隱藏時暫停 */
  const filterKey = `${kind}|${status}`;
  const jobsQuery = useJobsQuery(buildListParams(kind, status), { placeholderData: keepPreviousData });
  const jobs = jobsQuery.data?.items ?? EMPTY;
  const table = useTable({
    features: baseFeatures,
    columns: jobColumns,
    data: jobs,
    getRowId: (j) => String(j.id),
  });
  /* 錯誤畫面在背景重試期間不閃回載入中；換篩選後新 key 的失敗照樣顯示（見 useQueryView） */
  const { loading, error, retry: retryJobs } = useQueryView(jobsQuery);

  /* 首次載入或換篩選失敗才跳 toast（同原本只有非靜默載入才提示）；
     同一組篩選在背景重試又失敗不重複跳，換篩選後重新計算 */
  const toastedFilterRef = useRef(null);
  useEffect(() => {
    toastedFilterRef.current = null;
  }, [filterKey]);
  const loadError = error ? jobsQuery.error : null;
  useEffect(() => {
    if (!loadError || toastedFilterRef.current === filterKey) return;
    toastedFilterRef.current = filterKey;
    toast.error(loadError?.message ?? t("Error.generic", { ns: "common" }));
  }, [loadError, filterKey, toast, t]);

  /* 三張卡的口徑要一致：後端只回全域的 active_count，沒有全域的
     completed／failed，所以三個數字一律用目前這份清單算，並在卡片上
     標示「目前篩選」，免得「進行中」是全站數、另外兩個是篩選後的數。 */
  const stats = useMemo(() => ({
    active: jobs.filter((j) => ACTIVE_STATUSES.has(j.status)).length,
    completed: jobs.filter((j) => j.status === "completed").length,
    failed: jobs.filter((j) => j.status === "failed").length,
  }), [jobs]);

  return (
    <div className={styles.page}>
      <PageHeader title={t("JobsPage.pageTitle")} />

      {/* 三個數字都是這份清單算出來的，卡片上用同一個副標說明範圍 */}
      <div className={styles.statRow} data-guide="jobs-stats">
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconBusy}`}>
            <MIcon name="autorenew" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>{t("JobsPage.statActive")}</span>
            <span className={styles.statValue}>{stats.active}</span>
            <span className={styles.statScope}>{t("JobsPage.statScopeCurrentFilter")}</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconOk}`}>
            <MIcon name="task_alt" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>{t("JobsPage.statCompleted")}</span>
            <span className={styles.statValue}>{stats.completed}</span>
            <span className={styles.statScope}>{t("JobsPage.statScopeCurrentFilter")}</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconErr}`}>
            <MIcon name="error_outline" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>{t("JobsPage.statFailed")}</span>
            <span className={styles.statValue}>{stats.failed}</span>
            <span className={styles.statScope}>{t("JobsPage.statScopeCurrentFilter")}</span>
          </div>
        </div>
      </div>

      <div className={styles.toolbar}>
        <div className={styles.filterGroup} data-guide="jobs-kind-filter">
          <SegmentedControl
            className={styles.filterTabs}
            options={KIND_OPTIONS}
            value={kind}
            onChange={setKind}
            ariaLabel={t("JobsPage.filterKind")}
          />
        </div>
        <div className={styles.filterGroup} data-guide="jobs-status-filter">
          <SegmentedControl
            className={styles.filterTabs}
            options={STATUS_OPTIONS}
            value={status}
            onChange={setStatus}
            ariaLabel={t("JobsPage.filterStatus")}
          />
        </div>
      </div>

      <div className={styles.content}>
        {error ? (
          /* 這組篩選一筆都還沒抓到就失敗：不能顯示成「沒有結果」 */
          <ErrorState onRetry={retryJobs} />
        ) : loading ? (
          <LoadingState fullPage text={t("JobsPage.loading")} />
        ) : jobs.length === 0 ? (
          <EmptyState onClear={kind !== "all" || status !== "all" ? () => { setKind("all"); setStatus("all"); } : undefined} />
        ) : (
          <div className={styles.tableWrap} data-guide="jobs-table">
            <DataTable
              table={table}
              className={styles.table}
              rowClassName={() => styles.tr}
              rowProps={(row) => ({ onClick: () => setFocusJobId(row.original.id) })}
            />
          </div>
        )}
      </div>

      <JobDetailDialog jobId={focusJobId} onClose={() => setFocusJobId(null)} />
    </div>
  );
}
