import { useCallback, useEffect, useRef, useState } from "react";
import styles from "./ResourcesPage.module.scss";
import MIcon from "../../../components/MIcon";
import { ResourcesService } from "../../../services/resources";
import {
  PENDING_POLL_INTERVAL,
  cancelVmRequest,
  fetchPendingResources,
  pendingSignature,
} from "../../../services/pendingResources";
import { useToast } from "../../../hooks/useToast";
import useAutoRefresh from "../../../hooks/useAutoRefresh";
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

function formatDatetime(isoStr) {
  if (!isoStr) return null;
  return new Date(isoStr).toLocaleString("zh-TW", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
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
          <span style={{ color: "var(--color-success)", lineHeight: 1 }}><MIcon name="play_arrow" size={15} /></span>
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

/* ── Creating placeholder card ── */

/** 依申請階段決定 placeholder 的狀態顯示（開通中 / 超時 / 失敗…） */
function getCreatingDisplay(req) {
  if (req.status === "pending") {
    return { label: "審核中", color: "info", spin: true };
  }
  if (req.migration_status === "failed") {
    return { label: "開通失敗", color: "danger", spin: false };
  }
  if (req.migration_status === "running") {
    return { label: "開通中", color: "info", spin: true };
  }
  // approved 等待排程開機：start_at 已過但仍未開始建立 → 超時
  if (req.start_at && new Date(req.start_at).getTime() < Date.now()) {
    const overdueMin = Math.floor((Date.now() - new Date(req.start_at).getTime()) / 60_000);
    const overdueLabel = overdueMin >= 60 ? `${Math.floor(overdueMin / 60)} 小時` : `${overdueMin} 分鐘`;
    return { label: `超時 (${overdueLabel})`, color: "danger", spin: false };
  }
  return { label: "排程中", color: "info", spin: true };
}

function formatMemory(memoryMb) {
  if (memoryMb == null) return null;
  return memoryMb >= 1024 ? `${memoryMb / 1024} GB` : `${memoryMb} MB`;
}

function CreatingCard({ request, onCancelled }) {
  const toast = useToast();
  const [cancelConfirm, setCancelConfirm] = useState(false);
  const [cancelling, setCancelling]       = useState(false);

  const type    = TYPE_MAP[request.resource_type === "lxc" ? "lxc" : "qemu"];
  const display = getCreatingDisplay(request);
  // 開通流程一旦開始跑 Proxmox clone 就無法取消
  const canCancel = request.migration_status !== "running";

  async function handleCancel() {
    setCancelling(true);
    try {
      await cancelVmRequest(request.id);
      toast.success(`已送出取消申請「${request.hostname}」`);
      setCancelConfirm(false);
      onCancelled();
    } catch (err) {
      toast.error(err?.message ?? "取消申請失敗");
    } finally {
      setCancelling(false);
    }
  }

  const specs = [
    request.cores != null ? `${request.cores} 核心` : null,
    formatMemory(request.memory),
  ].filter(Boolean).join(" / ");

  return (
    <>
      <div className={`${styles.card} ${styles.cardCreating}`}>

        <div className={styles.cardHeader}>
          <div className={styles.cardIcon}>
            <MIcon name={type.icon} size={20} />
          </div>
          <div className={styles.cardMeta}>
            <span className={styles.cardName}>{request.hostname}</span>
            <div className={styles.cardChips}>
              <span className={styles.typeChip}>{type.label}</span>
            </div>
          </div>
          <span className={`${styles.badge} ${styles[`badge_${display.color}`]} ${styles.creatingBadge}`}>
            <span className={display.spin ? styles.spin : styles.badgeIcon}>
              <MIcon name={display.spin ? "autorenew" : "error_outline"} size={12} />
            </span>
            {display.label}
          </span>
        </div>

        <div className={styles.cardInfo}>
          <InfoRow icon="memory"   label="規格" value={specs || null} />
          <InfoRow icon="dns"      label="節點" value={request.assigned_node ?? request.desired_node ?? "自動分配"} />
          <InfoRow icon="schedule" label="排程" value={formatDatetime(request.start_at)} />
          <InfoRow icon="event"    label="申請" value={formatDatetime(request.created_at)} />
        </div>

        <div className={styles.cardFooter}>
          <span className={styles.deletedNote}>建立完成後會自動出現在列表</span>
          <button
            type="button"
            className={styles.cancelBtn}
            disabled={!canCancel || cancelling}
            title={canCancel ? "取消申請" : "建立流程已開始，無法取消"}
            onClick={() => setCancelConfirm(true)}
          >
            <MIcon name="cancel" size={14} />
            取消申請
          </button>
        </div>

      </div>

      {cancelConfirm && (
        <ConfirmModal
          title="確定取消申請？"
          desc={`申請「${request.hostname}」取消後無法復原，需重新提出申請。`}
          confirmLabel="取消申請"
          danger
          loading={cancelling}
          onConfirm={handleCancel}
          onClose={() => setCancelConfirm(false)}
        />
      )}
    </>
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
  const [pending, setPending]     = useState([]);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState(false);
  const pendingSigRef = useRef(null);

  /** silent = true 時不觸發 skeleton / error state，供背景同步使用 */
  const fetchResources = useCallback(async (silent = false) => {
    if (!silent) {
      setLoading(true);
      setError(false);
    }
    try {
      const data = await ResourcesService.list();
      setResources(data ?? []);
    } catch {
      if (!silent) setError(true);
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  /** 輪詢建立中的申請；階段變化（開通完成／失敗／取消）時靜默刷新資源列表 */
  const refreshPending = useCallback(async () => {
    try {
      const items = await fetchPendingResources();
      setPending(items);
      const sig = pendingSignature(items);
      if (pendingSigRef.current !== null && sig !== pendingSigRef.current) {
        fetchResources(true);
      }
      pendingSigRef.current = sig;
    } catch {
      // 輪詢失敗靜默忽略，下一輪再試
    }
  }, [fetchResources]);

  useEffect(() => {
    fetchResources();
  }, [fetchResources]);

  useEffect(() => {
    refreshPending();
    const timer = setInterval(refreshPending, PENDING_POLL_INTERVAL);
    return () => clearInterval(timer);
  }, [refreshPending]);

  useAutoRefresh(() => fetchResources(true));

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
      </div>

      <div className={styles.content}>
        {loading ? (
          <div className={styles.grid}>
            {[0, 1, 2].map((i) => <SkeletonCard key={i} />)}
          </div>
        ) : error ? (
          <ErrorState onRetry={() => fetchResources()} />
        ) : resources.length === 0 && pending.length === 0 ? (
          <EmptyState />
        ) : (
          <div className={styles.grid}>
            {pending.map((req) => (
              <CreatingCard
                key={`creating:${req.id}`}
                request={req}
                onCancelled={refreshPending}
              />
            ))}
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
