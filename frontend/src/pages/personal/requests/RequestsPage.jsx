import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { createColumnHelper, useTable } from "@tanstack/react-table";
import { useAuth } from "../../../contexts/AuthContext";
import styles from "./RequestsPage.module.scss";
import DataTable, { DataTableCells } from "../../../components/DataTable/DataTable";
import { baseFeatures } from "../../../components/DataTable/features";
import { VmRequestsService } from "../../../services/vmRequests";
import { CONSUMED_REQUEST_MARKERS } from "../../../services/pendingResources";
import {
  SpecChangeRequestsService,
  canApplySpecRequest,
  canCancelSpecRequest,
  specRequestChangeLabel,
  specRequestDisplayStatus,
} from "../../../services/specChangeRequests";
import { useMyActiveVmRequestsQuery, useVmRequestCacheUpdater } from "../../../queries/vmRequests";
import { isApplying, useMySpecChangeRequestsQuery, useSpecChangeRequestCacheUpdater } from "../../../queries/specChangeRequests";
import { useToast } from "../../../hooks/useToast";
import useDialogPresence from "../../../hooks/useDialogPresence";
import { useQueryView } from "../../../hooks/useQueryLoadState";
import RequestFormPage from "./RequestFormPage";
import MIcon from "../../../components/MIcon";
import Modal from "../../../components/Modal/Modal";
import SharedEmptyState from "../../../components/EmptyState/EmptyState";
import ErrorState from "../../../components/ErrorState/ErrorState";
import LoadingState from "../../../components/LoadingState/LoadingState";
import PageHeader from "../../../components/PageHeader/PageHeader";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { formatDate, formatDateTime } from "../../../utils/formatDate";
import { canTeachUser, isAdminUser } from "../../../utils/roles";

/* ── Constants ── */
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
function getDisplayStatus(req, t) {
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

/* 系統寫入 review_comment 的撤銷標記，不是審核人留言 */
const SPEC_CANCEL_MARKERS = ["Cancelled by requester", "Cancelled by admin"];

/* ── Helpers ── */
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

function getSpecDisplay(req, t) {
  return t("RequestsPage.specDisplay", { cores: req.cores, mem: getMemDisplay(req.memory), storage: req.storage });
}


/* ── Error Log Modal ── */
/* 管理員限定：原始開通錯誤 log 太長，不進表格也不進展開列，點狀態旁圖示開窗看 */
function ErrorLogModal({ req, closing, onClose }) {
  const { t } = useTranslation("personal");
  const toast = useToast();

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(req.provisioning_error);
      toast.success(t("ErrorLogModal.copied"));
    } catch {
      toast.error(t("ErrorLogModal.copyFailed"));
    }
  }

  return (
    <Modal
      size="log"
      closing={closing}
      onClose={onClose}
      title={t("ErrorLogModal.title", { hostname: req.hostname })}
      description={isProvisionedButFailed(req) ? t("ErrorLogModal.machineFailDesc") : undefined}
      actions={
        <>
          <button type="button" className={styles.btnSecondary} onClick={handleCopy}>
            <MIcon name="content_copy" size={14} />
            {t("ErrorLogModal.copy")}
          </button>
          <button type="button" className={styles.btnPrimary} onClick={onClose}>
            {t("ErrorLogModal.close")}
          </button>
        </>
      }
    >
      <pre className={styles.logText}>{req.provisioning_error}</pre>
    </Modal>
  );
}

/* ── 兩張表共用的儲存格／表頭（cell 從 rowState.req 取申請單，列資料換了才會跟著重繪） ── */
function RequestHeader({ column }) {
  const { t } = useTranslation("personal");
  return t(column.columnDef.meta.labelKey);
}

function RequestReasonCell({ rowState }) {
  const { req } = rowState;
  return (
    <span className={styles.reasonCell} title={req.reason || undefined}>
      {req.reason || "—"}
    </span>
  );
}

function RequestCreatedCell({ rowState }) {
  return formatDate(rowState.req.created_at);
}

/* ── VM 申請表的儲存格 ── */
function RequestResourceCell({ rowState }) {
  const { t } = useTranslation("personal");
  const { req, showVmid } = rowState;
  const type = RESOURCE_TYPE_MAP[req.resource_type] ?? { label: req.resource_type, icon: "computer" };
  return (
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
  );
}

