import { useCallback, useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import styles from "./RequestsPage.module.scss";
import { VmRequestsService } from "../../../services/vmRequests";
import { useToast } from "../../../hooks/useToast";
import useAutoRefresh from "../../../hooks/useAutoRefresh";
import RequestFormPage from "./RequestFormPage";
import MIcon from "../../../components/MIcon";

/* ── Constants ── */
const STATUS_MAP = {
  pending:   { label: "審核中", color: "info"    },
  approved:  { label: "已核准", color: "success" },
  rejected:  { label: "已拒絕", color: "danger"  },
  cancelled: { label: "已取消", color: "muted"   },
};

const RESOURCE_TYPE_MAP = {
  lxc: { label: "容器 (LXC)", icon: "terminal" },
  vm:  { label: "虛擬機 (VM)", icon: "computer" },
};

/* 開通成功後 VMRequest.status 仍停留在 approved（後端只把 vmid 寫回），
   所以「重試／撤銷」必須同時看 vmid：vmid 已存在代表機器已開出來，
   重試會把使用者關機的 VM 重新開機、撤銷會讓 request 與活著的資源脫鉤。 */
function canRetry(req) {
  return (
    req.status === "approved" &&
    req.vmid == null &&
    req.migration_status === "failed"
  );
}

function canCancel(req) {
  return (
    req.status === "pending" ||
    (req.status === "approved" && req.vmid == null)
  );
}

/* approved 在 UI 上再依開通進度細分（vmid 為空時 migration_status 反映的是開通流程） */
function getDisplayStatus(req) {
  if (req.status === "approved") {
    if (req.vmid != null)                    return { label: "已開通",   color: "success" };
    if (req.migration_status === "failed")   return { label: "開通失敗", color: "danger"  };
    if (req.migration_status === "running")  return { label: "開通中",   color: "info"    };
    return { label: "已核准", color: "success" };
  }
  return STATUS_MAP[req.status] ?? { label: req.status, color: "muted" };
}

const VIEW_LIST   = "list";
const VIEW_CREATE = "create";

const LIST_COLUMNS = ["資源", "系統", "規格", "申請時間", "狀態", "操作"];

/* ── Helpers ── */
function formatDatetime(isoStr) {
  if (!isoStr) return null;
  return new Date(isoStr).toLocaleString("zh-TW", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

function formatDate(isoStr) {
  if (!isoStr) return "—";
  return new Date(isoStr).toLocaleDateString("zh-TW", {
    year: "numeric", month: "2-digit", day: "2-digit",
  });
}

function getOsDisplay(req) {
  if (req.os_info) return req.os_info;
  if (req.ostemplate) {
    const filename = req.ostemplate.split("/").pop() ?? req.ostemplate;
    return filename.replace(/\.tar\.\w+$/, "").replace(/\.tar$/, "");
  }
  return null;
}

function getFormInfoItems(req) {
  const items = [];
  if (req.username)             items.push({ label: "帳號",   value: req.username });
  if (req.gpu_mapping_id)       items.push({ label: "GPU",    value: req.gpu_mapping_id });
  if (req.service_template_slug) items.push({ label: "服務模板", value: req.service_template_slug });
  return items;
}

function getMemDisplay(memMB) {
  if (memMB % 1024 === 0) return `${memMB / 1024} GB`;
  return `${(memMB / 1024).toFixed(1)} GB`;
}

/* ── Primitive sub-components ── */
function StatusBadge({ req }) {
  const s = getDisplayStatus(req);
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

function getSpecDisplay(req) {
  return `${req.cores} 核 / ${getMemDisplay(req.memory)} / ${req.storage}`;
}

/* ── Confirm Modal ── */
function ConfirmModal({ title, desc, confirmLabel = "確定", danger = false, loading = false, onConfirm, onClose }) {
  const [closing, setClosing] = useState(false);

  function close() {
    if (closing) return;
    setClosing(true);
  }

  function handleAnimationEnd() {
    if (closing) onClose();
  }

  return (
    <div
      className={`${styles.modalOverlay} ${closing ? styles.modalOverlayOut : ""}`}
      onClick={close}
      onAnimationEnd={handleAnimationEnd}
    >
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <span className={styles.modalTitle}>{title}</span>
        {desc && <p className={styles.modalDesc}>{desc}</p>}
        <div className={styles.modalActions}>
          <button type="button" className={styles.btnSecondary} onClick={close}>
            取消
          </button>
          <button
            type="button"
            className={danger ? styles.btnDanger : styles.btnPrimary}
            disabled={loading}
            onClick={onConfirm}
          >
            {loading ? "處理中…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── RequestRow ── */
function RequestRow({ req, onUpdated }) {
  const toast = useToast();
  const [expanded, setExpanded]           = useState(false);
  const [cancelConfirm, setCancelConfirm] = useState(false);
  const [cancelling, setCancelling]       = useState(false);
  const [retrying, setRetrying]           = useState(false);

  const type      = RESOURCE_TYPE_MAP[req.resource_type] ?? { label: req.resource_type, icon: "computer" };
  const osDisplay = getOsDisplay(req);
  const formItems = getFormInfoItems(req);
  const startFmt  = formatDatetime(req.start_at);
  const endFmt    = formatDatetime(req.end_at);

  const showRejection = req.status === "rejected" && req.review_comment;
  const showFailure   = canRetry(req) && req.migration_status === "failed" && req.migration_error;
  const hasDetail =
    formItems.length > 0 || req.reason || startFmt || showRejection || showFailure;

  async function handleCancel() {
    setCancelling(true);
    try {
      const updated = await VmRequestsService.cancel(req.id);
      onUpdated(updated);
      toast.success(`已撤銷申請「${req.hostname}」`);
    } catch (err) {
      toast.error(err?.message ?? "撤銷失敗，請稍後再試。");
    } finally {
      setCancelling(false);
      setCancelConfirm(false);
    }
  }

  async function handleRetry() {
    setRetrying(true);
    try {
      const updated = await VmRequestsService.retry(req.id);
      onUpdated(updated);
      toast.success("已重新觸發開通，進度將自動更新");
    } catch (err) {
      toast.error(err?.message ?? "重試失敗，請稍後再試。");
    } finally {
      setRetrying(false);
    }
  }

  return (
    <>
      <tr
        className={`${styles.tr} ${hasDetail ? styles.trClickable : ""} ${expanded ? styles.trExpanded : ""}`}
        onClick={hasDetail ? () => setExpanded((v) => !v) : undefined}
      >
        <td className={styles.td}>
          <div className={styles.nameCell}>
            <div className={styles.nameIcon}>
              <MIcon name={type.icon} size={18} />
            </div>
            <div className={styles.nameMeta}>
              <span className={styles.namePrimary}>{req.hostname}</span>
              <span className={styles.nameSub}>
                {type.label}
                {req.vmid != null && ` · VMID ${req.vmid}`}
              </span>
            </div>
          </div>
        </td>
        <td className={styles.td}>
          <span className={styles.osCell}>{osDisplay ?? "—"}</span>
        </td>
        <td className={styles.td}>
          <span className={styles.specCell}>{getSpecDisplay(req)}</span>
        </td>
        <td className={styles.td}>{formatDate(req.created_at)}</td>
        <td className={styles.td}><StatusBadge req={req} /></td>
        <td className={styles.td} onClick={(e) => e.stopPropagation()}>
          <div className={styles.rowActions}>
            {canRetry(req) && (
              <button type="button" className={styles.retryBtn} disabled={retrying} onClick={handleRetry}>
                <MIcon name="refresh" size={13} />
                {retrying ? "…" : "重試"}
              </button>
            )}
            {canCancel(req) && (
              <button type="button" className={styles.cancelBtn} onClick={() => setCancelConfirm(true)}>
                <MIcon name="close" size={13} />
                撤銷
              </button>
            )}
            {hasDetail && (
              <button
                type="button"
                className={`${styles.expandBtn} ${expanded ? styles.expandBtnOpen : ""}`}
                aria-label={expanded ? "收合詳細資訊" : "展開詳細資訊"}
                onClick={() => setExpanded((v) => !v)}
              >
                <MIcon name="expand_more" size={16} />
              </button>
            )}
          </div>
        </td>
      </tr>

      {expanded && (
        <tr className={styles.detailTr}>
          <td className={styles.detailTd} colSpan={LIST_COLUMNS.length}>
            <div className={styles.detailBody}>
              {formItems.map(({ label, value }) => (
                <InfoRow key={label} icon="tune" label={label} value={value} />
              ))}
              <InfoRow icon="chat_bubble_outline" label="申請原因" value={req.reason} />
              <InfoRow
                icon="calendar_month"
                label="預約期間"
                value={startFmt ? `${startFmt}${endFmt ? ` ~ ${endFmt}` : ""}` : null}
              />
              {showRejection && (
                <div className={styles.reviewComment}>
                  <MIcon name="comment" size={13} />
                  <span>{req.review_comment}</span>
                </div>
              )}
              {showFailure && (
                <div className={styles.reviewComment}>
                  <MIcon name="error_outline" size={13} />
                  <span>{req.migration_error}</span>
                </div>
              )}
            </div>
          </td>
        </tr>
      )}

      {cancelConfirm && (
        <ConfirmModal
          title="確定撤銷申請？"
          desc={`申請「${req.hostname}」撤銷後無法復原。`}
          confirmLabel="撤銷申請"
          danger
          loading={cancelling}
          onConfirm={handleCancel}
          onClose={() => setCancelConfirm(false)}
        />
      )}
    </>
  );
}

/* ── Skeleton ── */
function SkeletonRow() {
  return (
    <tr className={styles.tr} aria-hidden>
      <td className={styles.td}>
        <div className={styles.nameCell}>
          <div className={`${styles.nameIcon} ${styles.skeleton}`} />
          <div className={styles.nameMeta}>
            <div className={`${styles.skeleton} ${styles.skRow}`} style={{ width: 110, height: 13 }} />
            <div className={`${styles.skeleton} ${styles.skRow}`} style={{ width: 70, height: 10 }} />
          </div>
        </div>
      </td>
      <td className={styles.td}>
        <div className={`${styles.skeleton} ${styles.skRow}`} style={{ width: 90, height: 12 }} />
      </td>
      <td className={styles.td}>
        <div className={`${styles.skeleton} ${styles.skRow}`} style={{ width: 130, height: 12 }} />
      </td>
      <td className={styles.td}>
        <div className={`${styles.skeleton} ${styles.skRow}`} style={{ width: 80, height: 12 }} />
      </td>
      <td className={styles.td}>
        <div className={`${styles.skeleton} ${styles.skBadge}`} />
      </td>
      <td className={styles.td}>
        <div className={`${styles.skeleton} ${styles.skRow}`} style={{ width: 60, height: 12 }} />
      </td>
    </tr>
  );
}

/* ── Empty / Error states ── */
function EmptyState({ onCreateClick }) {
  return (
    <div className={styles.empty}>
      <div className={styles.emptyIcon}>
        <MIcon name="description" size={40} />
      </div>
      <h2 className={styles.emptyTitle}>尚無申請紀錄</h2>
      <p className={styles.emptyDesc}>你送出的虛擬機／容器申請將會顯示在這裡</p>
      <button type="button" className={styles.btnPrimary} onClick={onCreateClick}>
        <MIcon name="add" size={16} />
        立即申請
      </button>
    </div>
  );
}

function ErrorState({ onRetry }) {
  return (
    <div className={styles.empty}>
      <div className={`${styles.emptyIcon} ${styles.emptyIconError}`}>
        <MIcon name="error_outline" size={40} />
      </div>
      <h2 className={styles.emptyTitle}>載入失敗</h2>
      <p className={styles.emptyDesc}>無法取得申請紀錄，請稍後再試</p>
      <button type="button" className={styles.btnSecondary} onClick={onRetry}>
        <MIcon name="refresh" size={16} />
        重試
      </button>
    </div>
  );
}

/* ── Page ── */
export default function RequestsPage() {
  /* 其他頁（如快速建立的「完整設定」）可用 navigate("/my-requests", { state: { create: true } }) 直接開表單 */
  const location = useLocation();
  const [requests, setRequests] = useState([]);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState(false);
  const [view, setView]         = useState(location.state?.create ? VIEW_CREATE : VIEW_LIST);
  const [returning, setReturning] = useState(false);

  /** silent = true 時不觸發 loading / error state，供背景自動刷新使用 */
  const fetchRequests = useCallback(async (silent = false) => {
    if (!silent) {
      setLoading(true);
      setError(false);
    }
    try {
      const res = await VmRequestsService.list();
      setRequests(
        (res.data ?? []).filter(
          (r) => !(r.review_comment ?? "").startsWith("Resource deleted")
        )
      );
    } catch {
      if (!silent) setError(true);
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (view === "list") fetchRequests();
  }, [view, fetchRequests]);

  useAutoRefresh(() => {
    if (view === "list") fetchRequests(true);
  });

  function handleUpdated(updated) {
    setRequests((prev) => prev.map((r) => r.id === updated.id ? updated : r));
  }

  if (view === VIEW_CREATE) {
    return (
      <RequestFormPage
        key="create"
        className={styles.animSlideInRight}
        onBack={() => { setReturning(true); setView(VIEW_LIST); }}
      />
    );
  }

  return (
    <div
      className={`${styles.page} ${returning ? styles.animSlideInLeft : ""}`}
      onAnimationEnd={returning ? () => setReturning(false) : undefined}
    >
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>我的申請</h1>
          <p className={styles.pageSubtitle}>管理你的虛擬機與容器申請</p>
        </div>
        <button type="button" className={styles.btnPrimary} onClick={() => setView(VIEW_CREATE)}>
          <MIcon name="add" size={16} />
          申請資源
        </button>
      </div>

      <div className={styles.content}>
        {error ? (
          <ErrorState onRetry={fetchRequests} />
        ) : !loading && requests.length === 0 ? (
          <EmptyState onCreateClick={() => setView(VIEW_CREATE)} />
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  {LIST_COLUMNS.map((column) => (
                    <th key={column} className={styles.th}>{column}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {loading
                  ? [0, 1, 2, 3].map((i) => <SkeletonRow key={i} />)
                  : requests.map((r) => (
                      <RequestRow key={r.id} req={r} onUpdated={handleUpdated} />
                    ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
