import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../../contexts/AuthContext";
import styles from "./RequestsPage.module.scss";
import i18n from "../../../i18n";
import { VmRequestsService } from "../../../services/vmRequests";
import { CONSUMED_REQUEST_MARKERS, isConsumedRequest } from "../../../services/pendingResources";
import {
  SpecChangeRequestsService,
  canApplySpecRequest,
  canCancelSpecRequest,
  specRequestChangeLabel,
  specRequestDisplayStatus,
} from "../../../services/specChangeRequests";
import { useToast } from "../../../hooks/useToast";
import useAutoRefresh from "../../../hooks/useAutoRefresh";
import RequestFormPage from "./RequestFormPage";
import MIcon from "../../../components/MIcon";
import SharedEmptyState from "../../../components/EmptyState/EmptyState";
import LoadingState from "../../../components/LoadingState/LoadingState";
import PageHeader from "../../../components/PageHeader/PageHeader";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import * as fmt from "../../../utils/formatDate";

/* ── Constants ── */
const defaultT = (key) => i18n.t(key, { ns: "personal" });

const STATUS_MAP = {
  pending:   { labelKey: "RequestsPage.statusPending",   color: "info"    },
  approved:  { labelKey: "RequestsPage.statusApproved",  color: "success" },
  rejected:  { labelKey: "RequestsPage.statusRejected",  color: "danger"  },
  cancelled: { labelKey: "RequestsPage.statusCancelled", color: "muted"   },
  expired:   { labelKey: "RequestsPage.statusExpired",   color: "muted"   },
};

const RESOURCE_TYPE_MAP = {
  lxc: { labelKey: "RequestsPage.typeLxc", icon: "terminal" },
  vm:  { labelKey: "RequestsPage.typeVm", icon: "computer" },
};

/* 開通成功後 VMRequest.status 仍停留在 approved（後端只把 vmid 寫回），
   所以「重試／撤銷」必須同時看 vmid：vmid 已存在代表機器已開出來，
   重試會把使用者關機的 VM 重新開機、撤銷會讓 request 與活著的資源脫鉤。 */
function canRetry(req) {
  return (
    req.status === "approved" &&
    req.vmid == null &&
    req.provisioning_status === "failed"
  );
}

function canCancel(req) {
  return (
    req.status === "pending" ||
    (req.status === "approved" && req.vmid == null)
  );
}

/* 機器已建立（vmid 已寫回）但排程器後續維運失敗：
   後端 retry 會拒絕這種狀態，只能到「我的資源」操作或刪除該機器 */
function isProvisionedButFailed(req) {
  return (
    req.status === "approved" &&
    req.vmid != null &&
    req.provisioning_status === "failed"
  );
}
/* 機器已建立但暫時開不了機（如 GPU 記憶體不足），排程器持續重試中 */
function isWaitingForResources(req) {
  return (
    req.status === "approved" &&
    req.vmid != null &&
    req.provisioning_status !== "failed" &&
    Boolean(req.resource_warning)
  );
}

/* approved 在 UI 上再依開通進度細分（vmid 為空時 provisioning_status 反映開通流程） */
function getDisplayStatus(req, t = defaultT) {
  if (req.status === "approved") {
    if (req.vmid != null) {
      if (req.provisioning_status === "failed") return { label: t("RequestsPage.statusMachineError"), color: "danger" };
      if (isWaitingForResources(req)) return { label: t("RequestsPage.statusWaitingResources"), color: "warning" };
      return { label: t("RequestsPage.statusProvisioned"), color: "success" };
    }
    if (req.provisioning_status === "failed") return { label: t("RequestsPage.statusProvisionFailed"), color: "danger" };
    if (req.provisioning_status === "running") return { label: t("RequestsPage.statusProvisioning"), color: "info" };
    return { label: t("RequestsPage.statusApproved"), color: "success" };
  }
  const mapped = STATUS_MAP[req.status];
  return mapped ? { label: t(mapped.labelKey), color: mapped.color } : { label: req.status, color: "muted" };
}

const VIEW_LIST   = "list";
const VIEW_CREATE = "create";

const LIST_COLUMN_KEYS = [
  "RequestsPage.colResource",
  "RequestsPage.colOs",
  "RequestsPage.fieldAccount",
  "RequestsPage.colSpec",
  "RequestsPage.colGpu",
  "RequestsPage.colReason",
  "RequestsPage.colRequestedAt",
  "RequestsPage.colPeriod",
  "RequestsPage.colStatus",
  "RequestsPage.colActions",
];

