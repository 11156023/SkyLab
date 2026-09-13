import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./JobsPage.module.scss";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import SharedEmptyState from "../../../components/EmptyState/EmptyState";
import { JobsService } from "../../../services/jobs";
import JobDetailDialog from "../../../components/Jobs/JobDetailDialog";
import { useToast } from "../../../hooks/useToast";
import useAutoRefresh from "../../../hooks/useAutoRefresh";
import PageHeader from "../../../components/PageHeader/PageHeader";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import { formatDateTime } from "../../../utils/formatDate";

function useKindLabels() {
  const { t } = useTranslation("system");
  return {
    vm_request:    t("JobsPage.kindVmRequest"),
    spec_change:   t("JobsPage.kindSpecChange"),
    deletion:      t("JobsPage.kindDeletion"),
    template:      t("JobsPage.kindTemplate"),
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

function EmptyState() {
  const { t } = useTranslation("system");
  return (
    <SharedEmptyState icon="hourglass_empty" title={t("JobsPage.emptyNoResult")} />
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
  const [jobs, setJobs] = useState([]);
  const [activeCount, setActiveCount] = useState(0);
  const [kind, setKind] = useState("all");
  const [status, setStatus] = useState("all");
  const [loading, setLoading] = useState(true);
  const [focusJobId, setFocusJobId] = useState(null);

  /** silent = true 時不觸發 loading 與錯誤提示，供背景自動刷新使用 */
  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const params = { limit: 200 };
      if (kind !== "all") params.kinds = [kind];
      if (status === "active") params.activeOnly = true;
      else if (status !== "all") params.statuses = [status];
      const res = await JobsService.list(params);
      setJobs(res?.items ?? []);
      setActiveCount(res?.active_count ?? 0);
    } catch (e) {
      if (!silent) toast.error(e?.message ?? t("JobsPage.toastLoadFailed"));
    } finally {
      if (!silent) setLoading(false);
    }
  }, [kind, status, toast, t]);

  useEffect(() => { load(); }, [load]);
  useAutoRefresh(() => load(true));

  const stats = useMemo(() => {
    const completed = jobs.filter((j) => j.status === "completed").length;
    const failed = jobs.filter((j) => j.status === "failed").length;
    return { active: activeCount, completed, failed };
  }, [jobs, activeCount]);

  const visible = jobs;

  return (
    <div className={styles.page}>
      <PageHeader title={t("JobsPage.pageTitle")} subtitle={t("JobsPage.pageSubtitle")} />

      <div className={styles.statRow}>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconBusy}`}>
            <MIcon name="autorenew" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>{t("JobsPage.statActive")}</span>
            <span className={styles.statValue}>{stats.active}</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconOk}`}>
            <MIcon name="task_alt" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>{t("JobsPage.statCompleted")}</span>
            <span className={styles.statValue}>{stats.completed}</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconErr}`}>
            <MIcon name="error_outline" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>{t("JobsPage.statFailed")}</span>
            <span className={styles.statValue}>{stats.failed}</span>
          </div>
        </div>
      </div>

      <div className={styles.toolbar}>
        <div className={styles.filterGroup}>
          <span className={styles.selectLabel}>{t("JobsPage.filterKind")}</span>
          <SegmentedControl
            className={styles.filterTabs}
            options={KIND_OPTIONS}
            value={kind}
            onChange={setKind}
            ariaLabel={t("JobsPage.filterKind")}
          />
        </div>
        <div className={styles.filterGroup}>
          <span className={styles.selectLabel}>{t("JobsPage.filterStatus")}</span>
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
        {loading ? (
          <LoadingState fullPage text={t("JobsPage.loading")} />
        ) : visible.length === 0 ? (
          <EmptyState />
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  {[t("JobsPage.colTask"), t("JobsPage.colKind"), t("JobsPage.colStatus"), t("JobsPage.colProgress"), t("JobsPage.colCreatedAt"), t("JobsPage.colUpdatedAt"), t("JobsPage.colApplicant")].map((col) => (
                    <th key={col} className={styles.th}>{col}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visible.map((j) => (
                  <tr
                    key={j.id}
                    className={styles.tr}
                    onClick={() => setFocusJobId(j.id)}
                  >
                    <td className={styles.td}>
                      <div className={styles.nameCell}>
                        <div className={styles.nameIcon}>
                          <MIcon name="task" size={18} />
                        </div>
                        <div>
                          <div className={styles.namePrimary}>{j.title ?? j.id}</div>
                          <div className={styles.nameSub}>{j.id}</div>
                        </div>
                      </div>
                    </td>
                    <td className={styles.td}>{KIND_LABELS[j.kind] ?? j.kind}</td>
                    <td className={styles.td}>
                      <StatusBadge status={j.status} />
                    </td>
                    <td className={styles.td}>
                      <Progress value={j.progress} />
                    </td>
                    <td className={styles.td}>{formatDateTime(j.created_at)}</td>
                    <td className={styles.td}>{formatDateTime(j.updated_at)}</td>
                    <td className={styles.td}>{j.user_email ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <JobDetailDialog jobId={focusJobId} onClose={() => setFocusJobId(null)} />
    </div>
  );
}
