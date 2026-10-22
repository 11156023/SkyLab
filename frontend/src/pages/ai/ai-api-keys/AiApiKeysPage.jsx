import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { createColumnHelper, useTable } from "@tanstack/react-table";
import styles from "./AiApiKeysPage.module.scss";
import MIcon from "../../../components/MIcon";
import DataTable from "../../../components/DataTable/DataTable";
import { baseFeatures } from "../../../components/DataTable/features";
import Modal from "../../../components/Modal/Modal";
import LoadingState from "../../../components/LoadingState/LoadingState";
import SharedEmptyState from "../../../components/EmptyState/EmptyState";
import ErrorState from "../../../components/ErrorState/ErrorState";
import { AiApiService } from "../../../services/aiApi";
import { useAiApiCacheUpdater, useAllAiCredentialsQuery } from "../../../queries/aiApi";
import { useToast } from "../../../hooks/useToast";
import { useLoadError } from "../../../hooks/useQueryLoadState";
import useDialogPresence from "../../../hooks/useDialogPresence";
import PageHeader from "../../../components/PageHeader/PageHeader";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import { formatDate, formatDateTime } from "../../../utils/formatDate";

const PAGE_SIZE = 50;
const EMPTY = Object.freeze([]);
const ROLE_OPTIONS = ["student", "teacher", "admin"];
const CREATED_OPTIONS = ["all", "7d", "30d"];

function createdAfterFor(range) {
  if (range === "all") return undefined;
  const days = range === "7d" ? 7 : 30;
  return new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString();
}

export function buildCredentialListParams({
  status,
  query,
  roles = [],
  createdRange = "all",
  page = 0,
  limit = PAGE_SIZE,
} = {}) {
  return {
    status: status && status !== "all" ? status : undefined,
    query: query?.trim() || undefined,
    user_role: roles,
    created_after: createdAfterFor(createdRange),
    skip: page * limit,
    limit,
  };
}

function roleLabel(role, t) {
  const key = role === "admin" ? "roleAdmin" : role === "teacher" ? "roleTeacher" : role === "student" ? "roleStudent" : null;
  return key ? t(`AiApiKeysPage.${key}`) : "—";
}

function StatusBadge({ item }) {
  const { t } = useTranslation("ai");
  const isActive = item.status === "active";
  return (
    <span className={`${styles.badge} ${isActive ? styles.badge_active : styles.badge_inactive}`}>
      <span className={styles.dot} />
      {isActive
        ? t("AiApiKeysPage.statusActive")
        : t(item.inactive_reason === "deleted" ? "AiApiKeysPage.statusDeleted" : "AiApiKeysPage.statusInactive")}
    </span>
  );
}

/* 憑證表格的欄位（TanStack Table）。欄位定義放模組層級：v9 的 FlexRender 把函式型的 header／cell
   當元件掛載，render 裡新建會讓整格重新掛載。表頭文字取 meta.headerKey 的翻譯；撤銷經 table meta 交回頁面。
   資料沒變時表格的列不會重繪，切換語言只會重繪有用 useTranslation 的元件，
   所以依語系格式化日期的格子（formatDate 讀 i18n.language）自己訂閱語系 */
const columnHelper = createColumnHelper();

function HeaderLabel({ column }) {
  const { t } = useTranslation("ai");
  return t(column.columnDef.meta.headerKey);
}

function NameCell({ row }) {
  const item = row.original;
  return (
    <div className={styles.nameCell}>
      <div className={styles.rowIcon}>
        <MIcon name="vpn_key" size={20} />
      </div>
      <div className={styles.rowMain}>
        <span className={styles.rowName} title={item.api_key_name || undefined}>{item.api_key_name || "—"}</span>
        <span className={styles.rowMeta} title={item.user_email || undefined}>{item.user_email || "—"}</span>
      </div>
    </div>
  );
}

function TextCell({ getValue }) {
  const value = getValue();
  return <span className={styles.cellText} title={value || undefined}>{value || "—"}</span>;
}

function ActivityCell({ row }) {
  const { t } = useTranslation("ai");
  const lastUsedAt = row.original.last_used_at;
  return lastUsedAt ? formatDateTime(lastUsedAt) : <span className={styles.cellMuted}>{t("AiApiKeysPage.detailNeverUsed")}</span>;
}

function CreatedAtCell({ getValue }) {
  useTranslation("ai");
  return formatDate(getValue());
}