const SPEC_COLUMN_KEYS = [
  "RequestsPage.specColMachine",
  "RequestsPage.specColChange",
  "RequestsPage.colReason",
  "RequestsPage.colRequestedAt",
  "RequestsPage.colStatus",
  "RequestsPage.colActions",
];
/* 套用中（關機 → 改規格 → 開機）約 1～3 分鐘，比 30 秒自動刷新更勤地跟進度 */
const SPEC_APPLY_POLL_MS = 5000;
/* 系統寫入 review_comment 的撤銷標記，不是審核人留言 */
const SPEC_CANCEL_MARKERS = ["Cancelled by requester", "Cancelled by admin"];

/* ── Helpers ── */
function formatDatetime(isoStr) {
  return fmt.formatDateTime(isoStr, null);
}

function formatDate(isoStr) {
  return fmt.formatDate(isoStr);
}

function getOsDisplay(req) {
  if (req.os_info) return req.os_info;
  if (req.ostemplate) {
    const filename = req.ostemplate.split("/").pop() ?? req.ostemplate;
    return filename.replace(/\.tar\.\w+$/, "").replace(/\.tar$/, "");
  }
  return null;
}

function getMemDisplay(memMB) {
  if (memMB % 1024 === 0) return `${memMB / 1024} GB`;
  return `${(memMB / 1024).toFixed(1)} GB`;
}

/* ── Primitive sub-components ── */
function StatusBadge({ req }) {
  const { t } = useTranslation("personal");
  const s = getDisplayStatus(req, t);
  return (
    <span className={`${styles.badge} ${styles[`badge_${s.color}`]}`}>
      {s.label}
    </span>
  );
}

function InfoRow({ icon, label, value }) {
  if (!value) return null;
  return (
    <div className={styles.infoRow}>
      <span className={styles.infoLabel}>
        <MIcon name={icon} size={12} />
        {label}
      </span>
      <span className={styles.infoValue}>{value}</span>
    </div>
  );
}

function getSpecDisplay(req, t = defaultT) {
  return t("RequestsPage.specDisplay", { cores: req.cores, mem: getMemDisplay(req.memory), storage: req.storage });
}


/* ── Error Log Modal ── */
/* 管理員限定：原始開通錯誤 log 太長，不進表格也不進展開列，點狀態旁圖示開窗看 */
function ErrorLogModal({ req, onClose }) {
  const { t } = useTranslation("personal");
  const toast = useToast();
  const [closing, setClosing] = useState(false);

  function close() {
    if (closing) return;
    setClosing(true);
  }

  function handleAnimationEnd() {
    if (closing) onClose();
  }

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(req.provisioning_error);
      toast.success(t("ErrorLogModal.copied"));
    } catch {
      toast.error(t("ErrorLogModal.copyFailed"));
    }
  }

  /* 同 ConfirmModal：portal 到 body 逃離 .tableWrap 的 backdrop-filter containing block */
  return createPortal(
    <div
      className={`${styles.modalOverlay} ${closing ? styles.modalOverlayOut : ""}`}
      onClick={close}
      onAnimationEnd={handleAnimationEnd}
    >
      <div className={`${styles.modal} ${styles.logModal}`} onClick={(e) => e.stopPropagation()}>
        <span className={styles.modalTitle}>{t("ErrorLogModal.title", { hostname: req.hostname })}</span>
        {isProvisionedButFailed(req) && (
          <p className={styles.modalDesc}>{t("ErrorLogModal.machineFailDesc")}</p>
        )}
        <pre className={styles.logText}>{req.provisioning_error}</pre>
        <div className={styles.modalActions}>
          <button type="button" className={styles.btnSecondary} onClick={handleCopy}>
            <MIcon name="content_copy" size={14} />
            {t("ErrorLogModal.copy")}
          </button>
          <button type="button" className={styles.btnPrimary} onClick={close}>
            {t("ErrorLogModal.close")}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}

