import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./AuditPage.module.scss";
import MIcon from "../../../components/MIcon";
import { useToast } from "../../../hooks/useToast";
import { downloadBlob } from "../../../services/api";
import { AuditLogsService } from "../../../services/auditLogs";

const PAGE_SIZE = 50;

function formatTime(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString("zh-TW", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/** date input（yyyy-mm-dd）轉 ISO；end 補到當日 23:59:59 */
function toIso(dateStr, endOfDay = false) {
  if (!dateStr) return "";
  return new Date(`${dateStr}T${endOfDay ? "23:59:59" : "00:00:00"}`).toISOString();
}

function EmptyState({ hasFilter }) {
  return (
    <div className={styles.empty}>
      <div className={styles.emptyIcon}>
        <MIcon name={hasFilter ? "search_off" : "receipt_long"} size={40} />
      </div>
      <h2 className={styles.emptyTitle}>{hasFilter ? "找不到符合的紀錄" : "尚無操作紀錄"}</h2>
      <p className={styles.emptyDesc}>
        {hasFilter ? "請調整篩選條件後重試。" : "系統操作紀錄將會顯示在這裡"}
      </p>
    </div>
  );
}

export default function AuditPage() {
  const toast = useToast();
  const [logs, setLogs] = useState([]);
  const [count, setCount] = useState(0);
  const [stats, setStats] = useState(null);
  const [actionOptions, setActionOptions] = useState([]);
  const [userOptions, setUserOptions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [page, setPage] = useState(0);

  const [filters, setFilters] = useState({
    search: "",
    action: "",
    userId: "",
    startDate: "",
    endDate: "",
  });
  /** 送出查詢用的 filters（按「查詢」後才生效，避免每個字元都打 API） */
  const [applied, setApplied] = useState(filters);

  const queryParams = useMemo(() => ({
    skip: page * PAGE_SIZE,
    limit: PAGE_SIZE,
    search: applied.search.trim() || undefined,
    action: applied.action || undefined,
    userId: applied.userId || undefined,
    startTime: toIso(applied.startDate) || undefined,
    endTime: toIso(applied.endDate, true) || undefined,
  }), [applied, page]);

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    try {
      const res = await AuditLogsService.list(queryParams);
      setLogs(res?.data ?? []);
      setCount(res?.count ?? 0);
    } catch (err) {
      toast.error(err?.message ?? "載入稽核日誌失敗");
    } finally {
      setLoading(false);
    }
  }, [queryParams, toast]);

  useEffect(() => {
    fetchLogs();
  }, [fetchLogs]);

  useEffect(() => {
    AuditLogsService.stats({
      startTime: toIso(applied.startDate) || undefined,
      endTime: toIso(applied.endDate, true) || undefined,
    })
      .then(setStats)
      .catch(() => {});
  }, [applied.startDate, applied.endDate]);

  useEffect(() => {
    AuditLogsService.actions().then(setActionOptions).catch(() => {});
    AuditLogsService.users().then(setUserOptions).catch(() => {});
  }, []);

  const hasFilter = Boolean(
    applied.search.trim() || applied.action || applied.userId || applied.startDate || applied.endDate,
  );
  const totalPages = Math.max(Math.ceil(count / PAGE_SIZE), 1);

  function setField(name, value) {
    setFilters((prev) => ({ ...prev, [name]: value }));
  }

  function applyFilters(e) {
    e?.preventDefault();
    setPage(0);
    setApplied(filters);
  }

  function resetFilters() {
    const empty = { search: "", action: "", userId: "", startDate: "", endDate: "" };
    setFilters(empty);
    setApplied(empty);
    setPage(0);
  }

  async function handleExport() {
    setExporting(true);
    try {
      const blob = await AuditLogsService.exportCsv({ ...queryParams, skip: 0, limit: 10000 });
      downloadBlob(blob, `audit-logs-${new Date().toISOString().slice(0, 10)}.csv`);
      toast.success("已匯出 CSV");
    } catch (err) {
      toast.error(err?.message ?? "匯出失敗");
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>稽核日誌</h1>
          <p className={styles.pageSubtitle}>查看所有系統操作記錄</p>
        </div>
        <button
          type="button"
          className={styles.btnSecondary}
          onClick={handleExport}
          disabled={exporting}
        >
          <MIcon name="download" size={16} />
          {exporting ? "匯出中..." : "匯出 CSV"}
        </button>
      </div>

      {stats && (
        <div className={styles.summaryGrid}>
          <div className={styles.summaryItem}>
            <span>總紀錄數</span>
            <strong>{stats.total}</strong>
          </div>
          <div className={styles.summaryItem}>
            <span>危險操作</span>
            <strong className={styles.summaryDanger}>{stats.danger}</strong>
          </div>
          <div className={styles.summaryItem}>
            <span>登入失敗</span>
            <strong className={styles.summaryDanger}>{stats.login_failed}</strong>
          </div>
          <div className={styles.summaryItem}>
            <span>活躍使用者</span>
            <strong>{stats.active_users}</strong>
          </div>
        </div>
      )}

      <form className={styles.toolbar} onSubmit={applyFilters}>
        <div className={styles.searchBox}>
          <MIcon name="search" size={16} />
          <input
            value={filters.search}
            onChange={(e) => setField("search", e.target.value)}
            placeholder="搜尋操作內容、IP..."
          />
        </div>

        <select
          className={styles.filterSelect}
          value={filters.action}
          onChange={(e) => setField("action", e.target.value)}
        >
          <option value="">全部操作</option>
          {actionOptions.map((a) => (
            <option key={a.value} value={a.value}>
              {a.category ? `[${a.category}] ` : ""}{a.value}
            </option>
          ))}
        </select>

        <select
          className={styles.filterSelect}
          value={filters.userId}
          onChange={(e) => setField("userId", e.target.value)}
        >
          <option value="">全部使用者</option>
          {userOptions.map((u) => (
            <option key={u.id} value={u.id}>
              {u.full_name ? `${u.full_name}（${u.email}）` : u.email}
            </option>
          ))}
        </select>

        <input
          type="date"
          className={styles.filterSelect}
          value={filters.startDate}
          onChange={(e) => setField("startDate", e.target.value)}
        />
        <input
          type="date"
          className={styles.filterSelect}
          value={filters.endDate}
          onChange={(e) => setField("endDate", e.target.value)}
        />

        <button type="submit" className={styles.btnSecondary}>
          <MIcon name="filter_alt" size={16} />
          查詢
        </button>
        {hasFilter && (
          <button type="button" className={styles.btnSecondary} onClick={resetFilters}>
            <MIcon name="filter_alt_off" size={16} />
            清除
          </button>
        )}
      </form>

      <div className={styles.content}>
        {loading ? (
          <div className={styles.loading}>載入稽核日誌...</div>
        ) : logs.length === 0 ? (
          <EmptyState hasFilter={hasFilter} />
        ) : (
          <>
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    {["時間", "使用者", "操作", "內容", "VMID", "IP"].map((col) => (
                      <th key={col} className={styles.th}>{col}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {logs.map((log) => (
                    <tr key={log.id} className={styles.tr}>
                      <td className={`${styles.td} ${styles.tdNowrap}`}>{formatTime(log.created_at)}</td>
                      <td className={styles.td}>
                        <div className={styles.userCell}>
                          <span>{log.user_full_name ?? "—"}</span>
                          <span className={styles.userEmail}>{log.user_email ?? ""}</span>
                        </div>
                      </td>
                      <td className={styles.td}>
                        <span className={styles.actionBadge}>{log.action}</span>
                      </td>
                      <td className={`${styles.td} ${styles.tdDetails}`} title={log.details}>
                        {log.details}
                      </td>
                      <td className={styles.td}>{log.vmid ?? "—"}</td>
                      <td className={`${styles.td} ${styles.tdNowrap}`}>{log.ip_address ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

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
                  <MIcon name="chevron_left" size={16} />
                  上一頁
                </button>
                <button
                  type="button"
                  className={styles.btnSecondary}
                  disabled={page + 1 >= totalPages}
                  onClick={() => setPage((p) => p + 1)}
                >
                  下一頁
                  <MIcon name="chevron_right" size={16} />
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
