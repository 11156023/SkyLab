import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { createColumnHelper, useTable } from "@tanstack/react-table";
import styles from "./AiApiReviewPage.module.scss";
import MIcon from "../../../components/MIcon";
import DataTable from "../../../components/DataTable/DataTable";
import { baseFeatures } from "../../../components/DataTable/features";
import Modal from "../../../components/Modal/Modal";
import LoadingState from "../../../components/LoadingState/LoadingState";
import SharedEmptyState from "../../../components/EmptyState/EmptyState";
import ErrorState from "../../../components/ErrorState/ErrorState";
import { AiApiService } from "../../../services/aiApi";
import { useAiApiCacheUpdater, useAllAiRequestsQuery } from "../../../queries/aiApi";
import { useToast } from "../../../hooks/useToast";
import { useLoadError } from "../../../hooks/useQueryLoadState";
import useDialogPresence from "../../../hooks/useDialogPresence";
import PageHeader from "../../../components/PageHeader/PageHeader";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import { formatDateTime } from "../../../utils/formatDate";

function EmptyState() {
  const { t } = useTranslation("ai");
  return <SharedEmptyState icon="assignment_turned_in" title={t("AiApiReviewPage.emptyTitle")} />;
}

/* ── Review Dialog ── */
function ReviewDialog({ open, onClose, request, action, onDone }) {
  const { t } = useTranslation("ai");
  const toast = useToast();
  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);
  // 關閉時先播放離場動畫再卸載
  const presence = useDialogPresence(open);

  if (!presence.open || !request) return null;

  const isApprove = action === "approved";

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      await AiApiService.reviewRequest(request.id, {
        status: action,
        review_comment: comment || null,
      });
      toast.success(isApprove ? t("AiApiReviewPage.approveSuccess") : t("AiApiReviewPage.rejectSuccess"));
      setComment("");
      onClose();
      onDone();
    } catch (e) {
      toast.error(e?.message ?? t("AiApiReviewPage.actionError"));
    } finally {
      setSubmitting(false);
    }
  };

  /* 外框（遮罩、Esc、焦點、捲動鎖）交給共用 Modal；送出中 Esc／點遮罩都不關 */
  return (
    <Modal
      closing={presence.closing}
      onClose={onClose}
      busy={submitting}
      size="md"
      title={isApprove ? t("AiApiReviewPage.approveDialogTitle") : t("AiApiReviewPage.rejectDialogTitle")}
      actions={
        <>
          <button type="button" className={styles.btnSecondary} onClick={onClose} disabled={submitting}>
            {t("AiApiReviewPage.cancel")}
          </button>
          <button
            type="button"
            className={isApprove ? styles.btnPrimary : styles.btnDanger}
            onClick={handleSubmit}
            disabled={submitting}
          >
            {submitting ? t("AiApiReviewPage.processing") : isApprove ? t("AiApiReviewPage.confirmApprove") : t("AiApiReviewPage.confirmReject")}
          </button>
        </>
      }
    >
      <div className={styles.dialogBody}>
        <div className={styles.dialogInfo}>
          <div>{t("AiApiReviewPage.dialogApplicant", { value: request.user_full_name || request.user_email })}</div>
          <div>{t("AiApiReviewPage.dialogKeyName", { value: request.api_key_name })}</div>
          <div>{t("AiApiReviewPage.dialogAppliedAt", { value: formatDateTime(request.created_at, t("AiApiReviewPage.notReviewed")) })}</div>
          <div className={styles.dialogPurpose}>{t("AiApiReviewPage.dialogPurpose", { value: request.purpose })}</div>
        </div>
        <textarea
          className={styles.dialogTextarea}
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder={t("AiApiReviewPage.commentPlaceholder")}
          maxLength={2000}
          rows={4}
        />
      </div>
    </Modal>
  );
}

/* ── ReviewActions in table row ── */
function ReviewActions({ item, onDone }) {
  const { t } = useTranslation("ai");
  const [approveOpen, setApproveOpen] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);

  if (item.status !== "pending") {
    return (
      <span className={styles.reviewComment}>
        {item.review_comment || "—"}
      </span>
    );
  }

  return (
    <>
      <div className={styles.actions} data-guide="ai-review-actions">
        <button
          type="button"
          className={`${styles.actionBtn} ${styles.actionBtnOk}`}
          title={t("AiApiReviewPage.actionApprove")}
          onClick={() => setApproveOpen(true)}
        >
          <MIcon name="check" size={16} />
          {t("AiApiReviewPage.actionApprove")}
        </button>
        <button
          type="button"
          className={`${styles.actionBtn} ${styles.actionBtnDanger}`}
          title={t("AiApiReviewPage.actionReject")}
          onClick={() => setRejectOpen(true)}
        >
          <MIcon name="close" size={16} />
          {t("AiApiReviewPage.actionReject")}
        </button>
      </div>
      <ReviewDialog
        open={approveOpen}
        onClose={() => setApproveOpen(false)}
        request={item}
        action="approved"
        onDone={onDone}
      />
      <ReviewDialog
        open={rejectOpen}
        onClose={() => setRejectOpen(false)}
        request={item}
        action="rejected"
        onDone={onDone}
      />
    </>
  );
}

