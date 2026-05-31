import { useCallback, useEffect, useState } from "react";
import styles from "./RequestsPage.module.scss";
import { VmRequestsService } from "../../../services/vmRequests";
import RequestFormPage from "./RequestFormPage";
import MIcon from "../../../components/MIcon";

/* ── Constants ── */
const STATUS_MAP = {
  pending:   { label: "審核中", color: "info",    icon: "schedule"     },
  approved:  { label: "已核准", color: "success", icon: "check_circle" },
  rejected:  { label: "已拒絕", color: "danger",  icon: "cancel"       },
  cancelled: { label: "已取消", color: "muted",   icon: "block"        },
};

const RESOURCE_TYPE_MAP = {
  lxc: { label: "容器 (LXC)", icon: "terminal" },
  vm:  { label: "虛擬機 (VM)", icon: "computer" },
};

const CANCELLABLE = new Set(["pending", "approved"]);
const RETRYABLE   = new Set(["approved"]);

const VIEW_LIST   = "list";
const VIEW_CREATE = "create";

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
function StatusBadge({ status }) {
  const s = STATUS_MAP[status] ?? { label: status, color: "muted", icon: "info" };
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

function SpecChip({ label, value }) {
  return (
    <div className={styles.specChip}>
      <span className={styles.specChipLabel}>{label}</span>
      <span className={styles.specChipValue}>{value}</span>
    </div>
  );
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

/* ── RequestCard ── */
function RequestCard({ req, onUpdated }) {
  const [cancelConfirm, setCancelConfirm] = useState(false);
  const [cancelling, setCancelling]       = useState(false);
  const [retrying, setRetrying]           = useState(false);

  const type      = RESOURCE_TYPE_MAP[req.resource_type] ?? { label: req.resource_type, icon: "computer" };
  const osDisplay = getOsDisplay(req);
  const formItems = getFormInfoItems(req);
  const startFmt  = formatDatetime(req.start_at);
  const endFmt    = formatDatetime(req.end_at);

  async function handleCancel() {
    setCancelling(true);
    try {
      const updated = await VmRequestsService.cancel(req.id);
      onUpdated(updated);
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
    } finally {
      setRetrying(false);
    }
  }

  return (
    <>
      <div className={styles.card}>

        {/* ── Header ── */}
        <div className={styles.cardHeader}>
          <div className={styles.cardIcon}>
            <MIcon name={type.icon} size={22} />
          </div>
          <div className={styles.cardMeta}>
            <span className={styles.cardName}>{req.hostname}</span>
            <div className={styles.cardChips}>
              <span className={styles.typeChip}>{type.label}</span>
              {req.vmid && <span className={styles.vmidChip}>VMID {req.vmid}</span>}
            </div>
          </div>
          <StatusBadge status={req.status} />
        </div>

        {/* ── Info rows ── */}
        <div className={styles.cardInfo}>
          <InfoRow icon="monitor"             label="系統"    value={osDisplay} />
          {formItems.map(({ label, value }) => (
            <InfoRow key={label} icon="tune"  label={label}   value={value} />
          ))}
          <InfoRow icon="chat_bubble_outline" label="申請原因" value={req.reason} />
        </div>

        {/* ── Spec chips ── */}
        <div className={styles.specRow}>
          <SpecChip label="CPU"    value={`${req.cores} 核`}        />
          <SpecChip label="記憶體" value={getMemDisplay(req.memory)} />
          <SpecChip label="儲存"   value={req.storage}              />
        </div>

        {/* ── Booking period ── */}
        {startFmt && (
          <div className={styles.cardPeriod}>
            <MIcon name="calendar_month" size={13} />
            <span>{startFmt}{endFmt ? ` ~ ${endFmt}` : ""}</span>
          </div>
        )}

        {/* ── Rejection comment ── */}
        {req.status === "rejected" && req.review_comment && (
          <div className={styles.reviewComment}>
            <MIcon name="comment" size={13} />
            <span>{req.review_comment}</span>
          </div>
        )}

        {/* ── Footer ── */}
        <div className={styles.cardFooter}>
          <span className={styles.cardDate}>申請於 {formatDate(req.created_at)}</span>
          <div className={styles.cardActions}>
            {RETRYABLE.has(req.status) && (
              <button type="button" className={styles.retryBtn} disabled={retrying} onClick={handleRetry}>
                <MIcon name="refresh" size={13} />
                {retrying ? "…" : "重試"}
              </button>
            )}
            {CANCELLABLE.has(req.status) && (
              <button type="button" className={styles.cardCancelBtn} onClick={() => setCancelConfirm(true)}>
                <MIcon name="close" size={13} />
                撤銷申請
              </button>
            )}
          </div>
        </div>
      </div>

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
function SkeletonCard() {
  return (
    <div className={styles.card} aria-hidden>
      <div className={styles.cardHeader}>
        <div className={`${styles.cardIcon} ${styles.skeleton}`} />
        <div className={styles.cardMeta}>
          <div className={`${styles.skeleton} ${styles.skRow}`} style={{ width: "55%", height: 14 }} />
          <div className={`${styles.skeleton} ${styles.skRow}`} style={{ width: "32%", height: 11 }} />
        </div>
        <div className={`${styles.skeleton} ${styles.skBadge}`} />
      </div>
      <div className={styles.cardInfo}>
        {[0, 1, 2].map((i) => (
          <div key={i} className={styles.skInfoRow}>
            <div className={`${styles.skeleton} ${styles.skRow}`} style={{ width: "22%", height: 11 }} />
            <div className={`${styles.skeleton} ${styles.skRow}`} style={{ width: "55%", height: 11 }} />
          </div>
        ))}
      </div>
      <div className={styles.specRow}>
        {[0, 1, 2].map((i) => (
          <div key={i} className={`${styles.skeleton} ${styles.skChip}`} />
        ))}
      </div>
      <div className={styles.cardFooter}>
        <div className={`${styles.skeleton} ${styles.skRow}`} style={{ width: 120, height: 11 }} />
      </div>
    </div>
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
export default function RequestsPage({ intent }) {
  const [requests, setRequests] = useState([]);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState(false);
  const [view, setView]         = useState(VIEW_LIST);

  const fetchRequests = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const res = await VmRequestsService.list();
      setRequests(
        (res.data ?? []).filter(
          (r) => r.review_comment !== "Resource deleted by user"
        )
      );
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (view === "list") fetchRequests();
  }, [view, fetchRequests]);

  useEffect(() => {
    if (intent?.view === VIEW_CREATE) setView(VIEW_CREATE);
  }, [intent?.nonce, intent?.view]);

  function handleUpdated(updated) {
    setRequests((prev) => prev.map((r) => r.id === updated.id ? updated : r));
  }

  if (view === VIEW_CREATE) {
    return (
      <RequestFormPage
        key="create"
        className={styles.animSlideInRight}
        quickTemplateSlug={intent?.quickTemplateSlug}
        onBack={() => setView(VIEW_LIST)}
      />
    );
  }

  return (
    <div className={styles.page}>
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
        {loading ? (
          <div className={styles.grid}>
            {[0, 1, 2].map((i) => <SkeletonCard key={i} />)}
          </div>
        ) : error ? (
          <ErrorState onRetry={fetchRequests} />
        ) : requests.length === 0 ? (
          <EmptyState onCreateClick={() => setView(VIEW_CREATE)} />
        ) : (
          <div className={styles.grid}>
            {requests.map((r) => (
              <RequestCard key={r.id} req={r} onUpdated={handleUpdated} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
