import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./JobsPage.module.scss";
import MIcon from "../../../components/MIcon";
import { JobsService } from "../../../services/jobs";
import { useToast } from "../../../hooks/useToast";
import useAutoRefresh from "../../../hooks/useAutoRefresh";

const COLUMNS = ["任務", "類型", "狀態", "進度", "建立時間", "更新時間", "申請人"];

const KIND_LABELS = {
  migration:     "遷移",
  script_deploy: "腳本部署",
  vm_request:    "VM 申請",
  spec_change:   "規格變更",
  deletion:      "刪除",
};

const STATUS_LABELS = {
  pending:   "等待中",
  running:   "執行中",
  completed: "已完成",
  failed:    "失敗",
  blocked:   "受阻",
  cancelled: "已取消",
};

const KIND_OPTIONS = [
  { value: "all", label: "全部類型" },
  ...Object.entries(KIND_LABELS).map(([value, label]) => ({ value, label })),
];

const STATUS_OPTIONS = [
  { value: "all",    label: "全部狀態" },
  { value: "active", label: "進行中" },
  ...Object.entries(STATUS_LABELS).map(([value, label]) => ({ value, label })),
];

function EmptyState() {
  return (
    <div className={styles.empty}>
      <div className={styles.emptyIcon}>
        <MIcon name="hourglass_empty" size={40} />
      </div>
      <h2 className={styles.emptyTitle}>沒有符合條件的任務</h2>
      <p className={styles.emptyDesc}>
        所有遷移、部署與規格變更等背景任務將顯示在這裡
      </p>
    </div>
  );
}

function StatusBadge({ status }) {
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

function fmtDate(iso) {
  return iso ? new Date(iso).toLocaleString("zh-TW") : "—";
}

export default function JobsPage() {
  const toast = useToast();
  const [jobs, setJobs] = useState([]);
  const [activeCount, setActiveCount] = useState(0);
  const [kind, setKind] = useState("all");
  const [status, setStatus] = useState("all");
  const [loading, setLoading] = useState(true);

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
      if (!silent) toast.error(e?.message ?? "載入背景任務失敗");
    } finally {
      if (!silent) setLoading(false);
    }
  }, [kind, status, toast]);

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
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>背景任務</h1>
          <p className={styles.pageSubtitle}>追蹤遷移、部署與資源配置等長時間執行的任務</p>
        </div>
      </div>

      <div className={styles.statRow}>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconBusy}`}>
            <MIcon name="autorenew" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>進行中</span>
            <span className={styles.statValue}>{stats.active}</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconOk}`}>
            <MIcon name="task_alt" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>已完成</span>
            <span className={styles.statValue}>{stats.completed}</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconErr}`}>
            <MIcon name="error_outline" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>失敗</span>
            <span className={styles.statValue}>{stats.failed}</span>
          </div>
        </div>
      </div>

      <div className={styles.toolbar}>
        <label className={styles.selectWrap}>
          <span className={styles.selectLabel}>類型</span>
          <select
            className={styles.select}
            value={kind}
            onChange={(e) => setKind(e.target.value)}
          >
            {KIND_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </label>
        <label className={styles.selectWrap}>
          <span className={styles.selectLabel}>狀態</span>
          <select
            className={styles.select}
            value={status}
            onChange={(e) => setStatus(e.target.value)}
          >
            {STATUS_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </label>
      </div>

      <div className={styles.content}>
        {visible.length === 0 ? (
          <EmptyState />
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  {COLUMNS.map((col) => (
                    <th key={col} className={styles.th}>{col}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visible.map((j) => (
                  <tr key={j.id} className={styles.tr}>
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
                    <td className={styles.td}>{fmtDate(j.created_at)}</td>
                    <td className={styles.td}>{fmtDate(j.updated_at)}</td>
                    <td className={styles.td}>{j.user_email ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
