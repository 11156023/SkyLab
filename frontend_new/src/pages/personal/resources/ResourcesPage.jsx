import { useCallback, useEffect, useRef, useState } from "react";
import styles from "./ResourcesPage.module.scss";
import MIcon from "../../../components/MIcon";
import { ResourcesService } from "../../../services/resources";
import TerminalDialog from "./TerminalDialog";
import VncDialog from "./VncDialog";

/* ── Constants ── */
const STATUS_MAP = {
  scheduled:    { label: "已排程",   color: "info",    icon: "event"          },
  provisioning: { label: "建立中",   color: "info",    icon: "settings"       },
  running:      { label: "執行中",   color: "success", icon: "play_circle"    },
  stopped:      { label: "已關機",   color: "muted",   icon: "stop_circle"    },
  paused:       { label: "已暫停",   color: "muted",   icon: "pause_circle"   },
  failed:       { label: "建立失敗", color: "danger",  icon: "error_outline"  },
  deleted:      { label: "已刪除",   color: "danger",  icon: "delete_forever" },
  unknown:      { label: "狀態未知", color: "muted",   icon: "help_outline"   },
};

const TYPE_MAP = {
  lxc:   { label: "容器 (LXC)", icon: "terminal" },
  qemu:  { label: "虛擬機 (VM)", icon: "computer" },
};

/* ── Helpers ── */
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

