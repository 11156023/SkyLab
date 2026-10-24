import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./RequestReviewPage.module.scss";
import MIcon from "../../../components/MIcon";
import SharedEmptyState from "../../../components/EmptyState/EmptyState";
import { useToast } from "../../../hooks/useToast";
import { useLoadError } from "../../../hooks/useQueryLoadState";
import LoadingState from "../../../components/LoadingState/LoadingState";
import { SpecChangeRequestsService } from "../../../services/specChangeRequests";
import { VmRequestsService } from "../../../services/vmRequests";
import { CONSUMED_REQUEST_MARKERS } from "../../../services/pendingResources";
import {
  useAllVmRequestsQuery,
  useVmRequestCacheUpdater,
  useVmRequestReviewContextQuery,
} from "../../../queries/vmRequests";
import { useAllSpecChangeRequestsQuery, useSpecChangeRequestCacheUpdater } from "../../../queries/specChangeRequests";
import PageHeader from "../../../components/PageHeader/PageHeader";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import Modal from "../../../components/Modal/Modal";
import DataToolbar from "../../../components/DataToolbar/DataToolbar";
import BatchBar, { BatchAction } from "../../../components/DataToolbar/BatchBar";
import SelectCheckbox from "../../../components/DataToolbar/SelectCheckbox";
import ToolbarSearch from "../../../components/DataToolbar/ToolbarSearch";
import useDialogPresence from "../../../hooks/useDialogPresence";
import useRowSelection from "../../../hooks/useRowSelection";
import { formatShortDateTime } from "../../../utils/formatDate";
import { runBatch } from "../../../utils/runBatch";

function useTabs() {
  const { t } = useTranslation("resource");
  return useMemo(() => [
    { key: "pending", label: t("RequestReviewPage.tabPending") },
    { key: "approved", label: t("RequestReviewPage.tabApproved") },
    { key: "rejected", label: t("RequestReviewPage.tabRejected") },
    { key: "expired", label: t("RequestReviewPage.tabExpired") },
    { key: "all", label: t("RequestReviewPage.tabAll") },
  ], [t]);
}

/* 主 badge 只反映審核者的決策結果（#4）：准／不准／還沒定，
   加上申請自身的取消與過期。核准後的執行進度是申請人的事，降為次要灰字。 */
function useStatusMeta() {
  const { t } = useTranslation("resource");
  return useMemo(() => ({
    pending: { label: t("RequestReviewPage.statusPending"), tone: "info" },
    approved: { label: t("RequestReviewPage.statusApproved"), tone: "success" },
    rejected: { label: t("RequestReviewPage.statusRejected"), tone: "danger" },
    cancelled: { label: t("RequestReviewPage.statusCancelled"), tone: "muted" },
    expired: { label: t("RequestReviewPage.statusExpired"), tone: "muted" },
  }), [t]);
}

/** 規格調整核准後的套用進度（列表次要資訊用；未核准回傳 null） */
function specProgressLabel(request, t) {
  if (request.status !== "approved") return null;
  switch (request.apply_status) {
    case "applied":
      return t("RequestReviewPage.statusApplied");
    case "applying":
      return t("RequestReviewPage.statusApplying");
    case "failed":
    case "interrupted":
      return t("RequestReviewPage.statusApplyFailed");
    default:
      return t("RequestReviewPage.statusAwaitingApply");
  }
}