function RevokeCell({ row, table }) {
  const { t } = useTranslation("ai");
  const item = row.original;
  if (item.status !== "active") return "—";
  return (
    <button
      type="button"
      className={styles.rowRevokeBtn}
      data-guide="ai-keys-admin-revoke"
      onClick={() => table.options.meta.onRevoke(item)}
    >
      <MIcon name="block" size={16} />
      {t("AiApiKeysPage.revokeAction")}
    </button>
  );
}

function columnMeta(headerKey, extra) {
  return { headerKey, thClassName: styles.th, tdClassName: styles.td, ...extra };
}

const credentialColumns = columnHelper.columns([
  columnHelper.display({ id: "name", header: HeaderLabel, cell: NameCell, meta: columnMeta("AiApiKeysPage.colName") }),
  columnHelper.accessor("user_full_name", { header: HeaderLabel, cell: TextCell, meta: columnMeta("AiApiKeysPage.detailOwner") }),
  columnHelper.accessor("request_purpose", { header: HeaderLabel, cell: TextCell, meta: columnMeta("AiApiKeysPage.detailPurpose") }),
  columnHelper.display({
    id: "activity",
    header: HeaderLabel,
    cell: ActivityCell,
    meta: columnMeta("AiApiKeysPage.detailActivity", { tdProps: { "data-guide": "ai-keys-admin-activity" } }),
  }),
  columnHelper.display({ id: "status", header: HeaderLabel, cell: ({ row }) => <StatusBadge item={row.original} />, meta: columnMeta("AiApiKeysPage.colStatus") }),
  columnHelper.accessor("created_at", { header: HeaderLabel, cell: CreatedAtCell, meta: columnMeta("AiApiKeysPage.colCreatedAt") }),
  columnHelper.display({ id: "actions", header: HeaderLabel, cell: RevokeCell, meta: columnMeta("AiApiKeysPage.colActions") }),
]);

const credentialRowClassName = () => styles.tr;

function EmptyState({ hasFilters, onClear }) {
  const { t } = useTranslation("ai");
  return (
    <SharedEmptyState
      icon="vpn_key"
      title={t(hasFilters ? "AiApiKeysPage.emptyFilteredTitle" : "AiApiKeysPage.emptyTitle")}
      action={hasFilters ? (
        <button type="button" className={styles.btnSecondary} onClick={onClear}>
          <MIcon name="filter_alt_off" size={16} />{t("EmptyState.clearFilters", { ns: "common" })}
        </button>
      ) : undefined}
    />
  );
}

function FilterPopover({ roles, createdRange, onToggleRole, onCreatedRangeChange, onClear }) {
  const { t } = useTranslation("ai");
  return (
    <div className={styles.filterPopover} role="dialog" aria-label={t("AiApiKeysPage.filterHeading")}>
      <div className={styles.filterPopoverHeader}>
        <strong>{t("AiApiKeysPage.filterHeading")}</strong>
        <span className={styles.filterPopoverHint}>{t("AiApiKeysPage.filterHint")}</span>
      </div>

      <fieldset className={styles.filterGroup}>
        <legend>{t("AiApiKeysPage.filterRole")}</legend>
        {ROLE_OPTIONS.map((role) => (
          <label key={role} className={styles.checkOption}>
            <input type="checkbox" checked={roles.includes(role)} onChange={() => onToggleRole(role)} />
            <span>{roleLabel(role, t)}</span>
          </label>
        ))}
      </fieldset>

      <fieldset className={styles.filterGroup}>
        <legend>{t("AiApiKeysPage.filterCreated")}</legend>
        {CREATED_OPTIONS.map((range) => (
          <label key={range} className={styles.checkOption}>
            <input
              type="radio"
              name="ai-api-key-created-range"
              value={range}
              checked={createdRange === range}
              onChange={() => onCreatedRangeChange(range)}
            />
            <span>{t(`AiApiKeysPage.created${range === "all" ? "All" : range === "7d" ? "7d" : "30d"}`)}</span>
          </label>
        ))}
      </fieldset>

      <button type="button" className={styles.clearFilters} onClick={onClear}>{t("AiApiKeysPage.clearFilters")}</button>
    </div>
  );
}

