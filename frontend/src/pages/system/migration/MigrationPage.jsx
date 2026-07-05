import { useCallback, useEffect, useState } from "react";
import styles from "./MigrationPage.module.scss";
import MIcon from "../../../components/MIcon";
import { useToast } from "../../../hooks/useToast";
import useAutoRefresh from "../../../hooks/useAutoRefresh";
import { MigrationJobsService } from "../../../services/migrationJobs";

const PAGE_SIZE = 50;

const STATUS_META = {
  pending:   { label: "排隊中",  badge: "badge_info" },
  running:   { label: "遷移中",  badge: "badge_success" },
  completed: { label: "已完成",  badge: "badge_muted" },
  failed:    { label: "失敗",    badge: "badge_failed" },
  blocked:   { label: "已阻擋",  badge: "badge_failed" },
  cancelled: { label: "已取消",  badge: "badge_muted" },
};

const STATUS_FILTERS = [
  { value: "", label: "全部" },
  { value: "pending", label: "排隊中" },
  { value: "running", label: "遷移中" },
  { value: "completed", label: "已完成" },
  { value: "failed", label: "失敗" },
  { value: "cancelled", label: "已取消" },
];

function formatTime(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString("zh-TW", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function EmptyState({ hasFilter }) {
  return (
    <div className={styles.empty}>
      <div className={styles.emptyIcon}>
        <MIcon name="move_down" size={40} />
      </div>
      <h2 className={styles.emptyTitle}>{hasFilter ? "沒有符合的任務" : "尚無遷移任務"}</h2>
      <p className={styles.emptyDesc}>
        {hasFilter ? "請切換其他狀態篩選。" : "目前沒有進行中或已完成的遷移作業"}
      </p>
    </div>
  );
}

export default function MigrationPage() {
  const toast = useToast();
  const [jobs, setJobs] = useState([]);
  const [count, setCount] = useState(0);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState("");
  const [page, setPage] = useState(0);
  const [actingId, setActingId] = useState(null);

  /** silent = true 時不觸發 loading 與錯誤提示，供背景自動刷新使用 */
  const fetchJobs = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const [jobsRes, statsRes] = await Promise.all([
        MigrationJobsService.list({
          status: statusFilter || undefined,
          skip: page * PAGE_SIZE,
          limit: PAGE_SIZE,
        }),
        MigrationJobsService.stats().catch(() => null),
      ]);
      setJobs(jobsRes?.data ?? []);
      setCount(jobsRes?.count ?? 0);
      if (statsRes) setStats(statsRes);
    } catch (err) {
      if (!silent) toast.error(err?.message ?? "載入遷移任務失敗");
    } finally {
      if (!silent) setLoading(false);
    }
  }, [statusFilter, page, toast]);

  useEffect(() => {
    fetchJobs();
  }, [fetchJobs]);
  useAutoRefresh(() => fetchJobs(true));

  /* 有進行中任務時每 5 秒自動更新 */
  useEffect(() => {
    const hasActive = jobs.some((j) => j.status === "pending" || j.status === "running");
    if (!hasActive) return undefined;
    const timer = setInterval(() => fetchJobs(true), 5000);
    return () => clearInterval(timer);
  }, [jobs, fetchJobs]);

  async function handleRetry(job) {
    setActingId(job.id);
    try {
      await MigrationJobsService.retry(job.id);
      toast.success("已重新排入佇列");
      fetchJobs();
    } catch (err) {
      toast.error(err?.message ?? "重試失敗");
    } finally {
      setActingId(null);
    }
  }

  async function handleCancel(job) {
    setActingId(job.id);
    try {
      await MigrationJobsService.cancel(job.id);
      toast.success("任務已取消");
      fetchJobs();
    } catch (err) {
      toast.error(err?.message ?? "取消失敗");
    } finally {
      setActingId(null);
    }
  }

  const totalPages = Math.max(Math.ceil(count / PAGE_SIZE), 1);

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>Migration Jobs</h1>
          <p className={styles.pageSubtitle}>追蹤虛擬機與容器的跨節點遷移進度與歷史紀錄</p>
        </div>
      </div>

      {stats && (
        <div className={styles.summaryGrid}>
          <div className={styles.summaryItem}>
            <span>總任務數</span>
            <strong>{stats.total_jobs}</strong>
          </div>
          <div className={styles.summaryItem}>
            <span>進行中</span>
            <strong>{(stats.by_status?.pending ?? 0) + (stats.by_status?.running ?? 0)}</strong>
          </div>
          <div className={styles.summaryItem}>
            <span>成功率</span>
            <strong>{Math.round((stats.success_rate ?? 0) * 100)}%</strong>
          </div>
          <div className={styles.summaryItem}>
            <span>平均耗時</span>
            <strong>{Math.round(stats.avg_duration_seconds ?? 0)}s</strong>
          </div>
        </div>
      )}

      <div className={styles.filterTabs}>
        {STATUS_FILTERS.map((f) => (
          <button
            key={f.value}
            type="button"
            className={statusFilter === f.value ? styles.filterTabActive : styles.filterTab}
            onClick={() => {
              setStatusFilter(f.value);
              setPage(0);
            }}
          >
            {f.label}
          </button>
        ))}
      </div>

      <div className={styles.content}>
        {loading && jobs.length === 0 ? (
          <div className={styles.loading}>載入遷移任務...</div>
        ) : jobs.length === 0 ? (
          <EmptyState hasFilter={Boolean(statusFilter)} />
        ) : (
          <>
            <div className={styles.list}>
              {jobs.map((job) => {
                const meta = STATUS_META[job.status] ?? { label: job.status, badge: "badge_muted" };
                return (
                  <div key={job.id} className={styles.row}>
                    <div className={styles.rowIcon}>
                      <MIcon name="move_down" size={20} />
                    </div>
                    <div className={styles.rowMain}>
                      <span className={styles.rowName}>
                        VMID {job.vmid ?? "—"}
                        {job.attempt_count > 1 ? `（第 ${job.attempt_count} 次嘗試）` : ""}
                      </span>
                      <span className={styles.rowMeta}>
                        {job.source_node ?? "?"} → {job.target_node} · 申請於 {formatTime(job.requested_at)}
                        {job.finished_at ? ` · 完成於 ${formatTime(job.finished_at)}` : ""}
                      </span>
                      {job.last_error && (
                        <span className={styles.rowError} title={job.last_error}>
                          {job.last_error}
                        </span>
                      )}
                    </div>
                    <span className={`${styles.badge} ${styles[meta.badge]}`}>{meta.label}</span>
                    <div className={styles.rowActions}>
                      {(job.status === "failed" || job.status === "blocked") && (
                        <button
                          type="button"
                          className={styles.actionBtn}
                          title="重試"
                          disabled={actingId === job.id}
                          onClick={() => handleRetry(job)}
                        >
                          <MIcon name="replay" size={16} />
                        </button>
                      )}
                      {job.status === "pending" && (
                        <button
                          type="button"
                          className={`${styles.actionBtn} ${styles.actionBtnDanger}`}
                          title="取消"
                          disabled={actingId === job.id}
                          onClick={() => handleCancel(job)}
                        >
                          <MIcon name="close" size={16} />
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>

            {totalPages > 1 && (
              <div className={styles.pagination}>
                <span className={styles.paginationInfo}>
                  共 {count} 筆 · 第 {page + 1} / {totalPages} 頁
                </span>
                <div className={styles.paginationBtns}>
                  <button
                    type="button"
                    className={styles.btnSecondary}
                    disabled={page === 0}
                    onClick={() => setPage((p) => Math.max(p - 1, 0))}
                  >
                    上一頁
                  </button>
                  <button
                    type="button"
                    className={styles.btnSecondary}
                    disabled={page + 1 >= totalPages}
                    onClick={() => setPage((p) => p + 1)}
                  >
                    下一頁
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