function RequestOsCell({ rowState }) {
  const osDisplay = getOsDisplay(rowState.req);
  return <span className={styles.osCell} title={osDisplay ?? undefined}>{osDisplay ?? "—"}</span>;
}

function RequestAccountCell({ rowState }) {
  const { username } = rowState.req;
  return <span className={styles.osCell} title={username ?? undefined}>{username ?? "—"}</span>;
}

function RequestSpecCell({ rowState }) {
  const { t } = useTranslation("personal");
  return <span className={styles.specCell}>{getSpecDisplay(rowState.req, t)}</span>;
}

function RequestGpuCell({ rowState }) {
  const gpu = rowState.req.gpu_mapping_id;
  return <span className={styles.osCell} title={gpu ?? undefined}>{gpu ?? "—"}</span>;
}

function RequestPeriodCell({ rowState }) {
  const { req } = rowState;
  /* 無值時回 null（不是「—」），依此決定要不要顯示時段 */
  const startFmt = formatDateTime(req.start_at, null);
  const endFmt   = formatDateTime(req.end_at, null);
  return startFmt ? (
    <div className={styles.periodCell}>
      <span>{startFmt}</span>
      {endFmt && <span>~ {endFmt}</span>}
    </div>
  ) : (
    <span className={styles.periodCell}>—</span>
  );
}

function RequestStatusCell({ rowState }) {
  const { t } = useTranslation("personal");
  const { req, showFailureLog, onOpenLog } = rowState;
  const showRejection = req.status === "rejected" && req.review_comment;
  const showWaiting = isWaitingForResources(req);
  return (
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
          onClick={onOpenLog}
        >
          <MIcon name="receipt_long" size={14} />
        </button>
      )}
    </div>
  );
}

function RequestActionsCell({ rowState }) {
  const { t } = useTranslation("personal");
  const { req, cancelling, retrying, onRetry, onCancel, onGoToResources } = rowState;
  const hasAction = canRetry(req) || canCancel(req) || isProvisionedButFailed(req);
  return (
    <div className={styles.rowActions}>
      {!hasAction && <span className={styles.emptyAction}>—</span>}
      {canRetry(req) && (
        <button type="button" className={styles.retryBtn} disabled={retrying} onClick={onRetry}>
          <MIcon name="refresh" size={13} spin={retrying} />
          {retrying ? "…" : t("RequestRow.retry")}
        </button>
      )}
      {canCancel(req) && (
        <button type="button" className={styles.cancelBtn} disabled={cancelling} onClick={onCancel}>
          <MIcon name="close" size={13} />
          {t("RequestRow.cancelRequest")}
        </button>
      )}
      {isProvisionedButFailed(req) && (
        <button type="button" className={styles.retryBtn} onClick={onGoToResources}>
          <MIcon name="inventory_2" size={13} />
          {t("RequestRow.goToResources")}
        </button>
      )}
    </div>
  );
}

/* ── 規格調整申請表的儲存格 ── */
function SpecMachineCell({ rowState }) {
  const { t } = useTranslation("personal");
  const { req, showVmid } = rowState;
  const machineName = req.resource_name || t("SpecRequestRow.machineFallback", { vmid: req.vmid });
  return (
    <div className={styles.nameCell}>
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
  );
}

function SpecChangeCell({ rowState }) {
  const { t } = useTranslation("personal");
  return <span className={styles.specCell}>{specRequestChangeLabel(rowState.req, t)}</span>;
}

function SpecAppliedCell({ rowState }) {
  return <span className={styles.dateTimeCell}>{formatDateTime(rowState.req.applied_at)}</span>;
}

