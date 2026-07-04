import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./BatchReviewPage.module.scss";
import MIcon from "../../../components/MIcon";
import { BatchProvisionService } from "../../../services/batchProvision";
import { useToast } from "../../../hooks/useToast";

const STATUS_LABELS = {
  pending_review: "待審核",
  approved:       "已核准",
  rejected:       "已駁回",
  cancelled:      "已取消",
  pending:        "等待中",
  running:        "建立中",
  completed:      "已完成",
  failed:         "失敗",
};

function fmtTime(iso) {
  return iso ? new Date(iso).toLocaleString("zh-TW") : "—";
}

const STATUS_OPTIONS = [
  { value: "all", label: "全部狀態" },
  ...Object.entries(STATUS_LABELS).map(([value, label]) => ({ value, label })),
];

function EmptyState() {
  return (
    <div className={styles.empty}>
      <div className={styles.emptyIcon}>
        <MIcon name="library_add_check" size={40} />
      </div>
      <h2 className={styles.emptyTitle}>沒有待審核的批次申請</h2>
      <p className={styles.emptyDesc}>
        教師或助教提交的批量 VM 建立申請會顯示在這裡
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

function ProgressInline({ done, failed, total }) {
  const d = total ? (done / total) * 100 : 0;
  const f = total ? (failed / total) * 100 : 0;
  return (
    <div className={styles.progressInline}>
      <div className={styles.progressBar}>
        <div className={styles.progressApproved} style={{ width: `${d}%` }} />
        <div className={styles.progressRejected} style={{ width: `${f}%` }} />
      </div>
      <span className={styles.progressLabel}>
        {done}/{total}
      </span>
    </div>
  );
}

const COLUMNS = ["批次名稱", "申請人", "課程", "VM 數量", "進度", "狀態", "提交時間", "動作"];

export default function BatchReviewPage() {
  const toast = useToast();
  const [batches, setBatches] = useState([]);
  const [status, setStatus] = useState("all");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  /** jobId → "loading" | [start, end][]，點「週期」chip 時才載入 */
  const [previews, setPreviews] = useState({});

  const togglePreview = async (jobId) => {
    if (previews[jobId]) {
      setPreviews((p) => { const n = { ...p }; delete n[jobId]; return n; });
      return;
    }
    setPreviews((p) => ({ ...p, [jobId]: "loading" }));
    try {
      const res = await BatchProvisionService.getRecurrencePreview(jobId);
      setPreviews((p) => ({ ...p, [jobId]: res?.windows ?? [] }));
    } catch (e) {
      setPreviews((p) => { const n = { ...p }; delete n[jobId]; return n; });
      toast.error(e?.message ?? "載入週期預覽失敗");
    }
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await BatchProvisionService.listPending();
      setBatches(Array.isArray(res) ? res : []);
    } catch (e) {
      toast.error(e?.message ?? "載入批量申請失敗");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);

  const review = async (jobId, decision) => {
    if (!window.confirm(decision === "approved" ? "確定核准此批次?" : "確定駁回此批次?")) return;
    try {
      await BatchProvisionService.review(jobId, { decision });
      toast.success(decision === "approved" ? "已核准" : "已駁回");
      load();
    } catch (e) {
      toast.error(e?.message ?? "操作失敗");
    }
  };

  const stats = useMemo(() => {
    const pending = batches.filter((b) => b.status === "pending_review").length;
    const inProgress = batches.filter((b) =>
      ["approved", "pending", "running"].includes(b.status),
    ).length;
    const totalVms = batches.reduce((s, b) => s + (b.total ?? 0), 0);
    return { pending, inProgress, totalVms };
  }, [batches]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return batches.filter((b) => {
      if (status !== "all" && b.status !== status) return false;
      if (!q) return true;
      return (
        (b.hostname_prefix ?? "").toLowerCase().includes(q) ||
        (b.initiated_by_email ?? "").toLowerCase().includes(q) ||
        (b.initiated_by_name ?? "").toLowerCase().includes(q) ||
        (b.group_name ?? "").toLowerCase().includes(q)
      );
    });
  }, [batches, status, query]);

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>批量建立審核</h1>
          <p className={styles.pageSubtitle}>審核教師提交的批次 VM 配置申請</p>
        </div>
        <div className={styles.pageActions}>
          <button type="button" className={styles.btnSecondary} onClick={load} disabled={loading}>
            <MIcon name="sync" size={16} />
            {loading ? "載入中…" : "重新整理"}
          </button>
        </div>
      </div>

      <div className={styles.statRow}>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconWarn}`}>
            <MIcon name="pending_actions" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>待審核批次</span>
            <span className={styles.statValue}>{stats.pending}</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconBusy}`}>
            <MIcon name="hourglass_top" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>建立中</span>
            <span className={styles.statValue}>{stats.inProgress}</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={styles.statIcon}>
            <MIcon name="dns" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>累計 VM 數量</span>
            <span className={styles.statValue}>{stats.totalVms}</span>
          </div>
        </div>
      </div>

      <div className={styles.toolbar}>
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
        <div className={styles.search}>
          <MIcon name="search" size={16} />
          <input
            type="text"
            className={styles.searchInput}
            placeholder="搜尋批次、申請人或課程"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </div>

      <div className={styles.content}>
        {visible.length === 0 ? (
          <EmptyState />
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  {COLUMNS.map((c) => (
                    <th key={c} className={styles.th}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visible.map((b) => {
                  const canReview = b.status === "pending_review";
                  return (
                    <tr key={b.id} className={styles.tr}>
                      <td className={styles.td}>
                        <div className={styles.nameCell}>
                          <div className={styles.nameIcon}>
                            <MIcon name="library_add" size={18} />
                          </div>
                          <div>
                            <div className={styles.namePrimary}>{b.hostname_prefix}</div>
                            <div className={styles.nameSub}>{b.resource_type?.toUpperCase()}</div>
                            {b.recurrence_rule && (
                              <>
                                <button
                                  type="button"
                                  className={styles.recurChip}
                                  title="點擊查看未來開機時段"
                                  onClick={() => togglePreview(b.id)}
                                >
                                  <MIcon name="update" size={12} />
                                  週期排程
                                </button>
                                {Array.isArray(previews[b.id]) && (
                                  <ul className={styles.recurWindows}>
                                    {previews[b.id].length === 0 && <li>沒有排定的時段</li>}
                                    {previews[b.id].map(([start, end]) => (
                                      <li key={start}>{fmtTime(start)} ～ {fmtTime(end)}</li>
                                    ))}
                                  </ul>
                                )}
                                {previews[b.id] === "loading" && (
                                  <span className={styles.recurLoading}>載入中…</span>
                                )}
                              </>
                            )}
                          </div>
                        </div>
                      </td>
                      <td className={styles.td}>{b.initiated_by_email ?? b.initiated_by_name ?? "—"}</td>
                      <td className={styles.td}>{b.group_name ?? "—"}</td>
                      <td className={styles.td}>{b.total}</td>
                      <td className={styles.td}>
                        <ProgressInline
                          done={b.done ?? 0}
                          failed={b.failed_count ?? 0}
                          total={b.total ?? 0}
                        />
                      </td>
                      <td className={styles.td}>
                        <StatusBadge status={b.status} />
                      </td>
                      <td className={styles.td}>{fmtTime(b.created_at)}</td>
                      <td className={styles.td}>
                        <div className={styles.actions}>
                          <button
                            type="button"
                            className={`${styles.actionBtn} ${styles.actionBtnOk}`}
                            title="核准"
                            disabled={!canReview}
                            onClick={() => review(b.id, "approved")}
                          >
                            <MIcon name="check" size={16} />
                          </button>
                          <button
                            type="button"
                            className={`${styles.actionBtn} ${styles.actionBtnDanger}`}
                            title="駁回"
                            disabled={!canReview}
                            onClick={() => review(b.id, "rejected")}
                          >
                            <MIcon name="close" size={16} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