/* ── 申請表格（TanStack Table）──
   欄位定義放模組層級：v9 的 FlexRender 把函式型的 header／cell 當元件掛載，render 裡新建會讓整格重新掛載。
   表頭文字取 meta.headerKey 的翻譯；審核完成後的重抓經 table meta 交回頁面，
   核准／拒絕對話框的開關狀態仍由每列的 ReviewActions 自己管 */
const columnHelper = createColumnHelper();

const STATUS_LABEL_KEYS = {
  pending: "AiApiReviewPage.statusPending",
  approved: "AiApiReviewPage.statusApproved",
  rejected: "AiApiReviewPage.statusRejected",
};

function HeaderLabel({ column }) {
  const { t } = useTranslation("ai");
  return t(column.columnDef.meta.headerKey);
}

function ApplicantCell({ row }) {
  const r = row.original;
  return (
    <div className={styles.userCell}>
      <span className={styles.userName}>{r.user_full_name || r.user_email}</span>
      {r.user_full_name && r.user_email && (
        <span className={styles.userEmail}>{r.user_email}</span>
      )}
    </div>
  );
}

function PurposeCell({ row }) {
  const r = row.original;
  return (
    <span className={styles.purposeCell} title={r.purpose}>
      {(r.purpose ?? "").length > 60 ? `${r.purpose.slice(0, 60)}…` : r.purpose}
    </span>
  );
}

function StatusCell({ row }) {
  const { t } = useTranslation("ai");
  const status = row.original.status;
  const labelKey = STATUS_LABEL_KEYS[status];
  return (
    <span className={`${styles.badge} ${styles[`badge_${status}`]}`}>
      <span className={styles.dot} />
      {labelKey ? t(labelKey) : status}
    </span>
  );
}

function DateTimeCell({ getValue }) {
  const { t } = useTranslation("ai");
  return formatDateTime(getValue(), t("AiApiReviewPage.notReviewed"));
}

function ActionsCell({ row, table }) {
  /* 審核完成時才讀 meta，拿到的是頁面當下的重抓函式 */
  return <ReviewActions item={row.original} onDone={() => table.options.meta.onReviewed()} />;
}

function columnMeta(headerKey, extra) {
  return { headerKey, thClassName: styles.th, tdClassName: styles.td, ...extra };
}

const requestColumns = columnHelper.columns([
  columnHelper.display({ id: "applicant", header: HeaderLabel, cell: ApplicantCell, meta: columnMeta("AiApiReviewPage.colApplicant") }),
  columnHelper.accessor("api_key_name", { header: HeaderLabel, cell: ({ getValue }) => getValue(), meta: columnMeta("AiApiReviewPage.colKeyName") }),
  columnHelper.display({
    id: "purpose",
    header: HeaderLabel,
    cell: PurposeCell,
    meta: columnMeta("AiApiReviewPage.colPurpose", { tdProps: { "data-guide": "ai-review-purpose" } }),
  }),
  columnHelper.display({ id: "status", header: HeaderLabel, cell: StatusCell, meta: columnMeta("AiApiReviewPage.colStatus") }),
  columnHelper.accessor("created_at", { header: HeaderLabel, cell: DateTimeCell, meta: columnMeta("AiApiReviewPage.colAppliedAt") }),
  columnHelper.accessor("reviewed_at", { header: HeaderLabel, cell: DateTimeCell, meta: columnMeta("AiApiReviewPage.colReviewedAt") }),
  columnHelper.display({ id: "actions", header: HeaderLabel, cell: ActionsCell, meta: columnMeta("AiApiReviewPage.colActions") }),
]);

const requestRowClassName = () => styles.tr;

/* ── Main ── */
const REQUEST_TAB_KEYS = ["pending", "approved", "rejected", "all"];
/* 後端 /ai-api/requests 的 limit 上限 */
const REQUEST_LIST_LIMIT = 100;
const EMPTY = Object.freeze([]);

/* 狀態篩選交給後端：清單上限 100 筆且新到舊，前端自己篩會讓比最新 100 筆還舊的待審申請看不到。
   非目前分頁只取 1 筆，拿 count 給角標。 */
function tabParams(key, activeTab) {
  return {
    status: key === "all" ? undefined : key,
    limit: key === activeTab ? REQUEST_LIST_LIMIT : 1,
  };
}

function tabCount(query) {
  return query.data?.count ?? query.data?.data?.length ?? 0;
}