/* 超長文字預設收合成 4 行，實際有被裁掉才顯示展開按鈕 */
function ExpandableText({ text }) {
  const { t } = useTranslation("resource");
  const [expanded, setExpanded] = useState(false);
  const [clampable, setClampable] = useState(false);
  const textRef = useRef(null);

  useLayoutEffect(() => {
    setExpanded(false);
  }, [text]);

  useLayoutEffect(() => {
    if (expanded) return undefined;
    const measure = () => {
      const el = textRef.current;
      if (el) setClampable(el.scrollHeight > el.clientHeight + 1);
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [text, expanded]);

  return (
    <>
      <p ref={textRef} className={expanded ? undefined : styles.reasonClamp}>{text}</p>
      {(clampable || expanded) && (
        <button type="button" className={styles.reasonToggle} onClick={() => setExpanded((value) => !value)}>
          {expanded ? t("RequestReviewPage.showLess") : t("RequestReviewPage.showMore")}
        </button>
      )}
    </>
  );
}

function shortTime(value, t) {
  return formatShortDateTime(value, t("RequestReviewPage.notSet"));
}

function formatRange(startAt, endAt, t) {
  if (!startAt && !endAt) return t("RequestReviewPage.notSet");
  if (!endAt) return t("RequestReviewPage.startingFrom", { time: shortTime(startAt, t) });
  return `${shortTime(startAt, t)} - ${shortTime(endAt, t)}`;
}

function vmSpecLabel(request, t) {
  if (!request) return "-";
  const disk =
    request.resource_type === "vm"
      ? t("RequestReviewPage.diskLabel", { size: request.disk_size ?? 0 })
      : t("RequestReviewPage.rootfsLabel", { size: request.rootfs_size ?? 0 });
  return t("RequestReviewPage.specVm", {
    cores: request.cores,
    memory: (request.memory / 1024).toFixed(1),
    disk,
  });
}

const CHANGE_TYPE_KEYS = {
  cpu: "RequestReviewPage.changeTypeCpu",
  memory: "RequestReviewPage.changeTypeMemory",
  disk: "RequestReviewPage.changeTypeDisk",
  combined: "RequestReviewPage.changeTypeCombined",
  expiry: "RequestReviewPage.changeTypeExpiry",
};

/** 規格調整的類型：後端的 enum 值換成三語標籤，沒見過的值照原樣顯示 */
export function changeTypeLabel(type, t) {
  return CHANGE_TYPE_KEYS[type] ? t(CHANGE_TYPE_KEYS[type]) : type || "-";
}

/* 記憶體跟建立申請的規格一樣以 GB 顯示（後端存 MB） */
const memoryGb = (mb) => (mb == null ? "-" : (mb / 1024).toFixed(1));

export function specChangeLabel(request, t) {
  const parts = [
    request.requested_cpu
      ? t("RequestReviewPage.specChangeCpu", { from: request.current_cpu ?? "-", to: request.requested_cpu })
      : "",
    request.requested_memory
      ? t("RequestReviewPage.specChangeRam", { from: memoryGb(request.current_memory), to: memoryGb(request.requested_memory) })
      : "",
    request.requested_disk
      ? t("RequestReviewPage.specChangeDisk", { from: request.current_disk ?? "-", to: request.requested_disk })
      : "",
    request.requested_expiry_date
      ? t("RequestReviewPage.specChangeExpiry", {
          from: request.current_expiry_date ?? t("RequestReviewPage.expiryUnlimited"),
          to: request.requested_expiry_date,
        })
      : "",
  ].filter(Boolean);
  return parts.join(" / ") || changeTypeLabel(request.change_type, t);
}

/* AI API 金鑰申請有專屬的 /ai-api-review 頁；刪除申請是自動處理的佇列
   （無審核動作），由背景任務頁呈現，這裡都不列出 */
function sourceLabel(source, t) {
  return source === "spec" ? t("RequestReviewPage.sourceSpec") : t("RequestReviewPage.sourceCreate");
}

function sourceIcon(item) {
  if (item.source === "spec") return "tune";
  return item.raw?.resource_type === "vm" ? "computer" : "terminal";
}

/* 審核頁只反映申請本身的狀態；機器後來被刪掉是資源的事，不在這裡呈現 */
function normalizeVmRequest(request, t) {
  const reviewStatus = ["pending", "approved", "rejected", "expired"].includes(request.status)
    ? request.status
    : "other";

  return {
    id: `vm:${request.id}`,
    rawId: request.id,
    source: "vm",
    raw: request,
    reviewStatus,
    status: request.status,
    title: request.hostname || request.name || t("RequestReviewPage.unnamedRequest"),
    user: request.user_full_name || request.user_email || t("RequestReviewPage.unknownUser"),
    userSubtext: request.user_email || request.user_id || "-",
    timeText: formatRange(request.start_at, request.end_at, t),
    specText: vmSpecLabel(request, t),
    reason: request.reason,
    paramLabel: t("RequestReviewPage.paramLabelOs"),
    paramText:
      request.os_info ||
      request.ostemplate ||
      (request.template_id ? `Template #${request.template_id}` : t("RequestReviewPage.notSet")),
    gpuText: request.gpu_mapping_id || t("RequestReviewPage.gpuNotRequested"),
    nodeText: request.assigned_node || request.desired_node || t("RequestReviewPage.nodeNotEvaluated"),
    createdAt: request.created_at,
    reviewedAt: request.reviewed_at,
  };
}

function normalizeSpecRequest(request, t) {
  return {
    id: `spec:${request.id}`,
    rawId: request.id,
    source: "spec",
    raw: request,
    reviewStatus: request.status,
    status: request.status,
    progressText: specProgressLabel(request, t),
    title: request.resource_name
      ? t("RequestReviewPage.specChangeTitleNamed", { name: request.resource_name, vmid: request.vmid })
      : t("RequestReviewPage.specChangeTitle", { vmid: request.vmid }),
    user: request.user_full_name || request.user_email || t("RequestReviewPage.unknownUser"),
    userSubtext: request.user_email || request.user_id || "-",
    timeText: shortTime(request.created_at, t),
    specText: specChangeLabel(request, t),
    reason: request.reason,
    paramLabel: t("RequestReviewPage.paramLabelChangeType"),
    paramText: changeTypeLabel(request.change_type, t),
    gpuText: "-",
    nodeText: `VMID ${request.vmid}`,
    createdAt: request.created_at,
    reviewedAt: request.reviewed_at,
  };
}

function StatusBadge({ status }) {
  const statusMeta = useStatusMeta();
  const meta = statusMeta[status] ?? { label: status, tone: "muted" };
  return (
    <span className={`${styles.badge} ${styles[`badge_${meta.tone}`]}`}>
      {meta.label}
    </span>
  );
}

function EmptyState({ onClearSearch }) {
  const { t } = useTranslation("resource");
  return <SharedEmptyState icon="assignment_turned_in" title={t("RequestReviewPage.emptyTitle")}
    action={onClearSearch && <button type="button" className={styles.btnSecondary} onClick={onClearSearch}><MIcon name="search_off" size={16} />{t("EmptyState.clearSearch", { ns: "common" })}</button>} />;
}

/* 批量拒絕：列出要拒絕的申請（最多 5 筆），共用一段審核備註（選填，同單筆） */
const BATCH_NAME_PREVIEW = 5;

/* 外觀比照共用確認框（useConfirm 的危險款）：紅色警告圖示＋標題＋說明，多一份白底名單與備註欄 */
function BatchRejectDialog({ items, closing, onClose, onSubmit }) {
  const { t } = useTranslation("resource");
  const [comment, setComment] = useState("");
  const preview = items.slice(0, BATCH_NAME_PREVIEW);
  const rest = items.length - preview.length;

  return (
    <Modal
      as="form"
      onSubmit={(event) => { event.preventDefault(); onSubmit(comment.trim() || null); }}
      closing={closing}
      onClose={onClose}
      role="alertdialog"
      icon={<span className={styles.dialogIconDanger}><MIcon name="warning" size={20} /></span>}
      title={t("RequestReviewPage.batchRejectTitle", { count: items.length })}
      description={t("RequestReviewPage.batchRejectDesc")}
      actions={(
        <>
          <button type="button" className={styles.btnSecondary} onClick={onClose}>{t("RequestReviewPage.cancel")}</button>
          <button type="submit" className={styles.btnDanger}>{t("RequestReviewPage.batchRejectConfirm", { count: items.length })}</button>
        </>
      )}
    >
      <ul className={styles.rejectList}>
        {preview.map((item) => (
          <li key={item.id} className={styles.rejectItem}>
            <MIcon name={sourceIcon(item)} size={16} />
            <span className={styles.rejectItemName}>{item.title}</span>
            <span className={styles.rejectItemUser}>{item.user}</span>
          </li>
        ))}
        {rest > 0 && <li className={styles.rejectMore}>{t("RequestReviewPage.batchNamesMore", { count: rest })}</li>}
      </ul>
      <label className={styles.dialogField}>
        <span>{t("RequestReviewPage.commentLabel")}</span>
        <textarea
          value={comment}
          onChange={(event) => setComment(event.target.value)}
          placeholder={t("RequestReviewPage.batchCommentPlaceholder")}
        />
      </label>
    </Modal>
  );
}

function InfoRow({ label, value }) {
  return (
    <div className={styles.infoRow}>
      <span>{label}</span>
      <strong>{value || "-"}</strong>
    </div>
  );
}

const TAB_KEYS = ["pending", "approved", "rejected", "expired", "all"];
const LIST_LIMIT = 100;
/* 待審分頁是審核工作佇列，不能被截斷：service 超過後端單頁上限時會自動以 skip 分頁補齊 */
const PENDING_LIST_LIMIT = 1000;
const EMPTY_PAGE = { data: [], count: 0 };
const NO_REQUESTS = Object.freeze([]);

function pageCount(res) {
  return res?.count ?? res?.data?.length ?? 0;
}

/* 狀態篩選交給後端：清單上限 100 筆且依建立時間新到舊，前端自己篩
   會讓比最新 100 筆還舊的待審申請永遠看不到。規格調整沒有 expired 狀態，
   傳過去會 422，不送請求直接視為空。非目前分頁只取筆數（limit=1）給角標用。
   每個分頁×每種上限各是一份快取：切分頁時舊分頁晚回來的回應只會寫進它自己的快取，
   不會蓋掉新分頁的清單；背景刷新也只會撞上同一份快取裡最新的那次請求 */
function useTabPages(key, limit, { enabled = true } = {}) {
  const status = key === "all" ? undefined : key;
  const hasSpec = key !== "expired";
  const vmQuery = useAllVmRequestsQuery(status, { limit }, { enabled });
  const specQuery = useAllSpecChangeRequestsQuery({ status, limit }, { enabled: enabled && hasSpec });
  /* 結果物件是存取追蹤的：兩份查詢用到的欄位都先無條件讀出來，短路判斷才不會漏追蹤 */
  const vmPage = vmQuery.data;
  /* 首載失敗看 useLoadError：沒資料時 v5 每次重抓都清掉 error，看 isError 的錯誤會在背景重試時閃回載入中 */
  const vmLoadError = useLoadError(vmQuery).error;
  const specData = specQuery.data;
  const specLoadError = useLoadError(specQuery).error;
  const specPage = hasSpec ? specData : EMPTY_PAGE;
  const noData = vmPage === undefined || specPage === undefined;
  const failure = vmLoadError ?? (hasSpec ? specLoadError : null);
  return {
    vmPage,
    specPage,
    noData,
    /* 只有「還沒有任何資料」時才算錯誤；背景刷新失敗沿用上一筆資料（與原本靜默刷新相同） */
    error: noData && failure ? failure : null,
    count: noData ? undefined : pageCount(vmPage) + pageCount(specPage),
  };
}

export default function RequestReviewPage() {
  const { t } = useTranslation("resource");
  const tabs = useTabs();
  const toast = useToast();
  const [activeTab, setActiveTab] = useState("pending");
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  /* 切換列時詳情欄捲回頂端，避免殘留上一筆的捲動位置 */
  const detailScrollRef = useRef(null);
  useEffect(() => { detailScrollRef.current?.scrollTo({ top: 0 }); }, [selectedId]);
  const [comment, setComment] = useState("");
  const [reviewing, setReviewing] = useState(false);
  const vmCache = useVmRequestCacheUpdater();
  const specCache = useSpecChangeRequestCacheUpdater();

  /* 清單走 TanStack Query：目前分頁抓完整清單（待審 1000 筆、其他 100 筆），其餘分頁只抓筆數；
     背景 30 秒刷新、分頁隱藏時暫停，切回看過的分頁先顯示快取再背景更新 */
  const list = useTabPages(activeTab, activeTab === "pending" ? PENDING_LIST_LIMIT : LIST_LIMIT);
  /* 角標：目前分頁的筆數取自完整清單，那個分頁的筆數查詢先停著（請求數與原本相同）。
     hook 不能放進迴圈，五個分頁逐一寫出來 */
  const pendingCount = useTabPages("pending", 1, { enabled: activeTab !== "pending" });
  const approvedCount = useTabPages("approved", 1, { enabled: activeTab !== "approved" });
  const rejectedCount = useTabPages("rejected", 1, { enabled: activeTab !== "rejected" });
  const expiredCount = useTabPages("expired", 1, { enabled: activeTab !== "expired" });
  const allCount = useTabPages("all", 1, { enabled: activeTab !== "all" });
  const countPages = {
    pending: pendingCount,
    approved: approvedCount,
    rejected: rejectedCount,
    expired: expiredCount,
    all: allCount,
  };
  const tabCounts = Object.fromEntries(TAB_KEYS.map((key) => [
    key,
    (key === activeTab ? list.count : undefined) ?? countPages[key].count,
  ]));
  const loading = list.noData && !list.error;
  const error = list.error ? (list.error.message ?? t("RequestReviewPage.loadRequestsFailed")) : "";
  const listNoData = list.noData;
  const vmPage = list.vmPage;
  const specPage = list.specPage;
  const requests = useMemo(() => {
    if (listNoData) return NO_REQUESTS;
    return [
      ...(vmPage?.data ?? []).map((r) => normalizeVmRequest(r, t)),
      ...(specPage?.data ?? []).map((r) => normalizeSpecRequest(r, t)),
    ].sort(
      (a, b) =>
        new Date(b.createdAt || 0).getTime() - new Date(a.createdAt || 0).getTime(),
    );
  }, [listNoData, vmPage, specPage, t]);

  /* 不 fallback 到第一筆：背景自動刷新或審核後清單變動時，詳情面板悄悄換人
     會讓審核者對沒看過的申請按下核准 */
  const selected = useMemo(
    () => requests.find((request) => request.id === selectedId) ?? null,
    [requests, selectedId],
  );

  /* 目前那筆不在清單裡了（背景刷新、審核後、換分頁）就清空選取，不 fallback 到別筆
     （理由見 selected 上方）；清單還沒有資料時先不動，等新分頁載入完再判斷 */
  useEffect(() => {
    if (listNoData || selectedId == null) return;
    if (!requests.some((item) => item.id === selectedId)) setSelectedId(null);
  }, [listNoData, requests, selectedId]);

  /* 選取換人（含背景刷新讓原本那筆消失）時備註跟著清空，不能帶到別筆申請上 */
  useEffect(() => { setComment(""); }, [selectedId]);

  /* 待審的 VM 申請才查審核脈絡（可行性、預計節點）；選到別筆就換一份快取 */
  const contextEnabled = Boolean(selected?.rawId)
    && selected?.source === "vm"
    && selected?.reviewStatus === "pending";
  const contextQuery = useVmRequestReviewContextQuery(selected?.rawId, { enabled: contextEnabled });
  const contextData = contextQuery.data;
  const contextFetching = contextQuery.isFetching;
  const contextFailed = contextQuery.isError;
  const context = contextEnabled ? (contextData ?? null) : null;
  const contextLoading = contextEnabled && contextData === undefined && contextFetching;
  const contextError = contextEnabled && contextData === undefined && contextFailed && !contextFetching
    ? (contextQuery.error?.message ?? t("RequestReviewPage.loadContextFailed"))
    : "";
  /* 核准只看最新的脈絡：換回看過的申請時先顯示快取，但重抓完之前不能按核准 */
  const contextBusy = contextEnabled && contextFetching;

  /** 審核、批量拒絕、重試：所有分頁的清單與角標都重抓（有資料時畫面沿用不閃載入畫面）；
      還在路上的背景刷新會被取消，審核前送出的舊快照不會蓋回來 */
  function refreshAll() {
    return Promise.all([vmCache.invalidateEveryone(), specCache.invalidateEveryone()]);
  }

  function changeTab(tab) {
    setActiveTab(tab);
    setComment("");
  }

  async function submitReview(status) {
    if (!selected?.rawId || reviewing || selected.reviewStatus !== "pending") return;
    setReviewing(true);
    try {
      const body = {
        status,
        review_comment: comment.trim() || null,
      };
      if (selected.source === "vm") {
        await VmRequestsService.review(selected.rawId, body);
      } else {
        await SpecChangeRequestsService.review(selected.rawId, body);
      }
      toast.success(status === "approved" ? t("RequestReviewPage.approvedToast") : t("RequestReviewPage.rejectedToast"));
      setComment("");
      setSelectedId(null);
      /* 重抓完才解除 reviewing：剛審過的那筆在清單更新前還看得到，期間不能再按一次 */
      await refreshAll();
    } catch (err) {
      toast.error(err?.message ?? t("RequestReviewPage.reviewFailed"));
    } finally {
      setReviewing(false);
    }
  }

  const isPending = selected?.reviewStatus === "pending";
  /* 規格調整：機器已刪除（resource_vmid 已清空）就不能核准，後端也會擋 */
  const specResourceGone =
    selected?.source === "spec" && selected?.raw?.resource_exists === false;
  /* 系統寫入的刪除標記（CONSUMED_REQUEST_MARKERS）不是審核人留的備註，不顯示 */
  const rawReviewComment = selected?.raw?.review_comment;
  const reviewNote =
    rawReviewComment && !CONSUMED_REQUEST_MARKERS.includes(rawReviewComment)
      ? rawReviewComment
      : null;

  const visibleRequests = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return requests;
    return requests.filter((request) => {
      const searchable = [
        sourceLabel(request.source, t),
        request.title,
        request.user,
        request.userSubtext,
        request.status,
        request.specText,
        request.timeText,
        request.gpuText,
      ]
        .join(" ")
        .toLowerCase();
      return searchable.includes(q);
    });
  }, [query, requests]);

  /* 批量拒絕：只勾得到看得見的待審申請；換分頁、搜尋或刷新後看不到的自動取消勾選 */
  const selectableIds = visibleRequests
    .filter((request) => request.reviewStatus === "pending")
    .map((request) => request.id);
  const selection = useRowSelection(selectableIds);
  const selectedRequests = visibleRequests.filter((request) => selection.isSelected(request.id));
  const [rejectOpen, setRejectOpen] = useState(false);
  const rejectDialog = useDialogPresence(rejectOpen);
  const [batchProgress, setBatchProgress] = useState(null); // { done, total }

  async function batchReject(reviewComment) {
    const targets = selectedRequests;
    setRejectOpen(false);
    if (!targets.length || batchProgress) return;
    setBatchProgress({ done: 0, total: targets.length });
    const body = { status: "rejected", review_comment: reviewComment };
    const res = await runBatch(
      targets,
      (item) => (item.source === "vm"
        ? VmRequestsService.review(item.rawId, body)
        : SpecChangeRequestsService.review(item.rawId, body)),
      { concurrency: 3, onProgress: ({ done, total }) => setBatchProgress({ done, total }) },
    );
    if (res.failed === 0) {
      toast.success(t("RequestReviewPage.batchRejectedToast", { count: res.succeeded }));
    } else {
      toast.error(t("RequestReviewPage.batchRejectFailedKeptToast", { succeeded: res.succeeded, failed: res.failed }));
    }
    /* 成功的取消勾選，失敗的留著可以直接再試；靜默刷新，清單不閃載入畫面 */
    selection.replace(res.results.filter((r) => !r.ok).map((r) => r.item.id));
    setBatchProgress(null);
    await refreshAll();
  }

  return (
    <div className={styles.page}>
      <PageHeader title={t("RequestReviewPage.pageTitle")} />

      {/* 平常是分頁切換＋搜尋；勾選待審申請後原地換成批量拒絕（同一個高度，清單不會跳） */}
      <DataToolbar
        selecting={selectedRequests.length > 0}
        batch={(
          <BatchBar
            countLabel={t("RequestReviewPage.selectedCount", { count: selectedRequests.length })}
            onClear={selection.clear}
            selectAll={{
              checked: selection.allSelected,
              indeterminate: selection.someSelected,
              onToggle: selection.toggleAll,
            }}
            progress={batchProgress && {
              text: t("RequestReviewPage.batchRejecting", batchProgress),
              done: batchProgress.done,
              total: batchProgress.total,
            }}
          >
            <BatchAction
              icon="block"
              label={t("RequestReviewPage.reject")}
              danger
              onClick={() => setRejectOpen(true)}
            />
          </BatchBar>
        )}
      >
        <SegmentedControl
          className={styles.tabsControl}
          options={tabs.map(({ key, label }) => ({
            value: key,
            label,
            badge: tabCounts[key] ?? 0,
            buttonProps: key === "pending" ? { "data-guide-tab": "request-review-pending" } : undefined,
          }))}
          value={activeTab}
          onChange={changeTab}
          ariaLabel={t("RequestReviewPage.tabsAriaLabel")}
        />
        <ToolbarSearch
          value={query}
          onChange={setQuery}
          placeholder={t("RequestReviewPage.searchPlaceholder")}
        />
      </DataToolbar>

      <div className={styles.content}>
        <div className={styles.reviewGrid}>
          <section className={styles.listPane} data-guide="request-review-list">
            {loading ? (
              <LoadingState text={t("RequestReviewPage.loadingRequests")} />
            ) : error ? (
              <div className={styles.stateBox}>
                <span>{error}</span>
                <button type="button" className={styles.btnSecondary} onClick={() => refreshAll()}>
                  {t("RequestReviewPage.retry")}
                </button>
              </div>
            ) : visibleRequests.length === 0 ? (
              <EmptyState onClearSearch={query.trim() ? () => setQuery("") : undefined} />
            ) : (
              <div className={styles.list}>
                {visibleRequests.map((request) => (
                  /* 勾選框跟開詳情的按鈕並排（按鈕裡不能放勾選框）；「全部」分頁已審過的那幾列留空格對齊 */
                  <div
                    key={request.id}
                    className={`${styles.row} ${selected?.id === request.id ? styles.rowActive : ""} ${selection.isSelected(request.id) ? styles.rowSelected : ""}`}
                  >
                    {selectableIds.length > 0 && (
                      <div className={styles.rowCheck} data-guide="request-review-row-select">
                        {request.reviewStatus === "pending" && (
                          <SelectCheckbox
                            checked={selection.isSelected(request.id)}
                            onToggle={(shiftKey) => selection.toggle(request.id, shiftKey)}
                            ariaLabel={t("RequestReviewPage.selectRowAria", { name: request.title })}
                          />
                        )}
                      </div>
                    )}
                    {/* 勾選狀態下點待審的列改成勾選，不開詳情；已審過的列照舊開詳情 */}
                    <button
                      type="button"
                      className={styles.rowButton}
                      data-guide="request-review-row"
                      onClick={(event) => {
                        if (selection.rowClick(request.id, event)) return;
                        setSelectedId(request.id);
                        setComment("");
                      }}
                    >
                      <div className={styles.rowIcon}>
                        <MIcon name={sourceIcon(request)} size={20} />
                      </div>
                      <div className={styles.rowMain}>
                        <span className={styles.rowName}>{request.title}</span>
                        <span className={styles.rowMeta}>
                          {sourceLabel(request.source, t)}・{request.user}
                        </span>
                      </div>
                      <div className={styles.rowSide}>
                        <StatusBadge status={request.status} />
                        {request.progressText && <span className={styles.rowProgress}>{request.progressText}</span>}
                        <span className={styles.rowTime}>{request.timeText}</span>
                      </div>
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className={styles.detailPane} data-guide="request-review-detail">
            {!selected ? (
              <div className={styles.stateBox}>{t("RequestReviewPage.selectARequest")}</div>
            ) : (
              <>
                <div className={styles.detailScroll} ref={detailScrollRef}>
                  <div className={styles.detailHeader}>
                    <h2>{selected.title}</h2>
                    <p>{selected.user}</p>
                  </div>
  
                  <div className={styles.infoGrid}>
                    <InfoRow label={t("RequestReviewPage.infoLabelType")} value={sourceLabel(selected.source, t)} />
                    <InfoRow label={t("RequestReviewPage.infoLabelSpec")} value={selected.specText} />
                    <InfoRow label={t("RequestReviewPage.infoLabelTime")} value={selected.timeText} />
                    <InfoRow label={selected.paramLabel} value={selected.paramText} />
                    <InfoRow label={t("RequestReviewPage.infoLabelGpu")} value={selected.gpuText} />
                    <InfoRow label={t("RequestReviewPage.infoLabelNode")} value={context?.projected_node || selected.nodeText} />
                  </div>
  
                  <div className={styles.reasonBox}>
                    <span>{t("RequestReviewPage.reasonLabel")}</span>
                    <ExpandableText text={selected.reason} />
                  </div>
  
                  {contextLoading && <LoadingState text={t("RequestReviewPage.loadingContext")} />}
                  {contextError && selected.source === "vm" && (
                    <div className={`${styles.stateBox} ${styles.stateError}`}>
                      {contextError}
                    </div>
                  )}
                  {!isPending && (
                    <>
                      {(reviewNote || selected.reviewedAt) && (
                        <div className={styles.reasonBox}>
                          <span>
                            {t("RequestReviewPage.commentLabel")}
                            {selected.reviewedAt ? t("RequestReviewPage.reviewedAtSuffix", { time: shortTime(selected.reviewedAt, t) }) : ""}
                          </span>
                          <ExpandableText text={reviewNote || t("RequestReviewPage.noReviewNote")} />
                        </div>
                      )}
                      {selected.source === "spec" && selected.raw?.status === "approved" && (
                        <div className={styles.reasonBox}>
                          <span>
                            {t("RequestReviewPage.applyResultLabel")}
                            {selected.raw.applied_at ? t("RequestReviewPage.applyResultAppliedAt", { time: shortTime(selected.raw.applied_at, t) }) : ""}
                          </span>
                          <p>
                            {selected.raw.apply_error
                              || (selected.raw.apply_status === "applied"
                                ? t("RequestReviewPage.applyResultApplied")
                                : selected.raw.apply_status === "applying"
                                  ? t("RequestReviewPage.applyResultApplying")
                                  : t("RequestReviewPage.applyResultAwaiting"))}
                          </p>
                        </div>
                      )}
                    </>
                  )}
                </div>

                {isPending && (
                  <div className={styles.reviewBar} data-guide="request-review-decision">
                    {specResourceGone && (
                      <div className={styles.goneHint}>
                        <span className={styles.doneText}>
                          {t("RequestReviewPage.specResourceGoneHint")}
                        </span>
                      </div>
                    )}
                    <label className={styles.commentField}>
                      <span>{t("RequestReviewPage.commentLabel")}</span>
                      <textarea
                        value={comment}
                        onChange={(event) => setComment(event.target.value)}
                        disabled={reviewing}
                        placeholder={t("RequestReviewPage.commentPlaceholder")}
                      />
                    </label>
                    <div className={styles.rowActions}>
                      <button
                        type="button"
                        className={styles.btnApprove}
                        disabled={reviewing || (selected.source === "vm" && (contextBusy || !context?.feasible)) || specResourceGone}
                        onClick={() => submitReview("approved")}
                      >
                        {t("RequestReviewPage.approve")}
                      </button>
                      <button
                        type="button"
                        className={styles.btnReject}
                        disabled={reviewing}
                        onClick={() => submitReview("rejected")}
                      >
                        {t("RequestReviewPage.reject")}
                      </button>
                    </div>
                  </div>
                )}
              </>
            )}
          </section>
        </div>
      </div>

      {rejectDialog.open && (
        <BatchRejectDialog
          items={selectedRequests}
          closing={rejectDialog.closing}
          onClose={() => setRejectOpen(false)}
          onSubmit={batchReject}
        />
      )}
    </div>
  );
}