/* ── RequestRow ── */
function RequestRow({ req, onUpdated }) {
  const { t } = useTranslation("personal");
  const toast = useToast();
  const navigate = useNavigate();
  const { user } = useAuth();
  /* VMID 是系統內部編號，僅管理員／老師看得到 */
  const showVmid = user?.is_superuser || user?.role === "admin" || user?.role === "teacher";
  /* 原始開通錯誤 log 是給管理員除錯用的，學生／老師只看狀態與操作 */
  const isAdmin = user?.is_superuser || user?.role === "admin";
  const confirm = useConfirm();
  const [cancelling, setCancelling]       = useState(false);
  const [retrying, setRetrying]           = useState(false);
  const [logOpen, setLogOpen]             = useState(false);

  const type      = RESOURCE_TYPE_MAP[req.resource_type] ?? { label: req.resource_type, icon: "computer" };
  const osDisplay = getOsDisplay(req);
  const startFmt  = formatDatetime(req.start_at);
  const endFmt    = formatDatetime(req.end_at);

  const showRejection = req.status === "rejected" && req.review_comment;
  const showFailureLog =
    isAdmin && (canRetry(req) || isProvisionedButFailed(req)) && req.provisioning_error;
  const showWaiting = isWaitingForResources(req);
  const hasAction = canRetry(req) || canCancel(req) || isProvisionedButFailed(req);

  async function handleCancel() {
    const ok = await confirm({
      title: t("RequestRow.confirmCancelTitle"),
      message: t("RequestRow.confirmCancelDesc", { hostname: req.hostname }),
      confirmText: t("RequestRow.confirmCancelLabel"),
      danger: true,
    });
    if (!ok) return;
    setCancelling(true);
    try {
      const updated = await VmRequestsService.cancel(req.id);
      onUpdated(updated);
      toast.success(t("RequestRow.cancelSuccess", { hostname: req.hostname }));
    } catch (err) {
      toast.error(err?.message ?? t("RequestRow.cancelFailed"));
    } finally {
      setCancelling(false);
    }
  }

  async function handleRetry() {
    setRetrying(true);
    try {
      const updated = await VmRequestsService.retry(req.id);
      onUpdated(updated);
      toast.success(t("RequestRow.retrySuccess"));
    } catch (err) {
      toast.error(err?.message ?? t("RequestRow.retryFailed"));
    } finally {
      setRetrying(false);
    }
  }

  return (
    <>
      <tr className={styles.tr}>
        <td className={styles.td}>
          <div className={styles.nameCell}>
            <div className={styles.nameIcon}>
              <MIcon name={type.icon} size={18} />
            </div>
            <div className={styles.nameMeta}>
              <span className={styles.namePrimary} title={req.hostname}>{req.hostname}</span>
              <span className={styles.nameSub}>
                {t(type.labelKey ?? type.label)}
                {showVmid && req.vmid != null && t("RequestRow.numberSuffix", { vmid: req.vmid })}
              </span>
            </div>
          </div>
        </td>
        <td className={styles.td}>
          <span className={styles.osCell} title={osDisplay ?? undefined}>{osDisplay ?? "—"}</span>
        </td>
        <td className={styles.td}>
          <span className={styles.osCell} title={req.username ?? undefined}>{req.username ?? "—"}</span>
        </td>
        <td className={styles.td}>
          <span className={styles.specCell}>{getSpecDisplay(req, t)}</span>
        </td>
        <td className={styles.td}>
          <span className={styles.osCell} title={req.gpu_mapping_id ?? undefined}>{req.gpu_mapping_id ?? "—"}</span>
        </td>
        <td className={styles.td}>
          <span className={styles.reasonCell} title={req.reason || undefined}>
            {req.reason || "—"}
          </span>
        </td>
        <td className={styles.td}>{formatDate(req.created_at)}</td>
        <td className={styles.td}>
          {startFmt ? (
            <div className={styles.periodCell}>
              <span>{startFmt}</span>
              {endFmt && <span>~ {endFmt}</span>}
            </div>
          ) : (
            <span className={styles.periodCell}>—</span>
          )}
        </td>
        <td className={styles.td}>
          <div className={styles.statusCell}>
            <StatusBadge req={req} />
            {/* 展開明細已移除：退件備註／等待資源說明改掛在小圖示的 tooltip */}
            {showRejection && (
              <span className={styles.statusNote} title={req.review_comment}>
                <MIcon name="comment" size={14} />
              </span>
            )}
            {showWaiting && (
              <span className={styles.statusNote} title={req.resource_warning}>
                <MIcon name="hourglass_empty" size={14} />
              </span>
            )}
            {showFailureLog && (
              <button
                type="button"
                className={styles.logBtn}
                title={t("RequestRow.viewErrorLog")}
                aria-label={t("RequestRow.viewErrorLog")}
                onClick={() => setLogOpen(true)}
              >
                <MIcon name="receipt_long" size={14} />
              </button>
            )}
          </div>
        </td>
        <td className={styles.td}>
          <div className={styles.rowActions}>
            {!hasAction && <span className={styles.emptyAction}>—</span>}
            {canRetry(req) && (
              <button type="button" className={styles.retryBtn} disabled={retrying} onClick={handleRetry}>
                <MIcon name="refresh" size={13} />
                {retrying ? "…" : t("RequestRow.retry")}
              </button>
            )}
            {canCancel(req) && (
              <button type="button" className={styles.cancelBtn} disabled={cancelling} onClick={handleCancel}>
                <MIcon name="close" size={13} />
                {t("RequestRow.cancelRequest")}
              </button>
            )}
            {isProvisionedButFailed(req) && (
              <button type="button" className={styles.retryBtn} onClick={() => navigate("/my-resources")}>
                <MIcon name="inventory_2" size={13} />
                {t("RequestRow.goToResources")}
              </button>
            )}
          </div>
        </td>
      </tr>

      {logOpen && <ErrorLogModal req={req} onClose={() => setLogOpen(false)} />}
    </>
  );
}