/* ── Power dropdown ── */
function PowerMenu({ resource, actionLoading, onControl, onDeleteClick, onClose, anchorRef, closing }) {
  const ref = useRef(null);

  useEffect(() => {
    function handler(e) {
      if (!ref.current?.contains(e.target) && !anchorRef?.current?.contains(e.target)) onClose();
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose, anchorRef]);

  const isRunning = resource.status === "running";
  const isStopped = resource.status === "stopped" || resource.status === "paused";

  return (
    <div
      ref={ref}
      className={`${styles.powerMenu} ${closing ? styles.powerMenuOut : ""}`}
    >
      <div className={styles.powerMenuTitle}>電源控制</div>
      <div className={styles.powerMenuGrid}>
        <button type="button" className={styles.powerMenuItem}
          disabled={!isStopped || !!actionLoading} onClick={() => { onClose(); onControl("start"); }}>
          <span className="material-icons" style={{ fontSize: 15, lineHeight: 1, color: "#28a745" }}>play_arrow</span>
          啟動
        </button>
        <button type="button" className={`${styles.powerMenuItem} ${styles.powerMenuItemWarn}`}
          disabled={!isRunning || !!actionLoading} onClick={() => { onClose(); onControl("stop"); }}>
          <MIcon name="stop" size={15} />強制停止
        </button>
        <button type="button" className={styles.powerMenuItem}
          disabled={!isRunning || !!actionLoading} onClick={() => { onClose(); onControl("shutdown"); }}>
          <MIcon name="power_settings_new" size={15} />關機
        </button>
        <button type="button" className={`${styles.powerMenuItem} ${styles.powerMenuItemWarn}`}
          disabled={!isRunning || !!actionLoading} onClick={() => { onClose(); onControl("reset"); }}>
          <MIcon name="restart_alt" size={15} />強制重置
        </button>
        <button type="button" className={styles.powerMenuItem}
          disabled={!isRunning || !!actionLoading} onClick={() => { onClose(); onControl("reboot"); }}>
          <MIcon name="replay" size={15} />重新啟動
        </button>
        <button type="button" className={`${styles.powerMenuItem} ${styles.powerMenuItemDanger}`}
          onClick={() => onDeleteClick()}>
          <MIcon name="delete_outline" size={15} />刪除
        </button>
      </div>
    </div>
  );
}

const LIVE_STATUSES = new Set(["running", "stopped", "paused"]);

function resourceCardKey(resource, index) {
  const parts = [
    resource.type || "resource",
    resource.node || "unknown-node",
    resource.vmid ?? resource.request_id ?? resource.name ?? "unknown",
  ];
  return `${parts.join(":")}:${index}`;
}

/* ── ResourceCard ── */
function ResourceCard({ resource, onUpdated, onDeleted }) {
  const [actionLoading, setActionLoading] = useState(null);
  const [deleteConfirm, setDeleteConfirm]  = useState(false);
  const [deleting, setDeleting]            = useState(false);
  const [menuOpen, setMenuOpen]            = useState(false);
  const [menuClosing, setMenuClosing]      = useState(false);
  const [consoleOpen, setConsoleOpen]      = useState(false);
  const menuBtnRef = useRef(null);

  function closeMenu() {
    setMenuClosing(true);
    setTimeout(() => { setMenuOpen(false); setMenuClosing(false); }, 130);
  }

  const type    = TYPE_MAP[resource.type] ?? { label: resource.type, icon: "computer" };
  const isLxc   = resource.type === "lxc";
  const canControl = resource.can_control !== false && resource.vmid != null && resource.vmid > 0;
  const isLive  = canControl && LIVE_STATUSES.has(resource.status);

  async function handleControl(action) {
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
            <MIcon name={type.icon} size={20} />
          </div>
          <div className={styles.cardMeta}>
            <span className={styles.cardName}>{resource.name}</span>
            <div className={styles.cardChips}>
              <span className={styles.typeChip}>{type.label}</span>
              {resource.vmid > 0 && <span className={styles.vmidChip}>VMID {resource.vmid}</span>}
            </div>
          </div>
          <StatusBadge status={resource.status} />
        </div>

        {/* ── Info rows ── */}
        <div className={styles.cardInfo}>
          <InfoRow icon="monitor"  label="系統" value={resource.os_info} />
          <InfoRow icon="category" label="環境" value={resource.environment_type} />
          <InfoRow icon="wifi"     label="IP"   value={resource.ip_address ?? "N/A"} />
          <div className={styles.infoRow}>
            <span className={styles.infoLabel}>
              <MIcon name="event" size={12} />
              到期
            </span>
            <span className={styles.infoValue}>
              {resource.expiry_date
                ? formatDate(resource.expiry_date)
                : <span className={styles.cardPeriodUnlimited}>∞ 無期限</span>
              }
            </span>
          </div>
        </div>

        {/* ── Footer ── */}
        <div className={styles.cardFooter}>
          {isLive ? (
            <>
              <button type="button" className={styles.terminalBtn} title={isLxc ? "終端機" : "控制台"} disabled={resource.status !== "running"} onClick={() => setConsoleOpen(true)}>
                <MIcon name={isLxc ? "terminal" : "desktop_windows"} size={14} />
                {isLxc ? "終端機" : "控制台"}
              </button>
              <div className={styles.cardActions}>
                {actionLoading && <MIcon name="hourglass_empty" size={16} />}
                <div className={styles.menuWrap}>
                  {menuOpen && (
                    <PowerMenu
                      resource={resource}
                      actionLoading={actionLoading}
                      onControl={handleControl}
                      onDeleteClick={() => { closeMenu(); setDeleteConfirm(true); }}
                      onClose={closeMenu}
                      anchorRef={menuBtnRef}
                      closing={menuClosing}
                    />
                  )}
                  <button
                    ref={menuBtnRef}
                    type="button"
                    className={`${styles.menuBtn} ${menuOpen ? styles.menuBtnActive : ""}`}
                    onClick={() => menuOpen ? closeMenu() : setMenuOpen(true)}
                    title="電源控制"
                  >
                    <MIcon name="more_vert" size={18} />
                  </button>
                </div>
              </div>
            </>
          ) : (
            <span className={styles.deletedNote}>
              {STATUS_MAP[resource.status]?.label ?? resource.status}
            </span>
          )}
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

      {consoleOpen && isLxc && (
        <TerminalDialog resource={resource} onClose={() => setConsoleOpen(false)} />
      )}
      {consoleOpen && !isLxc && (
        <VncDialog resource={resource} onClose={() => setConsoleOpen(false)} />
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
            {resources.map((r, index) => (
              <ResourceCard
                key={resourceCardKey(r, index)}
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
