import { useCallback, useEffect, useState } from "react";
import styles from "./ResourcesPage.module.scss";
import MIcon from "../../../components/MIcon";
import { ResourcesService } from "../../../services/resources";

/* ── Constants ── */
const STATUS_MAP = {
  running: { label: "運行中", color: "success", icon: "play_circle"  },
  stopped: { label: "已停止", color: "warning", icon: "stop_circle"  },
  paused:  { label: "已暫停", color: "info",    icon: "pause_circle" },
};

const TYPE_MAP = {
  lxc:   { label: "容器 (LXC)", icon: "terminal" },
  qemu:  { label: "虛擬機 (VM)", icon: "computer" },
};

/* ── Helpers ── */
function formatBytes(bytes) {
  if (!bytes) return null;
  const gb = bytes / (1024 ** 3);
  return gb >= 1 ? `${gb % 1 === 0 ? gb : gb.toFixed(1)} GB` : `${Math.round(bytes / (1024 ** 2))} MB`;
}

function formatUptime(seconds) {
  if (!seconds) return null;
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d} 天 ${h} 小時`;
  if (h > 0) return `${h} 小時 ${m} 分`;
  return `${m} 分`;
}

function formatDate(isoStr) {
  if (!isoStr) return null;
  return new Date(isoStr).toLocaleDateString("zh-TW", {
    year: "numeric", month: "2-digit", day: "2-digit",
  });
}

/* ── Primitive sub-components ── */
function StatusBadge({ status }) {
  const s = STATUS_MAP[status] ?? { label: status, color: "muted", icon: "help_outline" };
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
  if (!value) return null;
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

/* ── ResourceCard ── */
function ResourceCard({ resource, onUpdated, onDeleted }) {
  const [actionLoading, setActionLoading] = useState(null);
  const [deleteConfirm, setDeleteConfirm]  = useState(false);
  const [deleting, setDeleting]            = useState(false);

  const type   = TYPE_MAP[resource.type] ?? { label: resource.type, icon: "computer" };
  const isRunning = resource.status === "running";
  const isStopped = resource.status === "stopped" || resource.status === "paused";

  async function handleControl(action, label) {
    setActionLoading(action);
    try {
      await ResourcesService[action](resource.vmid);
      onUpdated({ ...resource, status: action === "start" ? "running" : "stopped" });
    } finally {
      setActionLoading(null);
    }
  }

  async function handleDelete() {
    setDeleting(true);
    try {
      await ResourcesService.delete(resource.vmid);
      onDeleted(resource.vmid);
    } finally {
      setDeleting(false);
      setDeleteConfirm(false);
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
            <span className={styles.cardName}>{resource.name}</span>
            <div className={styles.cardChips}>
              <span className={styles.typeChip}>{type.label}</span>
              <span className={styles.vmidChip}>VMID {resource.vmid}</span>
            </div>
          </div>
          <StatusBadge status={resource.status} />
        </div>

        {/* ── Info rows ── */}
        <div className={styles.cardInfo}>
          <InfoRow icon="monitor"    label="系統"   value={resource.os_info} />
          <InfoRow icon="wifi"       label="IP"     value={resource.ip_address} />
          <InfoRow icon="apps"       label="模板"   value={resource.service_template_slug} />
          <InfoRow icon="dns"        label="節點"   value={resource.node} />
        </div>

        {/* ── Spec chips ── */}
        <div className={styles.specRow}>
          <SpecChip label="CPU"    value={resource.maxcpu ? `${resource.maxcpu} 核` : null} />
          <SpecChip label="記憶體" value={formatBytes(resource.maxmem)} />
          {isRunning && <SpecChip label="已運行" value={formatUptime(resource.uptime)} />}
        </div>

        {/* ── Expiry ── */}
        {resource.expiry_date && (
          <div className={styles.cardPeriod}>
            <MIcon name="event" size={13} />
            <span>到期 {formatDate(resource.expiry_date)}</span>
          </div>
        )}

        {/* ── Footer ── */}
        <div className={styles.cardFooter}>
          <div className={styles.powerActions}>
            {isStopped && (
              <button
                type="button"
                className={`${styles.powerBtn} ${styles.powerBtnStart}`}
                disabled={!!actionLoading}
                onClick={() => handleControl("start")}
                title="啟動"
              >
                {actionLoading === "start"
                  ? <MIcon name="hourglass_empty" size={16} />
                  : <span className="material-icons" style={{ fontSize: 16, lineHeight: 1 }}>play_arrow</span>
                }
              </button>
            )}
            {isRunning && (
              <>
                <button
                  type="button"
                  className={styles.powerBtn}
                  disabled={!!actionLoading}
                  onClick={() => handleControl("reboot")}
                  title="重新啟動"
                >
                  <MIcon name={actionLoading === "reboot" ? "hourglass_empty" : "replay"} size={16} />
                </button>
                <button
                  type="button"
                  className={styles.powerBtn}
                  disabled={!!actionLoading}
                  onClick={() => handleControl("shutdown")}
                  title="正常關機"
                >
                  <MIcon name={actionLoading === "shutdown" ? "hourglass_empty" : "power_settings_new"} size={16} />
                </button>
                <button
                  type="button"
                  className={`${styles.powerBtn} ${styles.powerBtnStop}`}
                  disabled={!!actionLoading}
                  onClick={() => handleControl("stop")}
                  title="強制停止"
                >
                  <MIcon name={actionLoading === "stop" ? "hourglass_empty" : "stop"} size={16} />
                </button>
              </>
            )}
          </div>
          <button
            type="button"
            className={styles.deleteBtn}
            onClick={() => setDeleteConfirm(true)}
            title="刪除資源"
          >
            <MIcon name="delete_outline" size={15} />
            刪除
          </button>
        </div>
      </div>

      {deleteConfirm && (
        <ConfirmModal
          title="確定刪除資源？"
          desc={`「${resource.name}」(VMID ${resource.vmid}) 刪除後無法復原，所有資料將會消失。`}
          confirmLabel="刪除"
          danger
          loading={deleting}
          onConfirm={handleDelete}
          onClose={() => setDeleteConfirm(false)}
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
        {[0, 1].map((i) => (
          <div key={i} className={`${styles.skeleton} ${styles.skChip}`} />
        ))}
      </div>
      <div className={styles.cardFooter}>
        <div className={`${styles.skeleton} ${styles.skRow}`} style={{ width: 100, height: 28 }} />
      </div>
    </div>
  );
}

/* ── Empty / Error states ── */
function EmptyState() {
  return (
    <div className={styles.empty}>
      <div className={styles.emptyIcon}>
        <MIcon name="dns" size={40} />
      </div>
      <h2 className={styles.emptyTitle}>尚無資源</h2>
      <p className={styles.emptyDesc}>申請通過的虛擬機／容器將會顯示在這裡</p>
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
      <p className={styles.emptyDesc}>無法取得資源清單，請稍後再試</p>
      <button type="button" className={styles.btnSecondary} onClick={onRetry}>
        <MIcon name="refresh" size={16} />
        重試
      </button>
    </div>
  );
}

/* ── Page ── */
export default function ResourcesPage() {
  const [resources, setResources] = useState([]);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState(false);

  const fetchResources = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const data = await ResourcesService.list();
      setResources(data ?? []);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchResources();
  }, [fetchResources]);

  function handleUpdated(updated) {
    setResources((prev) => prev.map((r) => r.vmid === updated.vmid ? updated : r));
  }

  function handleDeleted(vmid) {
    setResources((prev) => prev.filter((r) => r.vmid !== vmid));
  }

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>我的資源</h1>
          <p className={styles.pageSubtitle}>查看與管理申請通過的虛擬機和容器</p>
        </div>
        <div className={styles.pageActions}>
          <button type="button" className={styles.btnSecondary} onClick={fetchResources} disabled={loading}>
            <MIcon name="sync" size={16} />
            重新整理
          </button>
        </div>
      </div>

      <div className={styles.content}>
        {loading ? (
          <div className={styles.grid}>
            {[0, 1, 2].map((i) => <SkeletonCard key={i} />)}
          </div>
        ) : error ? (
          <ErrorState onRetry={fetchResources} />
        ) : resources.length === 0 ? (
          <EmptyState />
        ) : (
          <div className={styles.grid}>
            {resources.map((r) => (
              <ResourceCard
                key={r.vmid}
                resource={r}
                onUpdated={handleUpdated}
                onDeleted={handleDeleted}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
