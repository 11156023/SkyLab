import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./RequestReviewPage.module.scss";
import MIcon from "../../../components/MIcon";
import SharedEmptyState from "../../../components/EmptyState/EmptyState";
import { useToast } from "../../../hooks/useToast";
import useAutoRefresh from "../../../hooks/useAutoRefresh";
import LoadingState from "../../../components/LoadingState/LoadingState";
import { SpecChangeRequestsService } from "../../../services/specChangeRequests";
import { VmRequestsService } from "../../../services/vmRequests";
import { CONSUMED_REQUEST_MARKERS } from "../../../services/pendingResources";
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

function specChangeLabel(request, t) {
  const parts = [
    request.requested_cpu
      ? t("RequestReviewPage.specChangeCpu", { from: request.current_cpu ?? "-", to: request.requested_cpu })
      : "",
    request.requested_memory
      ? t("RequestReviewPage.specChangeRam", { from: request.current_memory ?? "-", to: request.requested_memory })
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
  return parts.join(" / ") || request.change_type || "-";
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
    paramText: request.change_type || "-",
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

function pageCount(res) {
  return res?.count ?? res?.data?.length ?? 0;
}

/* 狀態篩選交給後端：清單上限 100 筆且依建立時間新到舊，前端自己篩
   會讓比最新 100 筆還舊的待審申請永遠看不到。規格調整沒有 expired 狀態，
   傳過去會 422，直接視為空。非目前分頁只取筆數（limit=1）給角標用。 */
async function fetchTabPage(key, limit) {
  const status = key === "all" ? undefined : key;
  const [vm, spec] = await Promise.all([
    VmRequestsService.listAll(status, { limit }),
    key === "expired"
      ? EMPTY_PAGE
      : SpecChangeRequestsService.listAll({ status, limit }),
  ]);
  return { vm, spec, count: pageCount(vm) + pageCount(spec) };
}

export default function RequestReviewPage() {
  const { t } = useTranslation("resource");
  const tabs = useTabs();
  const toast = useToast();
  const [activeTab, setActiveTab] = useState("pending");
  const [requests, setRequests] = useState([]);
  const [tabCounts, setTabCounts] = useState({});
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  /* 切換列時詳情欄捲回頂端，避免殘留上一筆的捲動位置 */
  const detailScrollRef = useRef(null);
  useEffect(() => { detailScrollRef.current?.scrollTo({ top: 0 }); }, [selectedId]);
  const [context, setContext] = useState(null);
  const [contextLoading, setContextLoading] = useState(false);
  const [contextError, setContextError] = useState("");
  const [comment, setComment] = useState("");
  const [reviewing, setReviewing] = useState(false);

  /* 不 fallback 到第一筆：背景自動刷新或審核後清單變動時，詳情面板悄悄換人
     會讓審核者對沒看過的申請按下核准 */
  const selected = useMemo(
    () => requests.find((request) => request.id === selectedId) ?? null,
    [requests, selectedId],
  );

  /* 只採用最後一次發出的請求：切分頁、審核後重新載入時，較早送出卻較晚回來的
     舊回應（別的分頁、審核前的快照）不能蓋掉新清單 */
  const fetchSeqRef = useRef(0);
  const foregroundLoadingRef = useRef(false);

  /** silent = true 時不觸發 loading 與錯誤提示，供背景自動刷新使用 */
  const fetchRequests = useCallback(async (tab = activeTab, silent = false) => {
    // 前景載入進行中就不另發背景刷新，否則前景那次會被當成舊回應丟掉、loading 收不掉
    if (silent && foregroundLoadingRef.current) return;
    const seq = ++fetchSeqRef.current;
    const isLatest = () => seq === fetchSeqRef.current;
    if (!silent) {
      foregroundLoadingRef.current = true;
      setLoading(true);
      setError("");
    }
    try {
      const pages = await Promise.all(
        TAB_KEYS.map((key) => fetchTabPage(key, key === tab ? (key === "pending" ? PENDING_LIST_LIMIT : LIST_LIMIT) : 1)),
      );
      if (!isLatest()) return;
      const active = pages[TAB_KEYS.indexOf(tab)] ?? { vm: EMPTY_PAGE, spec: EMPTY_PAGE };
      const items = [
        ...(active.vm?.data ?? []).map((r) => normalizeVmRequest(r, t)),
        ...(active.spec?.data ?? []).map((r) => normalizeSpecRequest(r, t)),
      ].sort(
        (a, b) =>
          new Date(b.createdAt || 0).getTime() - new Date(a.createdAt || 0).getTime(),
      );
      setTabCounts(Object.fromEntries(TAB_KEYS.map((key, i) => [key, pages[i].count])));
      setRequests(items);
      /* 目前那筆不在清單裡了就清空選取，不 fallback 到別筆（理由見 selected 上方） */
      setSelectedId((current) =>
        current && items.some((item) => item.id === current) ? current : null,
      );
    } catch (err) {
      if (!silent && isLatest()) {
        setRequests([]);
        setTabCounts({});
        setSelectedId(null);
        setError(err?.message ?? t("RequestReviewPage.loadRequestsFailed"));
      }
    } finally {
      if (!silent && isLatest()) {
        foregroundLoadingRef.current = false;
        setLoading(false);
      }
    }
  }, [activeTab, t]);

  useEffect(() => {
    fetchRequests(activeTab);
    setComment("");
  }, [activeTab, fetchRequests]);

  useAutoRefresh(() => fetchRequests(activeTab, true));

  /* 選取換人（含背景刷新讓原本那筆消失）時備註跟著清空，不能帶到別筆申請上 */
  useEffect(() => { setComment(""); }, [selectedId]);

  useEffect(() => {
    if (
      !selected?.rawId ||
      selected.source !== "vm" ||
      selected.reviewStatus !== "pending"
    ) {
      setContext(null);
      setContextError("");
      setContextLoading(false);
      return;
    }

    let cancelled = false;
    setContextLoading(true);
    setContextError("");
    setContext(null);
    VmRequestsService.getReviewContext(selected.rawId)
      .then((res) => {
        if (!cancelled) setContext(res);
      })
      .catch((err) => {
        if (!cancelled) setContextError(err?.message ?? t("RequestReviewPage.loadContextFailed"));
      })
      .finally(() => {
        if (!cancelled) setContextLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selected?.rawId, selected?.source, selected?.reviewStatus]);

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
      await fetchRequests(activeTab);
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
    await fetchRequests(activeTab, true);
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
          onChange={setActiveTab}
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
                <button type="button" className={styles.btnSecondary} onClick={() => fetchRequests(activeTab)}>
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
                      <div className={styles.rowActions}>
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
                        disabled={reviewing || (selected.source === "vm" && (contextLoading || !context?.feasible)) || specResourceGone}
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