/* ── 規格調整申請列 ── */
function SpecRequestRow({ req, onUpdated }) {
  const { t } = useTranslation("personal");
  const toast = useToast();
  const { user } = useAuth();
  const showVmid = user?.is_superuser || user?.role === "admin" || user?.role === "teacher";
  const confirm = useConfirm();
  const [expanded, setExpanded]           = useState(false);
  const [busy, setBusy]                   = useState(false);

  const display     = specRequestDisplayStatus(req);
  const statusLabel = display.labelKey ? t(display.labelKey) : display.key;
  const showApply   = canApplySpecRequest(req);
  const showCancel  = canCancelSpecRequest(req);
  const hasAction   = showApply || showCancel;
  /* 機器刪除時系統會把處理中的申請自動取消，備註是系統標記不是審核人留言 */
  const deletedByMachine = CONSUMED_REQUEST_MARKERS.includes(req.review_comment);
  const reviewNote =
    !deletedByMachine && req.review_comment && !SPEC_CANCEL_MARKERS.includes(req.review_comment)
      ? req.review_comment
      : null;
  const applyNote = req.apply_error || null;
  const appliedAt = formatDatetime(req.applied_at);
  const hasDetail = Boolean(reviewNote || applyNote || deletedByMachine || appliedAt);

  async function handleApply() {
    const ok = await confirm({
      title: t("SpecRequestRow.confirmApplyTitle"),
      message: t("SpecRequestRow.confirmApplyDesc"),
      confirmText: t("SpecRequestRow.confirmApplyLabel"),
    });
    if (!ok) return;
    setBusy(true);
    try {
      const res = await SpecChangeRequestsService.apply(req.id);
      onUpdated(res.request);
      toast.success(t("SpecRequestRow.applyStarted"));
    } catch (err) {
      toast.error(err?.message ?? t("SpecRequestRow.applyFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function handleCancel() {
    const ok = await confirm({
      title: t("SpecRequestRow.confirmCancelTitle"),
      message: t("SpecRequestRow.confirmCancelDesc"),
      confirmText: t("SpecRequestRow.confirmCancelLabel"),
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      const updated = await SpecChangeRequestsService.cancel(req.id);
      onUpdated(updated);
      toast.success(t("SpecRequestRow.cancelSuccess"));
    } catch (err) {
      toast.error(err?.message ?? t("SpecRequestRow.cancelFailed"));
    } finally {
      setBusy(false);
    }
  }

  const machineName = req.resource_name || t("SpecRequestRow.machineFallback", { vmid: req.vmid });

  return (
    <>
      <tr
        className={`${styles.tr} ${hasDetail ? styles.trClickable : ""} ${expanded ? styles.trExpanded : ""}`}
        onClick={hasDetail ? (event) => {
          if (event.target.closest("button")) return;
          setExpanded((v) => !v);
        } : undefined}
      >
        <td className={styles.td}>
          <div className={styles.nameCell}>
            {hasDetail ? (
              <button
                type="button"
                className={styles.expandBtn}
                aria-expanded={expanded}
                aria-label={expanded ? t("RequestRow.collapseDetails") : t("RequestRow.expandDetails")}
                onClick={() => setExpanded((v) => !v)}
              >
                <MIcon name={expanded ? "expand_more" : "chevron_right"} size={16} />
              </button>
            ) : (
              <span className={styles.expandPlaceholder} aria-hidden="true" />
            )}
            <div className={styles.nameIcon}>
              <MIcon name="tune" size={18} />
            </div>
            <div className={styles.nameMeta}>
              <span className={styles.namePrimary} title={machineName}>{machineName}</span>
              <span className={styles.nameSub}>
                {t("SpecRequestRow.kindLabel")}
                {showVmid && t("RequestRow.numberSuffix", { vmid: req.vmid })}
              </span>
            </div>
          </div>
        </td>
        <td className={styles.td}>
          <span className={styles.specCell}>{specRequestChangeLabel(req, t)}</span>
        </td>
        <td className={styles.td}>
          <span className={styles.reasonCell} title={req.reason || undefined}>
            {req.reason || "—"}
          </span>
        </td>
        <td className={styles.td}>{formatDate(req.created_at)}</td>
        <td className={styles.td}>
          <span className={`${styles.badge} ${styles[`badge_${display.color}`]}`}>{statusLabel}</span>
        </td>
        <td className={styles.td}>
          <div className={styles.rowActions}>
            {!hasAction && <span className={styles.emptyAction}>—</span>}
            {showApply && (
              <button type="button" className={styles.applyBtn} disabled={busy} onClick={handleApply}>
                <MIcon name="play_arrow" size={13} />
                {display.key === "ready" ? t("SpecRequestRow.apply") : t("SpecRequestRow.reapply")}
              </button>
            )}
            {showCancel && (
              <button type="button" className={styles.cancelBtn} disabled={busy} onClick={handleCancel}>
                <MIcon name="close" size={13} />
                {t("SpecRequestRow.cancel")}
              </button>
            )}
          </div>
        </td>
      </tr>

      {expanded && (
        <tr className={styles.detailTr}>
          <td className={styles.detailTd} colSpan={SPEC_COLUMN_KEYS.length}>
            <div className={styles.detailBody}>
              <InfoRow icon="event_available" label={t("SpecRequestRow.appliedAtLabel")} value={appliedAt} />
              {reviewNote && (
                <div className={styles.reviewComment}>
                  <MIcon name="comment" size={13} />
                  <span>{reviewNote}</span>
                </div>
              )}
              {deletedByMachine && (
                <div className={styles.reviewComment}>
                  <MIcon name="info" size={13} />
                  <span>{t("SpecRequestRow.deletedNote")}</span>
                </div>
              )}
              {applyNote && (
                <div className={styles.reviewComment}>
                  <MIcon name={req.applied_at ? "warning" : "error_outline"} size={13} />
                  <span>{applyNote}</span>
                </div>
              )}
            </div>
          </td>
        </tr>
      )}

    </>
  );
}

/* ── Empty / Error states ── */
function EmptyState({ onCreateClick }) {
  const { t } = useTranslation("personal");
  return (
    <SharedEmptyState
      icon="description"
      title={t("RequestsPage.emptyTitle")}
      action={
        <button type="button" className={styles.btnPrimary} onClick={onCreateClick}>
          <MIcon name="add" size={16} />
          {t("RequestsPage.createNow")}
        </button>
      }
    />
  );
}

function ErrorState({ onRetry }) {
  const { t } = useTranslation("personal");
  return (
    <EmptyState
      icon="error_outline"
      title={t("RequestsPage.errorTitle")}
      action={
        <button type="button" className={styles.btnSecondary} onClick={onRetry}>
          <MIcon name="refresh" size={16} />
          {t("RequestsPage.retry")}
        </button>
      }
    />
  );
}

/* ── Page ── */
export default function RequestsPage() {
  const { t } = useTranslation("personal");
  /* 其他頁（如快速建立的「完整設定」）可用 navigate("/my-requests", { state: { create: true } }) 直接開表單 */
  const location = useLocation();
  const [requests, setRequests] = useState([]);
  const [specRequests, setSpecRequests] = useState([]);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState(false);
  const [view, setView]         = useState(location.state?.create ? VIEW_CREATE : VIEW_LIST);
  const [returning, setReturning] = useState(false);
  /* AI 助手談完需求後會把推薦配置一起帶過來 */
  const [pendingPrefill, setPendingPrefill] = useState(location.state?.prefill ?? null);

  /** silent = true 時不觸發 loading / error state，供背景自動刷新使用 */
  const fetchRequests = useCallback(async (silent = false) => {
    if (!silent) {
      setLoading(true);
      setError(false);
    }
    try {
      const [res, specRes] = await Promise.all([
        VmRequestsService.list(),
        /* 規格調整申請載入失敗不拖垮主列表 */
        SpecChangeRequestsService.listMy().catch(() => null),
      ]);
      // 機器已被刪除／轉範本的申請單只留做稽核，不顯示
      setRequests((res.data ?? []).filter((r) => !isConsumedRequest(r)));
      if (specRes) setSpecRequests(specRes.data ?? []);
    } catch {
      if (!silent) setError(true);
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (view === "list") fetchRequests();
  }, [view, fetchRequests]);

  /* 已經停在本頁時 useState 的初值不會再跑一次，所以導覽助手在這頁按步驟
     只會換掉 location.state。兩個方向都要跟：帶 create 就開表單，沒帶就回列表
     （流程裡的「等待審核」指的就是列表，按了不能沒反應）。 */
  useEffect(() => {
    setView(location.state?.create ? VIEW_CREATE : VIEW_LIST);
    if (location.state?.prefill) setPendingPrefill(location.state.prefill);
  }, [location.key, location.state?.create, location.state?.prefill]);

  useAutoRefresh(() => {
    if (view === "list") fetchRequests(true);
  });

  const specApplying = specRequests.some((r) => r.apply_status === "applying");
  useEffect(() => {
    if (view !== VIEW_LIST || !specApplying) return undefined;
    const timer = setInterval(() => fetchRequests(true), SPEC_APPLY_POLL_MS);
    return () => clearInterval(timer);
  }, [view, specApplying, fetchRequests]);

  function handleUpdated(updated) {
    setRequests((prev) => prev.map((r) => r.id === updated.id ? updated : r));
  }

  function handleSpecUpdated(updated) {
    setSpecRequests((prev) => prev.map((r) => r.id === updated.id ? updated : r));
  }

  if (view === VIEW_CREATE) {
    return (
      <RequestFormPage
        key="create"
        className={styles.animSlideInRight}
        initialPrefill={pendingPrefill}
        onBack={() => { setReturning(true); setView(VIEW_LIST); setPendingPrefill(null); }}
      />
    );
  }

  return (
    <div
      className={`${styles.page} ${returning ? styles.animSlideInLeft : ""}`}
      onAnimationEnd={returning ? () => setReturning(false) : undefined}
    >
      <PageHeader title={t("RequestsPage.title")} subtitle={t("RequestsPage.subtitle")}>
        <button type="button" className={styles.btnPrimary} onClick={() => setView(VIEW_CREATE)} data-guide="request-create">
          <MIcon name="add" size={16} />
          {t("RequestsPage.requestResource")}
        </button>
      </PageHeader>

      <div className={styles.content} data-guide="request-list">
        {error ? (
          <ErrorState onRetry={fetchRequests} />
        ) : loading ? (
          <LoadingState fullPage />
        ) : requests.length === 0 && specRequests.length === 0 ? (
          <EmptyState onCreateClick={() => setView(VIEW_CREATE)} />
        ) : (
          <>
            {requests.length > 0 && (
              <div className={styles.tableWrap}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      {LIST_COLUMN_KEYS.map((columnKey, idx) => (
                        <th key={columnKey} className={styles.th}>{t(columnKey)}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {requests.map((r) => (
                      <RequestRow key={r.id} req={r} onUpdated={handleUpdated} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {specRequests.length > 0 && (
              <section className={styles.subSection}>
                <h2 className={styles.sectionTitle}>{t("RequestsPage.specSectionTitle")}</h2>
                <p className={styles.sectionDesc}>{t("RequestsPage.specSectionDesc")}</p>
                <div className={styles.tableWrap}>
                  <table className={styles.table}>
                    <thead>
                      <tr>
                        {SPEC_COLUMN_KEYS.map((columnKey, idx) => (
                          <th key={columnKey} className={idx === 0 ? `${styles.th} ${styles.thName}` : styles.th}>{t(columnKey)}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {specRequests.map((r) => (
                        <SpecRequestRow key={r.id} req={r} onUpdated={handleSpecUpdated} />
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}
          </>
        )}
      </div>
    </div>
  );
}
