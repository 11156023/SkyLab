import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./AiApiKeysPage.module.scss";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import SharedEmptyState from "../../../components/EmptyState/EmptyState";
import { AiApiService } from "../../../services/aiApi";
import { useToast } from "../../../hooks/useToast";
import useAutoRefresh from "../../../hooks/useAutoRefresh";
import useDialogPresence from "../../../hooks/useDialogPresence";
import PageHeader from "../../../components/PageHeader/PageHeader";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import { formatDate, formatDateTime } from "../../../utils/formatDate";

const PAGE_SIZE = 50;
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
      {isActive ? t("AiApiKeysPage.statusActive") : t("AiApiKeysPage.statusInactive")}
    </span>
  );
}

function EmptyState({ hasFilters }) {
  const { t } = useTranslation("ai");
  return (
    <SharedEmptyState
      icon="vpn_key"
      title={t(hasFilters ? "AiApiKeysPage.emptyFilteredTitle" : "AiApiKeysPage.emptyTitle")}
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
      await AiApiService.revokeCredential(item.id);
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
    <div className={`${styles.dialogOverlay} ${closing ? styles.dialogOverlayOut : ""}`} role="presentation" onMouseDown={() => { if (!busy) onClose(); }}>
      <div className={styles.dialog} role="dialog" aria-modal="true" aria-labelledby="ai-api-key-revoke-title" onMouseDown={(event) => event.stopPropagation()}>
        <div className={styles.dialogHeader}>
          <div className={styles.dialogIcon}><MIcon name="warning" size={20} /></div>
          <div>
            <h2 id="ai-api-key-revoke-title" className={styles.dialogTitle}>{t("AiApiKeysPage.revokeConfirmTitle")}</h2>
            <p className={styles.dialogDesc}>{t("AiApiKeysPage.revokeConfirmDesc", { name: item.api_key_name })}</p>
          </div>
        </div>
        <div className={styles.dialogFooter}>
          <button type="button" className={styles.btnOutline} onClick={onClose} disabled={busy}>{t("AiApiKeysPage.cancel")}</button>
          <button type="button" className={styles.btnDanger} onClick={handleRevoke} disabled={busy}>{busy ? t("AiApiKeysPage.revoking") : t("AiApiKeysPage.confirmRevoke")}</button>
        </div>
      </div>
    </div>
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
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [allCount, setAllCount] = useState(0);
  const [activeCount, setActiveCount] = useState(0);
  const [inactiveCount, setInactiveCount] = useState(0);
  const [loading, setLoading] = useState(true);
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

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const res = await AiApiService.listAllCredentials(buildCredentialListParams({
        status: statusFilter,
        query,
        roles: roleFilters,
        createdRange,
        page,
      }));
      setRows(res?.data ?? []);
      setTotal(res?.count ?? 0);
      setAllCount(res?.total_count ?? res?.count ?? 0);
      setActiveCount(res?.active_count ?? 0);
      setInactiveCount(res?.inactive_count ?? 0);
    } catch (error) {
      if (!silent) toast.error(error?.message ?? t("AiApiKeysPage.loadError"));
    } finally {
      if (!silent) setLoading(false);
    }
  }, [createdRange, page, query, roleFilters, statusFilter, t, toast]);

  useEffect(() => { load(); }, [load]);
  useAutoRefresh(() => load(true));

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
      <PageHeader title={t("AiApiKeysPage.pageTitle")} subtitle={t("AiApiKeysPage.pageSubtitle")} />

      <div className={styles.controlsRow}>
        <div className={styles.segmentGroup}>
          <SegmentedControl
            className={styles.statusTabs}
            options={tabs.map(({ key, label, count }) => ({ value: key, label, badge: count }))}
            value={statusFilter}
            onChange={(value) => { setStatusFilter(value); setPage(0); }}
            ariaLabel={t("AiApiKeysPage.statusTabsLabel")}
          />
          <div ref={filterAreaRef} className={styles.filterArea}>
            <button type="button" className={`${styles.filterButton} ${activeFilterCount ? styles.filterButtonActive : ""}`} aria-haspopup="dialog" aria-expanded={filterOpen} onClick={() => setFilterOpen((current) => !current)}>
              <MIcon name="filter_alt" size={16} />{activeFilterCount ? t("AiApiKeysPage.filterActiveCount", { count: activeFilterCount }) : t("AiApiKeysPage.filterButton")}
            </button>
            {filterOpen && <FilterPopover roles={roleFilters} createdRange={createdRange} onToggleRole={toggleRole} onCreatedRangeChange={(value) => { setCreatedRange(value); setPage(0); }} onClear={clearFilters} />}
          </div>
        </div>

        <div className={styles.searchBox}>
          <MIcon name="search" size={17} />
          <input type="search" value={searchInput} onChange={(event) => setSearchInput(event.target.value)} placeholder={t("AiApiKeysPage.searchPlaceholder")} aria-label={t("AiApiKeysPage.searchPlaceholder")} />
          {searchInput && <button type="button" className={styles.clearSearch} onClick={() => setSearchInput("")} aria-label={t("AiApiKeysPage.clearSearch")}><MIcon name="close" size={15} /></button>}
        </div>
      </div>

      <div className={styles.content}>
        {loading ? <LoadingState fullPage /> : (
          rows.length === 0 ? <EmptyState hasFilters={hasFilters} /> : (
            <>
              <div className={styles.tableWrap}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th className={styles.th}>{t("AiApiKeysPage.colName")}</th>
                      <th className={styles.th}>{t("AiApiKeysPage.detailOwner")}</th>
                      <th className={styles.th}>{t("AiApiKeysPage.detailPurpose")}</th>
                      <th className={styles.th}>{t("AiApiKeysPage.detailActivity")}</th>
                      <th className={styles.th}>{t("AiApiKeysPage.colStatus")}</th>
                      <th className={styles.th}>{t("AiApiKeysPage.colCreatedAt")}</th>
                      <th className={styles.th}>{t("AiApiKeysPage.colActions")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((item) => (
                      <tr key={item.id} className={styles.tr}>
                        <td className={styles.td}>
                          <div className={styles.nameCell}>
                            <div className={styles.rowIcon}>
                              <MIcon name="vpn_key" size={20} />
                            </div>
                            <div className={styles.rowMain}>
                              <span className={styles.rowName} title={item.api_key_name || undefined}>{item.api_key_name || "—"}</span>
                              <span className={styles.rowMeta} title={item.user_email || undefined}>{item.user_email || "—"}</span>
                            </div>
                          </div>
                        </td>
                        <td className={styles.td}>
                          <span className={styles.cellText} title={item.user_full_name || undefined}>{item.user_full_name || "—"}</span>
                        </td>
                        <td className={styles.td}>
                          <span className={styles.cellText} title={item.request_purpose || undefined}>{item.request_purpose || "—"}</span>
                        </td>
                        <td className={styles.td}>
                          {item.last_used_at ? formatDateTime(item.last_used_at) : <span className={styles.cellMuted}>{t("AiApiKeysPage.detailNeverUsed")}</span>}
                        </td>
                        <td className={styles.td}><StatusBadge item={item} /></td>
                        <td className={styles.td}>{formatDate(item.created_at)}</td>
                        <td className={styles.td}>
                          {item.status === "active" ? (
                            <button
                              type="button"
                              className={styles.rowRevokeBtn}
                              onClick={() => setDeletingItem(item)}
                            >
                              <MIcon name="block" size={16} />
                              {t("AiApiKeysPage.revokeAction")}
                            </button>
                          ) : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {totalPages > 1 && <div className={styles.pagination}><span className={styles.paginationInfo}>{t("AiApiKeysPage.paginationInfo", { page: page + 1, totalPages, total })}</span><div className={styles.paginationBtns}><button type="button" className={styles.btnOutline} disabled={page === 0} onClick={() => setPage((current) => Math.max(0, current - 1))}>{t("AiApiKeysPage.prevPage")}</button><button type="button" className={styles.btnOutline} disabled={page + 1 >= totalPages} onClick={() => setPage((current) => current + 1)}>{t("AiApiKeysPage.nextPage")}</button></div></div>}
            </>
          )
        )}
      </div>

      {deleteDialog.open && <RevokeDialog item={deleteDialog.item} closing={deleteDialog.closing} onClose={() => setDeletingItem(null)} onDone={() => load()} />}
    </div>
  );
}
