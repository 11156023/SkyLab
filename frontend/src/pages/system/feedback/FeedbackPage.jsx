import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./FeedbackPage.module.scss";
import MIcon from "../../../components/MIcon";
import PageHeader from "../../../components/PageHeader/PageHeader";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import LoadingState from "../../../components/LoadingState/LoadingState";
import EmptyState from "../../../components/EmptyState/EmptyState";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { useToast } from "../../../hooks/useToast";
import { useLoadFailureMessage, useQueryView } from "../../../hooks/useQueryLoadState";
import { useFeedbackCacheUpdater, useFeedbackListQuery } from "../../../queries/feedback";
import { FEEDBACK_CATEGORIES, FeedbackService, feedbackCategoryMeta } from "../../../services/feedback";
import { formatDateTime } from "../../../utils/formatDate";
import Pagination from "../shared/Pagination";

const PAGE_SIZE = 20;
const EMPTY = Object.freeze([]);

/** 狀態篩選：預設只看待處理，處理完的收進「已處理」 */
const STATUS_OPTIONS = [
  { value: "open", labelKey: "FeedbackPage.filterOpen", icon: "inbox" },
  { value: "resolved", labelKey: "FeedbackPage.filterResolved", icon: "task_alt" },
  { value: "", labelKey: "FeedbackPage.filterAll" },
];

function CategoryBadge({ category }) {
  const { t } = useTranslation("common");
  const meta = feedbackCategoryMeta(category);
  return (
    <span className={`${styles.badge} ${styles[`badge_${meta.color}`]}`}>
      <MIcon name={meta.icon} size={14} />
      {t(meta.labelKey)}
    </span>
  );
}

/**
 * 一則回饋：類型、送出者、時間在上，內容原樣換行顯示，底部是版本／處理紀錄與動作。
 * 列本身不可點（動作都在按鈕上），所以不做整列 hover。
 */
function FeedbackItem({ item, busy, onToggleStatus, onDelete }) {
  const { t } = useTranslation("system");
  const resolved = item.status === "resolved";
  const sender = item.user_email
    ? item.user_full_name || item.user_email
    : t("FeedbackPage.deletedUser");

  return (
    <article className={`${styles.item} ${resolved ? styles.itemResolved : ""}`}>
      <header className={styles.itemHead}>
        <CategoryBadge category={item.category} />
        <span className={styles.itemUser}>
          <span className={styles.itemName}>{sender}</span>
          {item.user_full_name && item.user_email && (
            <span className={styles.itemEmail}>{item.user_email}</span>
          )}
        </span>
        <time className={styles.itemTime} dateTime={item.created_at}>
          {formatDateTime(item.created_at)}
        </time>
        {resolved && (
          <span className={`${styles.badge} ${styles.badge_success}`}>
            <MIcon name="check" size={14} />
            {t("FeedbackPage.statusResolved")}
          </span>
        )}
      </header>

      <p className={styles.itemContent}>{item.content}</p>

      <footer className={styles.itemFoot}>
        <span className={styles.itemMeta}>
          {item.app_version && <span>{t("FeedbackPage.metaVersion", { version: item.app_version })}</span>}
          {resolved && item.resolved_at && (
            <span>
              {t("FeedbackPage.metaResolved", {
                by: item.resolved_by_email ?? "—",
                at: formatDateTime(item.resolved_at),
              })}
            </span>
          )}
        </span>
        <div className={styles.itemActions}>
          <button
            type="button"
            className={styles.btnSecondary}
            disabled={busy}
            onClick={() => onToggleStatus(item)}
          >
            <MIcon name={resolved ? "undo" : "task_alt"} size={16} />
            {resolved ? t("FeedbackPage.reopen") : t("FeedbackPage.markResolved")}
          </button>
          <button
            type="button"
            className={styles.btnGhostDanger}
            disabled={busy}
            onClick={() => onDelete(item)}
          >
            <MIcon name="delete" size={16} />
            {t("FeedbackPage.delete")}
          </button>
        </div>
      </footer>
    </article>
  );
}

/**
 * 使用者回饋（管理員）：帳號設定「關於」分頁送進來的問題回報與建議。
 * 清單走 TanStack Query 靜默更新；標記已處理／刪除後讓快取失效重抓，不放手動刷新鈕。
 */