function SpecStatusCell({ rowState }) {
  const { t } = useTranslation("personal");
  const { req } = rowState;
  const display     = specRequestDisplayStatus(req);
  const statusLabel = display.labelKey ? t(display.labelKey) : display.key;
  /* 機器刪除時系統會把處理中的申請自動取消，備註是系統標記不是審核人留言 */
  const deletedByMachine = CONSUMED_REQUEST_MARKERS.includes(req.review_comment);
  const reviewNote =
    !deletedByMachine && req.review_comment && !SPEC_CANCEL_MARKERS.includes(req.review_comment)
      ? req.review_comment
      : null;
  const applyNote = req.apply_error || null;
  return (
    <div className={styles.statusCell}>
      <span className={`${styles.badge} ${styles[`badge_${display.color}`]}`}>{statusLabel}</span>
      {/* 展開明細已移除（比照上方申請表）：審核備註／機器已刪除／套用失敗訊息改掛在小圖示的 tooltip */}
      {reviewNote && (
        <span className={styles.statusNote} title={reviewNote}>
          <MIcon name="comment" size={14} />
        </span>
      )}
      {deletedByMachine && (
        <span className={styles.statusNote} title={t("SpecRequestRow.deletedNote")}>
          <MIcon name="info" size={14} />
        </span>
      )}
      {applyNote && (
        <span className={styles.statusNote} title={applyNote}>
          <MIcon name={req.applied_at ? "warning" : "error_outline"} size={14} />
        </span>
      )}
    </div>
  );
}

function SpecActionsCell({ rowState }) {
  const { t } = useTranslation("personal");
  const { req, busy, onApply, onCancel } = rowState;
  const display    = specRequestDisplayStatus(req);
  const showApply  = canApplySpecRequest(req);
  const showCancel = canCancelSpecRequest(req);
  const hasAction  = showApply || showCancel;
  return (
    <div className={styles.rowActions}>
      {!hasAction && <span className={styles.emptyAction}>—</span>}
      {showApply && (
        <button type="button" className={styles.applyBtn} disabled={busy} onClick={onApply}>
          <MIcon name="play_arrow" size={13} />
          {display.key === "ready" ? t("SpecRequestRow.apply") : t("SpecRequestRow.reapply")}
        </button>
      )}
      {showCancel && (
        <button type="button" className={styles.cancelBtn} disabled={busy} onClick={onCancel}>
          <MIcon name="close" size={13} />
          {t("SpecRequestRow.cancel")}
        </button>
      )}
    </div>
  );
}

/* ── 欄位定義（模組層級：cell／header 會被當成元件掛載，render 裡新建會整格重掛） ── */
const columnHelper = createColumnHelper();

function requestColumn(id, labelKey, cell) {
  return columnHelper.display({ id, header: RequestHeader, cell, meta: { labelKey, thClassName: styles.th, tdClassName: styles.td } });
}

const REQUEST_COLUMNS = columnHelper.columns([
  requestColumn("resource", "RequestsPage.colResource", RequestResourceCell),
  requestColumn("os", "RequestsPage.colOs", RequestOsCell),
  requestColumn("account", "RequestsPage.fieldAccount", RequestAccountCell),
  requestColumn("spec", "RequestsPage.colSpec", RequestSpecCell),
  requestColumn("gpu", "RequestsPage.colGpu", RequestGpuCell),
  requestColumn("reason", "RequestsPage.colReason", RequestReasonCell),
  requestColumn("requestedAt", "RequestsPage.colRequestedAt", RequestCreatedCell),
  requestColumn("period", "RequestsPage.colPeriod", RequestPeriodCell),
  requestColumn("status", "RequestsPage.colStatus", RequestStatusCell),
  requestColumn("actions", "RequestsPage.colActions", RequestActionsCell),
]);

const SPEC_COLUMNS = columnHelper.columns([
  requestColumn("machine", "RequestsPage.specColMachine", SpecMachineCell),
  requestColumn("change", "RequestsPage.specColChange", SpecChangeCell),
  requestColumn("reason", "RequestsPage.colReason", RequestReasonCell),
  requestColumn("requestedAt", "RequestsPage.colRequestedAt", RequestCreatedCell),
  requestColumn("appliedAt", "RequestsPage.specColAppliedAt", SpecAppliedCell),
  requestColumn("status", "RequestsPage.colStatus", SpecStatusCell),
  requestColumn("actions", "RequestsPage.colActions", SpecActionsCell),
]);

const getRequestId = (req) => String(req.id);

/* ── RequestRow ──
   列元件擁有取消、重試、錯誤紀錄對話框的狀態並自己畫 <tr>，儲存格經 rowState 拿狀態與處理函式 */
