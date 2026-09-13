import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./AuditPage.module.scss";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import SharedEmptyState from "../../../components/EmptyState/EmptyState";
import { useToast } from "../../../hooks/useToast";
import { downloadBlob } from "../../../services/api";
import { AuditLogsService } from "../../../services/auditLogs";
import PageHeader from "../../../components/PageHeader/PageHeader";
import { formatDateTime } from "../../../utils/formatDate";

const PAGE_SIZE = 50;
/** 搜尋框即時查詢的防抖間隔（ms）；下拉與日期改變則立即查詢 */
const SEARCH_DEBOUNCE = 300;

/** date input（yyyy-mm-dd）轉 ISO；end 補到當日 23:59:59 */
function toIso(dateStr, endOfDay = false) {
  if (!dateStr) return "";
  return new Date(`${dateStr}T${endOfDay ? "23:59:59" : "00:00:00"}`).toISOString();
}

/** 本地時區的今天（yyyy-mm-dd）；不用 toISOString 以免 UTC 跨日 */
export function todayDateStr(now = new Date()) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** yyyy-mm-dd 字串可直接用字典序比較；任一為空視為「範圍合法」 */
export function isDateRangeValid(startDate, endDate) {
  if (!startDate || !endDate) return true;
  return startDate <= endDate;
}

/** 稽核日誌只有過去的紀錄，起訖任一端落在今天以後就不合法 */
export function isDateInFuture(dateStr, today = todayDateStr()) {
  return Boolean(dateStr) && dateStr > today;
}

/**
 * 更新起訖日期並維持「起始 ≤ 結束」且不超過今天：
 * 選到今天以後 → 校正為今天；
 * 起始被改到結束之後 → 結束跟著移到同一天；結束被改到起始之前 → 起始跟著移到同一天。
 */
export function applyDateField(filters, name, value, today = todayDateStr()) {
  const clamped = isDateInFuture(value, today) ? today : value;
  const next = { ...filters, [name]: clamped };
  if (isDateRangeValid(next.startDate, next.endDate)) return next;
  if (name === "startDate") next.endDate = clamped;
  else next.startDate = clamped;
  return next;
}

function EmptyState({ hasFilter }) {
  const { t } = useTranslation("system");
  return (
    <SharedEmptyState
      icon={hasFilter ? "search_off" : "receipt_long"}
      title={hasFilter ? t("AuditPage.emptyNoResult") : t("AuditPage.emptyNone")}
    />
  );
}

