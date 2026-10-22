import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { createColumnHelper, FlexRender, useTable } from "@tanstack/react-table";
import styles from "./AuditPage.module.scss";
import MIcon from "../../../components/MIcon";
import DataTable from "../../../components/DataTable/DataTable";
import { baseFeatures } from "../../../components/DataTable/features";
import LoadingState from "../../../components/LoadingState/LoadingState";
import SharedEmptyState from "../../../components/EmptyState/EmptyState";
import { useToast } from "../../../hooks/useToast";
import { useLoadFailureMessage, useQueryView } from "../../../hooks/useQueryLoadState";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { downloadBlob } from "../../../services/api";
import { AuditLogsService } from "../../../services/auditLogs";
import {
  useAuditActionsQuery,
  useAuditLogsQuery,
  useAuditStatsQuery,
  useAuditUsersQuery,
} from "../../../queries/auditLogs";
import PageHeader from "../../../components/PageHeader/PageHeader";
import Pagination from "../shared/Pagination";
import { formatDateTime } from "../../../utils/formatDate";

const PAGE_SIZE = 50;
/** 匯出一次最多幾筆；後端同樣有上限，兩邊要對齊 */
const EXPORT_LIMIT = 10000;
/** 搜尋框即時查詢的防抖間隔（ms）；下拉與日期改變則立即查詢 */
const SEARCH_DEBOUNCE = 300;

const EMPTY = Object.freeze([]);

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

function EmptyState({ hasFilter, onClear }) {
  const { t } = useTranslation("system");
  return (
    <SharedEmptyState
      icon={hasFilter ? "search_off" : "receipt_long"}
      title={hasFilter ? t("AuditPage.emptyNoResult") : t("AuditPage.emptyNone")}
      action={hasFilter ? <button type="button" className={styles.btnSecondary} onClick={onClear}><MIcon name="filter_alt_off" size={16} />{t("EmptyState.clearFilters", { ns: "common" })}</button> : undefined}
    />
  );
}

/** 表頭文字（header 是元件，可以直接用 hook） */
function ColTitle({ name }) {
  const { t } = useTranslation("system");
  return t(`AuditPage.${name}`);
}

function UserCell({ log }) {
  const { t } = useTranslation("system");
  return (
    <div className={styles.userCell}>
      <span>{log.user_full_name ?? (log.user_email ? "—" : t("AuditPage.systemUser"))}</span>
      <span className={styles.userEmail}>{log.user_email ?? ""}</span>
    </div>
  );
}

const columnHelper = createColumnHelper();