function RequestRow({ row, req, onUpdated }) {
  const { t } = useTranslation("personal");
  const toast = useToast();
  const navigate = useNavigate();
  const { user } = useAuth();
  /* VMID 是系統內部編號，僅管理員／老師看得到 */
  const showVmid = canTeachUser(user);
  /* 原始開通錯誤 log 是給管理員除錯用的，學生／老師只看狀態與操作 */
  const isAdmin = isAdminUser(user);
  const confirm = useConfirm();
  const [cancelling, setCancelling]       = useState(false);
  const [retrying, setRetrying]           = useState(false);
  const [logOpen, setLogOpen]             = useState(false);
  const logPresence = useDialogPresence(logOpen);

  const showFailureLog =
    isAdmin && (canRetry(req) || isProvisionedButFailed(req)) && req.provisioning_error;

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
        <DataTableCells
          row={row}
          rowState={{
            req,
            showVmid,
            showFailureLog,
            cancelling,
            retrying,
            onOpenLog: () => setLogOpen(true),
            onRetry: handleRetry,
            onCancel: handleCancel,
            onGoToResources: () => navigate("/my-resources"),
          }}
        />
      </tr>

      {logPresence.open && <ErrorLogModal req={req} closing={logPresence.closing} onClose={() => setLogOpen(false)} />}
    </>
  );
}

/* ── 規格調整申請列 ──
   列元件擁有套用／撤銷中的狀態並自己畫 <tr>，儲存格經 rowState 拿狀態與處理函式 */
function SpecRequestRow({ row, req, onUpdated }) {
  const { t } = useTranslation("personal");
  const toast = useToast();
  const { user } = useAuth();
  const showVmid = canTeachUser(user);
  const confirm = useConfirm();
  const [busy, setBusy]                   = useState(false);

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

  return (
    <tr className={styles.tr}>
      <DataTableCells
        row={row}
        rowState={{ req, showVmid, busy, onApply: handleApply, onCancel: handleCancel }}
      />
    </tr>
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
        /* 沒有紀錄時頁首不放申請鈕，這顆就是唯一入口，導覽的「進入申請表單」也點它 */
        <button type="button" className={styles.btnPrimary} onClick={onCreateClick} data-guide="request-create">
          <MIcon name="add" size={16} />
          {t("RequestsPage.createNow")}
        </button>
      }
    />
  );
}

/* ── Page ── */
const EMPTY = Object.freeze([]);