export default function FeedbackPage() {
  const { t } = useTranslation("system");
  const toast = useToast();
  const confirm = useConfirm();
  const cache = useFeedbackCacheUpdater();
  const [status, setStatus] = useState("open");
  const [category, setCategory] = useState("");
  const [page, setPage] = useState(0);
  const [busyId, setBusyId] = useState(null);

  const queryParams = useMemo(
    () => ({
      skip: page * PAGE_SIZE,
      limit: PAGE_SIZE,
      status: status || undefined,
      category: category || undefined,
    }),
    [page, status, category],
  );
  const listQuery = useFeedbackListQuery(queryParams);
  const items = listQuery.data?.data ?? EMPTY;
  const count = listQuery.data?.count ?? 0;
  const loadFailedMessage = useLoadFailureMessage(listQuery, t("Error.generic", { ns: "common" }));
  const listView = useQueryView(listQuery);
  useEffect(() => {
    if (loadFailedMessage) toast.error(loadFailedMessage);
  }, [loadFailedMessage, toast]);

  /* 翻頁後資料變少（處理掉最後一頁的全部），退回前一頁 */
  const totalPages = Math.max(Math.ceil(count / PAGE_SIZE), 1);
  useEffect(() => {
    if (page > 0 && page >= totalPages) setPage(totalPages - 1);
  }, [page, totalPages]);

  const hasFilter = Boolean(category) || status !== "open";
  const isInitialLoading = listView.loading;
  const refreshing = listQuery.isFetching && !isInitialLoading;

  function changeStatus(next) {
    setStatus(next);
    setPage(0);
  }

  function changeCategory(next) {
    setCategory(next);
    setPage(0);
  }

  function resetFilters() {
    setStatus("open");
    setCategory("");
    setPage(0);
  }

  async function toggleStatus(item) {
    const next = item.status === "resolved" ? "open" : "resolved";
    setBusyId(item.id);
    try {
      await FeedbackService.setStatus(item.id, next);
      toast.success(next === "resolved" ? t("FeedbackPage.toastResolved") : t("FeedbackPage.toastReopened"));
      await cache.invalidate();
    } catch (err) {
      toast.error(err?.message ?? t("FeedbackPage.toastUpdateFailed"));
    } finally {
      setBusyId(null);
    }
  }

  async function remove(item) {
    const ok = await confirm({
      title: t("FeedbackPage.deleteTitle"),
      message: t("FeedbackPage.deleteMessage"),
      confirmText: t("FeedbackPage.delete"),
      danger: true,
    });
    if (!ok) return;
    setBusyId(item.id);
    try {
      await FeedbackService.remove(item.id);
      toast.success(t("FeedbackPage.toastDeleted"));
      await cache.invalidate();
    } catch (err) {
      toast.error(err?.message ?? t("FeedbackPage.toastDeleteFailed"));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className={styles.page}>
      <PageHeader title={t("FeedbackPage.pageTitle")} />

      <div className={styles.toolbar} data-guide="feedback-filters">
        <SegmentedControl
          options={STATUS_OPTIONS.map((o) => ({ value: o.value, label: t(o.labelKey), icon: o.icon }))}
          value={status}
          onChange={changeStatus}
          ariaLabel={t("FeedbackPage.filterStatus")}
        />
        <select
          className={styles.filterSelect}
          value={category}
          aria-label={t("FeedbackPage.filterCategory")}
          onChange={(e) => changeCategory(e.target.value)}
        >
          <option value="">{t("FeedbackPage.allCategories")}</option>
          {FEEDBACK_CATEGORIES.map((c) => (
            <option key={c.value} value={c.value}>
              {t(c.labelKey, { ns: "common" })}
            </option>
          ))}
        </select>
      </div>

      <div className={`${styles.content} ${refreshing ? styles.refreshing : ""}`} aria-busy={listQuery.isFetching}>
        {isInitialLoading ? (
          <LoadingState fullPage text={t("FeedbackPage.loading")} />
        ) : items.length === 0 ? (
          <EmptyState
            icon={hasFilter ? "search_off" : "rate_review"}
            title={hasFilter ? t("FeedbackPage.emptyNoResult") : t("FeedbackPage.emptyNone")}
            description={hasFilter ? undefined : t("FeedbackPage.emptyNoneHint")}
            action={
              hasFilter ? (
                <button type="button" className={styles.btnSecondary} onClick={resetFilters}>
                  <MIcon name="filter_alt_off" size={16} />
                  {t("EmptyState.clearFilters", { ns: "common" })}
                </button>
              ) : undefined
            }
          />
        ) : (
          <>
            <div className={styles.list} data-guide="feedback-list">
              {items.map((item) => (
                <FeedbackItem
                  key={item.id}
                  item={item}
                  busy={busyId === item.id}
                  onToggleStatus={toggleStatus}
                  onDelete={remove}
                />
              ))}
            </div>
            <Pagination
              page={page}
              totalPages={totalPages}
              info={t("FeedbackPage.paginationInfo", { count, page: page + 1, totalPages })}
              prevLabel={t("FeedbackPage.prevPage")}
              nextLabel={t("FeedbackPage.nextPage")}
              onChange={setPage}
            />
          </>
        )}
      </div>
    </div>
  );
}