/* 欄位定義放模組層級：FlexRender 把函式型的 header／cell 當元件掛載，render 裡新建會整格重新掛載 */
const auditColumns = columnHelper.columns([
  columnHelper.accessor("created_at", {
    header: () => <ColTitle name="colTime" />,
    cell: ({ row }) => formatDateTime(row.original.created_at),
    meta: { thClassName: styles.th, tdClassName: `${styles.td} ${styles.tdNowrap}` },
  }),
  columnHelper.display({
    id: "user",
    header: () => <ColTitle name="colUser" />,
    cell: ({ row }) => <UserCell log={row.original} />,
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.accessor("action", {
    header: () => <ColTitle name="colAction" />,
    cell: ({ row }) => <span className={styles.actionBadge}>{row.original.action}</span>,
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.accessor("details", {
    header: () => <ColTitle name="colContent" />,
    cell: ({ row }) => row.original.details,
    /* 全文放在 td 的 title，滑過可看被截斷的內容 */
    meta: { thClassName: styles.th, tdClassName: `${styles.td} ${styles.tdDetails}`, tdTitle: (log) => log.details },
  }),
  columnHelper.accessor("vmid", {
    header: "VMID",
    cell: ({ row }) => row.original.vmid ?? "—",
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.accessor("ip_address", {
    header: "IP",
    cell: ({ row }) => row.original.ip_address ?? "—",
    meta: { thClassName: styles.th, tdClassName: `${styles.td} ${styles.tdNowrap}` },
  }),
]);

/**
 * 內容欄的 td 要帶逐列不同的 title，共用的 DataTableCells 只吃固定的 tdProps，
 * 所以這裡自己畫儲存格（DOM 與 DataTableCells 相同，只多了 title）。
 */
function renderAuditRow(row) {
  return (
    <tr key={row.id} className={styles.tr}>
      {row.getAllCells().map((cell) => {
        const meta = cell.column.columnDef.meta;
        return (
          <td key={cell.id} className={meta.tdClassName} title={meta.tdTitle?.(row.original)}>
            <FlexRender cell={cell} />
          </td>
        );
      })}
    </tr>
  );
}

export default function AuditPage() {
  const { t } = useTranslation("system");
  const toast = useToast();
  const confirm = useConfirm();
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
  /** 目前生效的搜尋字串：用來判斷是否真的變了，不在 setState updater 裡做副作用 */
  const debouncedSearchRef = useRef("");

  useEffect(() => {
    const next = filters.search.trim();
    const timer = setTimeout(() => {
      if (debouncedSearchRef.current === next) return;
      debouncedSearchRef.current = next;
      setDebouncedSearch(next);
      setPage(0);
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

  /* 清單與摘要走 TanStack Query：每組篩選一份快取，換條件時保留上一組畫面（半透明）直到新資料回來；
     較晚回來的舊條件結果只會寫進它自己那份快取，蓋不掉目前條件的畫面。
     原本只在條件改變時查詢，不背景輪詢，這裡照舊 */
  const logsQuery = useAuditLogsQuery(queryParams, { refetchInterval: false });
  const logs = logsQuery.data?.data ?? EMPTY;
  const count = logsQuery.data?.count ?? 0;
  /* 同一串失敗只提示一次、首載失敗後切回視窗不閃回整頁載入中（沒資料時 v5 每次重抓（切回視窗）都把 status 改回 pending、清掉 error） */
  const loadFailedMessage = useLoadFailureMessage(logsQuery, t("Error.generic", { ns: "common" }));
  const logsView = useQueryView(logsQuery);
  /* 伺服器分頁：頁碼由 page 決定查詢參數，表格只渲染當頁的列 */
  const table = useTable({
    features: baseFeatures,
    columns: auditColumns,
    data: logs,
    getRowId: (log) => String(log.id),
  });
  useEffect(() => {
    if (loadFailedMessage) toast.error(loadFailedMessage);
  }, [loadFailedMessage, toast]);

  /* 日期連續改動（手動打年份會逐位觸發）時，較晚回來的舊區間摘要不能蓋掉新區間的；
     摘要抓不到就把卡片收掉，不要留著上一個日期區間的數字誤導人 */
  const statsParams = useMemo(() => ({
    startTime: toIso(filters.startDate) || undefined,
    endTime: toIso(filters.endDate, true) || undefined,
  }), [filters.startDate, filters.endDate]);
  const statsQuery = useAuditStatsQuery(statsParams, { refetchInterval: false });
  const stats = statsQuery.isError ? null : statsQuery.data ?? null;

  /* 篩選選單；抓不到就留空選單（靜默） */
  const actionOptions = useAuditActionsQuery().data ?? EMPTY;
  const userOptions = useAuditUsersQuery().data ?? EMPTY;

  const hasFilter = Boolean(
    filters.search.trim() || filters.action || filters.userId || filters.startDate || filters.endDate,
  );
  const totalPages = Math.max(Math.ceil(count / PAGE_SIZE), 1);
  /** 載入過之後的重新查詢：保留畫面、半透明，不整頁換成 loading 動畫 */
  const isInitialLoading = logsView.loading;
  const loading = logsQuery.isFetching;

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
    if (exporting) return;
    /* 沒有任何篩選＝要把整個稽核庫拉下來，先問一次再送 */
    if (!hasFilter) {
      const ok = await confirm({
        title: t("AuditPage.exportAllConfirmTitle"),
        message: t("AuditPage.exportAllConfirmMessage", { limit: EXPORT_LIMIT }),
        confirmText: t("AuditPage.exportCsv"),
      });
      if (!ok) return;
    }
    setExporting(true);
    try {
      const blob = await AuditLogsService.exportCsv({ ...queryParams, limit: EXPORT_LIMIT });
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
      <PageHeader title={t("AuditPage.pageTitle")}>
        <button
          type="button"
          className={styles.btnSecondary}
          onClick={handleExport}
          disabled={exporting}
          data-guide="audit-export"
        >
          <MIcon name="download" size={16} />
          {exporting ? t("AuditPage.exporting") : t("AuditPage.exportCsv")}
        </button>
      </PageHeader>

      {stats && (
        <div className={styles.summaryGrid} data-guide="audit-summary">
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

      <form className={styles.toolbar} onSubmit={(e) => e.preventDefault()} data-guide="audit-filters">
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
          <EmptyState hasFilter={hasFilter} onClear={resetFilters} />
        ) : (
          <>
            <div className={styles.tableWrap} data-guide="audit-table">
              <DataTable table={table} className={styles.table} renderRow={renderAuditRow} />
            </div>

            <Pagination
              page={page}
              totalPages={totalPages}
              info={t("AuditPage.paginationInfo", { count, page: page + 1, totalPages })}
              prevLabel={t("AuditPage.prevPage")}
              nextLabel={t("AuditPage.nextPage")}
              onChange={setPage}
            />
          </>
        )}
      </div>
    </div>
  );
}