function RevokeDialog({ item, closing = false, onClose, onDone }) {
  const { t } = useTranslation("ai");
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  if (!item) return null;

  const handleRevoke = async () => {
    setBusy(true);
    try {
      await AiApiService.deleteCredential(item.id);
      toast.success(t("AiApiKeysPage.revokeSuccess"));
      onClose();
      onDone();
    } catch (error) {
      toast.error(error?.message ?? t("AiApiKeysPage.revokeError"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      role="alertdialog"
      closing={closing}
      onClose={onClose}
      busy={busy}
      icon={<span className={styles.revokeIcon}><MIcon name="warning" size={20} /></span>}
      title={t("AiApiKeysPage.revokeConfirmTitle")}
      description={t("AiApiKeysPage.revokeConfirmDesc", { name: item.api_key_name })}
      actions={
        <>
          <button type="button" className={styles.btnSecondary} onClick={onClose} disabled={busy}>{t("AiApiKeysPage.cancel")}</button>
          <button type="button" className={styles.btnDanger} onClick={handleRevoke} disabled={busy}>{busy ? t("AiApiKeysPage.revoking") : t("AiApiKeysPage.confirmRevoke")}</button>
        </>
      }
    />
  );
}

export default function AiApiKeysPage() {
  const { t } = useTranslation("ai");
  const toast = useToast();
  const filterAreaRef = useRef(null);
  const [statusFilter, setStatusFilter] = useState("active");
  const [searchInput, setSearchInput] = useState("");
  const [query, setQuery] = useState("");
  const [roleFilters, setRoleFilters] = useState([]);
  const [createdRange, setCreatedRange] = useState("all");
  const [filterOpen, setFilterOpen] = useState(false);
  const [deletingItem, setDeletingItem] = useState(null);
  const [page, setPage] = useState(0);
  const deleteDialog = useDialogPresence(deletingItem);

  useEffect(() => {
    const timer = setTimeout(() => {
      setQuery(searchInput);
      setPage(0);
    }, 220);
    return () => clearTimeout(timer);
  }, [searchInput]);

  useEffect(() => {
    if (!filterOpen) return undefined;
    const closeOnOutside = (event) => {
      if (!filterAreaRef.current?.contains(event.target)) setFilterOpen(false);
    };
    const closeOnEscape = (event) => {
      if (event.key === "Escape") setFilterOpen(false);
    };
    document.addEventListener("mousedown", closeOnOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeOnOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [filterOpen]);

  /* 清單走 TanStack Query，以查詢條件為 key：快速切換分頁／篩選時舊條件較晚回來的回應只會進舊 key，
     不會蓋掉目前的清單；換條件時先留著上一份（keepPreviousData），不閃回載入畫面。
     背景 30 秒刷新、分頁隱藏時暫停。條件在變動時才組一次：建立時間篩選的起點固定在那一刻，
     不能每次 render 都重算（key 會一直換）。 */
  const listParams = useMemo(() => buildCredentialListParams({
    status: statusFilter,
    query,
    roles: roleFilters,
    createdRange,
    page,
  }), [statusFilter, query, roleFilters, createdRange, page]);
  const credentialsQuery = useAllAiCredentialsQuery(listParams);
  const aiCache = useAiApiCacheUpdater();
  const res = credentialsQuery.data;
  /* 錯誤畫面在背景重試期間不閃回載入中，按重試才回到載入中（見 useLoadError）。
     換條件時沿用上一份清單照常顯示；但這組條件失敗後，沒有資料時每次重抓 TanStack 都會把上一組條件的
     清單放回來當佔位資料，所以判斷錯誤時佔位資料不算資料，錯誤畫面才不會閃成上一組條件的清單 */
  const { error: loadError, retry: retryCredentials } = useLoadError(credentialsQuery);
  const loading = res === undefined && !loadError && (credentialsQuery.isPending || credentialsQuery.isFetching);
  const rows = res?.data ?? EMPTY;
  /* 伺服器分頁：頁碼由頁面管（決定查詢參數），表格只渲染當頁的列 */
  const table = useTable({
    features: baseFeatures,
    columns: credentialColumns,
    data: rows,
    getRowId: (item) => String(item.id),
    meta: { onRevoke: setDeletingItem },
  });
  const total = res?.count ?? 0;
  const allCount = res?.total_count ?? res?.count ?? 0;
  const activeCount = res?.active_count ?? 0;
  const inactiveCount = res?.inactive_count ?? 0;

  /* 這組條件一筆資料都還沒拿到就失敗才提醒（與原本的前景載入相同）；
     背景刷新失敗沿用畫面上的資料、不跳 toast。以訊息字串為依賴，同一個錯誤重試失敗不重複跳
     （錯誤取自 useLoadError：背景重試期間不會先清空再變回來，才不會每輪重跳） */
  const loadErrorMessage = loadError
    ? loadError.message ?? t("AiApiKeysPage.loadError")
    : null;
  useEffect(() => {
    if (loadErrorMessage) toast.error(loadErrorMessage);
  }, [loadErrorMessage, toast]);

  /* 撤銷後在背景重抓（清單留在畫面上），重抓失敗照原本提醒 */
  function refreshAfterRevoke() {
    aiCache.invalidateAllCredentials({ throwOnError: true })
      .catch((error) => toast.error(error?.message ?? t("AiApiKeysPage.loadError")));
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  useEffect(() => {
    if (page >= totalPages && page > 0) setPage(totalPages - 1);
  }, [page, totalPages]);

  const activeFilterCount = roleFilters.length + (createdRange === "all" ? 0 : 1);
  const hasFilters = Boolean(statusFilter !== "all" || query.trim() || roleFilters.length || createdRange !== "all");
  const tabs = useMemo(() => [
    { key: "active", label: t("AiApiKeysPage.tabActive"), count: activeCount },
    { key: "inactive", label: t("AiApiKeysPage.tabInactive"), count: inactiveCount },
    { key: "all", label: t("AiApiKeysPage.tabAll"), count: allCount },
  ], [activeCount, allCount, inactiveCount, t]);

  function toggleRole(role) {
    setRoleFilters((current) => current.includes(role) ? current.filter((item) => item !== role) : [...current, role]);
    setPage(0);
  }

  function clearFilters() {
    setRoleFilters([]);
    setCreatedRange("all");
    setFilterOpen(false);
    setPage(0);
  }

  return (
    <div className={styles.page}>
      <PageHeader title={t("AiApiKeysPage.pageTitle")} />

      <div className={styles.controlsRow}>
        <div className={styles.segmentGroup} data-guide="ai-keys-admin-status">
          <SegmentedControl
            className={styles.statusTabs}
            options={tabs.map(({ key, label, count }) => ({ value: key, label, badge: count }))}
            value={statusFilter}
            onChange={(value) => { setStatusFilter(value); setPage(0); }}
            ariaLabel={t("AiApiKeysPage.statusTabsLabel")}
          />
          <div ref={filterAreaRef} className={styles.filterArea} data-guide="ai-keys-admin-filter">
            <button type="button" className={`${styles.filterButton} ${activeFilterCount ? styles.filterButtonActive : ""}`} aria-haspopup="dialog" aria-expanded={filterOpen} onClick={() => setFilterOpen((current) => !current)}>
              <MIcon name="filter_alt" size={16} />{activeFilterCount ? t("AiApiKeysPage.filterActiveCount", { count: activeFilterCount }) : t("AiApiKeysPage.filterButton")}
            </button>
            {filterOpen && <FilterPopover roles={roleFilters} createdRange={createdRange} onToggleRole={toggleRole} onCreatedRangeChange={(value) => { setCreatedRange(value); setPage(0); }} onClear={clearFilters} />}
          </div>
        </div>

        <div className={styles.searchBox} data-guide="ai-keys-admin-search">
          <MIcon name="search" size={17} />
          <input type="search" value={searchInput} onChange={(event) => setSearchInput(event.target.value)} placeholder={t("AiApiKeysPage.searchPlaceholder")} aria-label={t("AiApiKeysPage.searchPlaceholder")} />
          {searchInput && <button type="button" className={styles.clearSearch} onClick={() => setSearchInput("")} aria-label={t("AiApiKeysPage.clearSearch")}><MIcon name="close" size={15} /></button>}
        </div>
      </div>

      <div className={styles.content}>
        {loading ? <LoadingState fullPage /> : loadError ? <ErrorState onRetry={() => retryCredentials()} /> : (
          rows.length === 0 ? <EmptyState hasFilters={hasFilters} onClear={() => {
            setStatusFilter("all");
            setSearchInput("");
            setQuery("");
            clearFilters();
          }} /> : (
            <>
              <div className={styles.tableWrap} data-guide="ai-keys-admin-table">
                <DataTable table={table} className={styles.table} rowClassName={credentialRowClassName} />
              </div>

              {totalPages > 1 && <div className={styles.pagination}><span className={styles.paginationInfo}>{t("AiApiKeysPage.paginationInfo", { page: page + 1, totalPages, total })}</span><div className={styles.paginationBtns}><button type="button" className={styles.btnSecondary} disabled={page === 0} onClick={() => setPage((current) => Math.max(0, current - 1))}>{t("AiApiKeysPage.prevPage")}</button><button type="button" className={styles.btnSecondary} disabled={page + 1 >= totalPages} onClick={() => setPage((current) => current + 1)}>{t("AiApiKeysPage.nextPage")}</button></div></div>}
            </>
          )
        )}
      </div>

      {deleteDialog.open && <RevokeDialog item={deleteDialog.item} closing={deleteDialog.closing} onClose={() => setDeletingItem(null)} onDone={refreshAfterRevoke} />}
    </div>
  );
}