export default function AiApiReviewPage() {
  const { t } = useTranslation("ai");
  const toast = useToast();
  const [activeTab, setActiveTab] = useState("pending");

  const TABS = [
    { key: "pending",  label: t("AiApiReviewPage.tabPending") },
    { key: "approved", label: t("AiApiReviewPage.tabApproved") },
    { key: "rejected", label: t("AiApiReviewPage.tabRejected") },
    { key: "all",      label: t("AiApiReviewPage.tabAll") },
  ];

  /* 四個分頁各一份查詢（hook 固定四次、不在迴圈裡呼叫），以「狀態＋筆數上限」為 key：
     切換分頁時較晚回來的舊分頁回應只會進舊 key，不會蓋掉目前分頁，也不會提早收掉載入畫面。
     背景 30 秒刷新、分頁隱藏時暫停；角標在換分頁時沿用上一份筆數（keepPreviousData）。 */
  const pendingQuery = useAllAiRequestsQuery(tabParams("pending", activeTab));
  const approvedQuery = useAllAiRequestsQuery(tabParams("approved", activeTab));
  const rejectedQuery = useAllAiRequestsQuery(tabParams("rejected", activeTab));
  const allQuery = useAllAiRequestsQuery(tabParams("all", activeTab));
  const aiCache = useAiApiCacheUpdater();
  const tabQueries = { pending: pendingQuery, approved: approvedQuery, rejected: rejectedQuery, all: allQuery };
  const activeQuery = tabQueries[activeTab];
  const counts = {
    pending: tabCount(pendingQuery),
    approved: tabCount(approvedQuery),
    rejected: tabCount(rejectedQuery),
    all: tabCount(allQuery),
  };

  /* 清單要等這個分頁自己的完整資料（上限 100 筆）回來才顯示：佔位資料是上一個 key（只取 1 筆的角標查詢） */
  const rowsReady = activeQuery.data !== undefined && !activeQuery.isPlaceholderData;
  /* 四個查詢各自記錯誤狀態（見 useLoadError）：背景重試期間錯誤畫面維持，按重試才回到載入中。
     沒有資料時每次重抓 TanStack 都會把佔位資料放回來，所以佔位資料也當成沒資料交給它 */
  const pendingLoad = useLoadError(pendingQuery);
  const approvedLoad = useLoadError(approvedQuery);
  const rejectedLoad = useLoadError(rejectedQuery);
  const allLoad = useLoadError(allQuery);
  const tabLoads = { pending: pendingLoad, approved: approvedLoad, rejected: rejectedLoad, all: allLoad };
  const activeLoadError = tabLoads[activeTab].error;
  const loadError = !rowsReady && Boolean(activeLoadError);
  const loading = !rowsReady && !loadError && (activeQuery.isPending || activeQuery.isFetching);
  const requests = rowsReady ? activeQuery.data?.data ?? EMPTY : EMPTY;

  /* 重試：四個分頁一起重抓（與原本重抓全部清單相同），沒資料的回到載入中 */
  function retryLoad() {
    Object.values(tabLoads).forEach((load) => load.retry());
  }

  /* 目前分頁一筆資料都還沒拿到就失敗才提醒（與原本的前景載入相同）；背景刷新失敗不跳 toast。
     錯誤取自 useLoadError：背景重試期間不會先清空再變回來，同一個錯誤不會每輪重跳 */
  const loadErrorMessage = activeLoadError
    ? activeLoadError.message ?? t("AiApiReviewPage.loadError")
    : null;
  useEffect(() => {
    if (loadErrorMessage) toast.error(loadErrorMessage);
  }, [loadErrorMessage, toast]);

  /* 審核後在背景重抓所有分頁（清單留在畫面上），重抓失敗照原本提醒 */
  function refreshAfterReview() {
    aiCache.invalidateAllRequests({ throwOnError: true })
      .catch((e) => toast.error(e?.message ?? t("AiApiReviewPage.loadError")));
  }

  const filtered = useMemo(() => {
    if (activeTab === "all") return requests;
    return requests.filter((r) => r.status === activeTab);
  }, [requests, activeTab]);

  const table = useTable({
    features: baseFeatures,
    columns: requestColumns,
    data: filtered,
    getRowId: (r) => String(r.id),
    meta: { onReviewed: refreshAfterReview },
  });

  return (
    <div className={styles.page}>
      <PageHeader title={t("AiApiReviewPage.pageTitle")} />

      <div className={styles.tabsRow} data-guide="ai-review-tabs">
        <SegmentedControl
          className={styles.tabsControl}
          options={TABS.map(({ key, label }) => ({
            value: key,
            label,
            badge: counts[key] ?? 0,
          }))}
          value={activeTab}
          onChange={setActiveTab}
          ariaLabel={t("AiApiReviewPage.tabsAriaLabel")}
        />
      </div>

      <div className={styles.content}>
        {loading ? (
          <LoadingState fullPage />
        ) : loadError ? (
          <ErrorState onRetry={retryLoad} />
        ) : filtered.length === 0 ? (
          <EmptyState />
        ) : (
          <div className={styles.tableWrap} data-guide="ai-review-table">
            <DataTable table={table} className={styles.table} rowClassName={requestRowClassName} />
          </div>
        )}
      </div>
    </div>
  );
}