export default function AuditPage() {
  const { t } = useTranslation("system");
  const toast = useToast();
  const [logs, setLogs] = useState([]);
  const [count, setCount] = useState(0);
  const [stats, setStats] = useState(null);
  const [actionOptions, setActionOptions] = useState([]);
  const [userOptions, setUserOptions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [hasLoaded, setHasLoaded] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [page, setPage] = useState(0);

  /** 篩選條件一改就即時查詢；搜尋框另有防抖，避免每個字元都打 API */
  const [filters, setFilters] = useState({
    search: "",
    action: "",
    userId: "",
    startDate: "",
    endDate: "",
  });
  const [debouncedSearch, setDebouncedSearch] = useState("");
  /** 最新一次查詢的序號：回應順序錯亂時只採用最後一次的結果 */
  const requestSeq = useRef(0);

  useEffect(() => {
    const next = filters.search.trim();
    const timer = setTimeout(() => {
      setDebouncedSearch((prev) => {
        if (prev !== next) setPage(0);
        return next;
      });
    }, SEARCH_DEBOUNCE);
    return () => clearTimeout(timer);
  }, [filters.search]);

  const queryParams = useMemo(() => ({
    skip: page * PAGE_SIZE,
    limit: PAGE_SIZE,
    search: debouncedSearch || undefined,
    action: filters.action || undefined,
    userId: filters.userId || undefined,
    startTime: toIso(filters.startDate) || undefined,
    endTime: toIso(filters.endDate, true) || undefined,
  }), [debouncedSearch, filters.action, filters.userId, filters.startDate, filters.endDate, page]);

  const fetchLogs = useCallback(async () => {
    const seq = ++requestSeq.current;
    setLoading(true);
    try {
      const res = await AuditLogsService.list(queryParams);
      if (seq !== requestSeq.current) return;
      setLogs(res?.data ?? []);
      setCount(res?.count ?? 0);
    } catch (err) {
      if (seq !== requestSeq.current) return;
      toast.error(err?.message ?? t("AuditPage.toastLoadFailed"));
    } finally {
      if (seq === requestSeq.current) {
        setLoading(false);
        setHasLoaded(true);
      }
    }
  }, [queryParams, toast, t]);

  useEffect(() => {
    fetchLogs();
  }, [fetchLogs]);

  useEffect(() => {
    AuditLogsService.stats({
      startTime: toIso(filters.startDate) || undefined,
      endTime: toIso(filters.endDate, true) || undefined,
    })
      .then(setStats)
      .catch(() => {});
  }, [filters.startDate, filters.endDate]);

  useEffect(() => {
    AuditLogsService.actions().then(setActionOptions).catch(() => {});
    AuditLogsService.users().then(setUserOptions).catch(() => {});
  }, []);

  const hasFilter = Boolean(
    filters.search.trim() || filters.action || filters.userId || filters.startDate || filters.endDate,
  );
  const totalPages = Math.max(Math.ceil(count / PAGE_SIZE), 1);
  /** 載入過之後的重新查詢：保留畫面、半透明，不整頁換成 loading 動畫 */
  const isInitialLoading = loading && !hasLoaded;

  function setField(name, value) {
    setFilters((prev) => ({ ...prev, [name]: value }));
    if (name !== "search") setPage(0);
  }

  function setDateField(name, value) {
    setFilters((prev) => applyDateField(prev, name, value));
    setPage(0);
  }

  function resetFilters() {
    setFilters({ search: "", action: "", userId: "", startDate: "", endDate: "" });
    setPage(0);
  }

  async function handleExport() {
    setExporting(true);
    try {
      const blob = await AuditLogsService.exportCsv({ ...queryParams, skip: 0, limit: 10000 });
      downloadBlob(blob, `audit-logs-${new Date().toISOString().slice(0, 10)}.csv`);
      toast.success(t("AuditPage.toastExported"));
    } catch (err) {
      toast.error(err?.message ?? t("AuditPage.toastExportFailed"));
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className={styles.page}>
      <PageHeader title={t("AuditPage.pageTitle")} subtitle={t("AuditPage.pageSubtitle")}>
        <button
          type="button"
          className={styles.btnSecondary}
          onClick={handleExport}
          disabled={exporting}
        >
          <MIcon name="download" size={16} />
          {exporting ? t("AuditPage.exporting") : t("AuditPage.exportCsv")}
        </button>
      </PageHeader>

      {stats && (
        <div className={styles.summaryGrid}>
          <div className={styles.summaryItem}>
            <span>{t("AuditPage.statTotal")}</span>
            <strong>{stats.total}</strong>
          </div>
          <div className={styles.summaryItem}>
            <span>{t("AuditPage.statDanger")}</span>
            <strong className={styles.summaryDanger}>{stats.danger}</strong>
          </div>
          <div className={styles.summaryItem}>
            <span>{t("AuditPage.statLoginFailed")}</span>
            <strong className={styles.summaryDanger}>{stats.login_failed}</strong>
          </div>
          <div className={styles.summaryItem}>
            <span>{t("AuditPage.statActiveUsers")}</span>
            <strong>{stats.active_users}</strong>
          </div>
        </div>
      )}

      <form className={styles.toolbar} onSubmit={(e) => e.preventDefault()}>
        <div className={styles.searchBox}>
          <MIcon name="search" size={16} />
          <input
            value={filters.search}
            onChange={(e) => setField("search", e.target.value)}
            placeholder={t("AuditPage.searchPlaceholder")}
          />
        </div>

        <select
          className={styles.filterSelect}
          value={filters.action}
          onChange={(e) => setField("action", e.target.value)}
        >
          <option value="">{t("AuditPage.allActions")}</option>
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
          <option value="">{t("AuditPage.allUsers")}</option>
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
          aria-label={t("AuditPage.startDate")}
          title={t("AuditPage.startDate")}
          onChange={(e) => setDateField("startDate", e.target.value)}
        />
        <input
          type="date"
          className={styles.filterSelect}
          value={filters.endDate}
          aria-label={t("AuditPage.endDate")}
          title={t("AuditPage.endDate")}
          onChange={(e) => setDateField("endDate", e.target.value)}
        />

        {hasFilter && (
          <button type="button" className={styles.btnSecondary} onClick={resetFilters}>
            <MIcon name="filter_alt_off" size={16} />
            {t("AuditPage.clear")}
          </button>
        )}
      </form>

      <div className={`${styles.content} ${loading && !isInitialLoading ? styles.refreshing : ""}`} aria-busy={loading}>
        {isInitialLoading ? (
          <LoadingState fullPage text={t("AuditPage.loading")} />
        ) : logs.length === 0 ? (
          <EmptyState hasFilter={hasFilter} />
        ) : (
          <>
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    {[t("AuditPage.colTime"), t("AuditPage.colUser"), t("AuditPage.colAction"), t("AuditPage.colContent"), "VMID", "IP"].map((col) => (
                      <th key={col} className={styles.th}>{col}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {logs.map((log) => (
                    <tr key={log.id} className={styles.tr}>
                      <td className={`${styles.td} ${styles.tdNowrap}`}>{formatDateTime(log.created_at)}</td>
                      <td className={styles.td}>
                        <div className={styles.userCell}>
                          <span>{log.user_full_name ?? (log.user_email ? "—" : t("AuditPage.systemUser"))}</span>
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
                {t("AuditPage.paginationInfo", { count, page: page + 1, totalPages })}
              </span>
              <div className={styles.paginationBtns}>
                <button
                  type="button"
                  className={styles.btnSecondary}
                  disabled={page === 0}
                  onClick={() => setPage((p) => Math.max(p - 1, 0))}
                >
                  <MIcon name="chevron_left" size={16} />
                  {t("AuditPage.prevPage")}
                </button>
                <button
                  type="button"
                  className={styles.btnSecondary}
                  disabled={page + 1 >= totalPages}
                  onClick={() => setPage((p) => p + 1)}
                >
                  {t("AuditPage.nextPage")}
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