export default function RequestsPage() {
  const { t } = useTranslation("personal");
  /* 其他頁（如快速練習的「完整設定」）可用 navigate("/my-requests", { state: { create: true } }) 直接開表單 */
  const location = useLocation();
  const [view, setView]         = useState(location.state?.create ? VIEW_CREATE : VIEW_LIST);
  const [returning, setReturning] = useState(false);
  /* AI 助手談完需求後會把推薦配置一起帶過來 */
  const [pendingPrefill, setPendingPrefill] = useState(location.state?.prefill ?? null);

  /* 兩份清單走 TanStack Query：只在列表畫面抓、背景 30 秒刷新、分頁隱藏時暫停；
     規格調整套用中時改 5 秒追進度。規格調整申請載入失敗只影響自己那一段，不拖垮主列表。
     申請單快取與「我的資源」的建立中佔位列共用，兩頁來回不重打 API。 */
  const listOpen = view === VIEW_LIST;
  const specQuery = useMySpecChangeRequestsQuery({ enabled: listOpen });
  const specRequests = specQuery.data ?? EMPTY;
  const specApplying = isApplying(specRequests);
  const requestsQuery = useMyActiveVmRequestsQuery({ enabled: listOpen, fastPoll: specApplying });
  const requests = requestsQuery.data ?? EMPTY;
  const requestCache = useVmRequestCacheUpdater();
  const specCache = useSpecChangeRequestCacheUpdater();
  /* 只有「還沒有任何資料」時才顯示 skeleton／錯誤畫面；背景刷新失敗沿用上一筆資料。
     錯誤畫面在背景重試期間不閃回 skeleton，按重試才回到 skeleton，背景刷新成功也會自動離開錯誤畫面 */
  const requestsView = useQueryView(requestsQuery);
  const specView = useQueryView(specQuery);
  const { error, retry: retryRequests } = requestsView;
  /* 主列表是空的時，規格調整清單第一次有結果（或失敗）之前也算載入中：不然沒有 VM 申請、只有延期／規格調整
     申請的人會先看到「還沒有任何申請」（規格調整清單要問 PVE，常晚好幾秒才回來） */
  const loading = requestsView.loading || (!error && requests.length === 0 && specView.loading);
  const requestTable = useTable({
    features: baseFeatures,
    columns: REQUEST_COLUMNS,
    data: requests,
    getRowId: getRequestId,
  });
  const specTable = useTable({
    features: baseFeatures,
    columns: SPEC_COLUMNS,
    data: specRequests,
    getRowId: getRequestId,
  });

  /* 已經停在本頁時 useState 的初值不會再跑一次，所以導覽助手在這頁按步驟
     只會換掉 location.state。兩個方向都要跟：帶 create 就開表單，沒帶就回列表
     （流程裡的「等待審核」指的就是列表，按了不能沒反應）。 */
  useEffect(() => {
    setView(location.state?.create ? VIEW_CREATE : VIEW_LIST);
    if (location.state?.prefill) setPendingPrefill(location.state.prefill);
  }, [location.key, location.state?.create, location.state?.prefill]);

  function handleUpdated(updated) {
    return requestCache.update(updated);
  }

  function handleSpecUpdated(updated) {
    return specCache.update(updated);
  }

  function retryLoad() {
    retryRequests();
    specQuery.refetch();
  }

  if (view === VIEW_CREATE) {
    return (
      <RequestFormPage
        key="create"
        className={styles.animSlideInRight}
        initialPrefill={pendingPrefill}
        onBack={() => {
          setReturning(true);
          setView(VIEW_LIST);
          setPendingPrefill(null);
          /* 剛送出的申請要出現在列表：回來時先顯示快取，同時標記過期重抓 */
          requestCache.invalidate();
          specCache.invalidate();
        }}
      />
    );
  }

  const isEmpty = !error && !loading && requests.length === 0 && specRequests.length === 0;
  /* 頁首的申請鈕等有紀錄才出現：沒資料時只留中間空狀態那一顆，不要上下兩顆一樣的鈕；
     載入中也先不放，免得空清單的人看到它閃一下又消失 */
  const showHeaderCreate = !loading && !isEmpty;

  return (
    <div
      className={`${styles.page} ${returning ? styles.animSlideInLeft : ""}`}
      onAnimationEnd={returning ? () => setReturning(false) : undefined}
    >
      <PageHeader title={t("RequestsPage.title")}>
        {showHeaderCreate && (
          <button type="button" className={styles.btnPrimary} onClick={() => setView(VIEW_CREATE)} data-guide="request-create">
            <MIcon name="add" size={16} />
            {t("RequestsPage.requestResource")}
          </button>
        )}
      </PageHeader>

      <div className={styles.content} data-guide="request-list">
        {error ? (
          <ErrorState onRetry={retryLoad} />
        ) : loading ? (
          <LoadingState fullPage />
        ) : isEmpty ? (
          <EmptyState onCreateClick={() => setView(VIEW_CREATE)} />
        ) : (
          <>
            {requests.length > 0 && (
              <div className={styles.tableWrap}>
                <DataTable
                  table={requestTable}
                  className={styles.table}
                  renderRow={(row) => <RequestRow key={row.id} row={row} req={row.original} onUpdated={handleUpdated} />}
                />
              </div>
            )}

            {specRequests.length > 0 && (
              <section className={styles.subSection}>
                <h2 className={styles.sectionTitle}>{t("RequestsPage.specSectionTitle")}</h2>
                <p className={styles.sectionDesc}>{t("RequestsPage.specSectionDesc")}</p>
                <div className={styles.tableWrap}>
                  <DataTable
                    table={specTable}
                    className={styles.table}
                    renderRow={(row) => <SpecRequestRow key={row.id} row={row} req={row.original} onUpdated={handleSpecUpdated} />}
                  />
                </div>
              </section>
            )}
          </>
        )}
      </div>
    </div>
  );
}
