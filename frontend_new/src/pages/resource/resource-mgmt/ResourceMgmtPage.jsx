import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import styles from "./ResourceMgmtPage.module.scss";
import MIcon from "../../../components/MIcon";
import { useToast } from "../../../hooks/useToast";
import { ResourcesService } from "../../../services/resources";
import TerminalDialog from "../../personal/resources/TerminalDialog";
import VncDialog from "../../personal/resources/VncDialog";

/* ── Constants ── */
const STATUS_MAP = {
  scheduled:    { label: "已排程",   color: "info"    },
  provisioning: { label: "建立中",   color: "info"    },
  running:      { label: "執行中",   color: "success" },
  stopped:      { label: "已關機",   color: "muted"   },
  paused:       { label: "已暫停",   color: "muted"   },
  failed:       { label: "建立失敗", color: "danger"  },
  deleted:      { label: "已刪除",   color: "danger"  },
  unknown:      { label: "狀態未知", color: "muted"   },
};

const TYPE_MAP = {
  lxc:  { label: "容器 (LXC)",  icon: "terminal" },
  qemu: { label: "虛擬機 (VM)", icon: "computer" },
};

const ACTION_LABEL = {
  start:    "啟動",
  stop:     "強制停止",
  shutdown: "關機",
  reset:    "強制重置",
  reboot:   "重新啟動",
};

const COLUMNS = ["名稱", "環境 / 系統", "狀態", "IP 位址", "到期日", "節點", "動作"];

const BATCH_ACTIONS = [
  { action: "start",    label: "啟動",     icon: "play_arrow" },
  { action: "shutdown", label: "關機",     icon: "power_settings_new" },
  { action: "reboot",   label: "重新啟動", icon: "restart_alt" },
  { action: "stop",     label: "強制停止", icon: "stop" },
  { action: "reset",    label: "強制重置", icon: "cancel" },
];

const LIVE_STATUSES = new Set(["running", "stopped", "paused"]);

/* ── Helpers ── */
function formatDate(isoStr) {
  if (!isoStr) return null;
  return new Date(isoStr).toLocaleDateString("zh-TW", {
    year: "numeric", month: "2-digit", day: "2-digit",
  });
}

function resourceRowKey(resource, index) {
  const parts = [
    resource.type || "resource",
    resource.node || "unknown-node",
    resource.vmid ?? resource.request_id ?? resource.name ?? "unknown",
  ];
  return `${parts.join(":")}:${index}`;
}

/** 電源操作後的樂觀狀態：start/reboot/reset 後仍為執行中，stop/shutdown 後為已關機 */
function statusAfterAction(action) {
  return action === "stop" || action === "shutdown" ? "stopped" : "running";
}

/* ── Primitive sub-components ── */
function StatusBadge({ status }) {
  const s = STATUS_MAP[status] ?? { label: status, color: "muted" };
  return (
    <span className={`${styles.badge} ${styles[`badge_${s.color}`]}`}>
      {s.label}
    </span>
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

/* ── Table row ── */
/* ── 批次操作列（有勾選才顯示） ── */
function BatchActionBar({ selectedVmids, onDone, onClear }) {
  const toast = useToast();
  const [pending, setPending] = useState(null); // 進行中的 action
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const count = selectedVmids.length;

  async function run(action) {
    setPending(action);
    try {
      const res = await ResourcesService.batchAction(selectedVmids, action);
      const label = action === "delete" ? "刪除" : ACTION_LABEL[action];
      if ((res?.failed ?? 0) === 0) {
        toast.success(`已對 ${res?.succeeded ?? count} 台送出「${label}」`);
      } else {
        toast.error(`「${label}」成功 ${res?.succeeded ?? 0} 台、失敗 ${res?.failed} 台`);
      }
      onDone();
    } catch (err) {
      toast.error(err?.message ?? "批次操作失敗");
    } finally {
      setPending(null);
      setDeleteConfirm(false);
    }
  }

  if (count === 0) return null;

  return (
    <div className={styles.batchBar}>
      <span className={styles.batchCount}>已選 {count} 台</span>
      <span className={styles.batchDivider} />
      {BATCH_ACTIONS.map(({ action, label, icon }) => (
        <button
          key={action}
          type="button"
          className={styles.btnSecondary}
          disabled={pending !== null}
          onClick={() => run(action)}
        >
          <MIcon name={icon} size={14} />
          {label}
        </button>
      ))}
      <span className={styles.batchDivider} />
      <button
        type="button"
        className={styles.btnDangerOutline}
        disabled={pending !== null}
        onClick={() => setDeleteConfirm(true)}
      >
        <MIcon name="delete" size={14} />
        刪除
      </button>
      <button
        type="button"
        className={styles.btnGhost}
        disabled={pending !== null}
        onClick={onClear}
      >
        取消選取
      </button>

      {deleteConfirm && (
        <ConfirmModal
          title={`刪除 ${count} 台資源？`}
          desc="將對所有勾選的虛擬機/容器送出刪除請求，此操作無法復原。"
          confirmLabel={pending === "delete" ? "刪除中…" : "確認刪除"}
          danger
          loading={pending !== null}
          onConfirm={() => run("delete")}
          onClose={() => setDeleteConfirm(false)}
        />
      )}
    </div>
  );
}

function ResourceRow({ resource, onUpdated, onDeleted, selected = false, onToggleSelect = null }) {
  const toast = useToast();
  const navigate = useNavigate();
  const [actionLoading, setActionLoading] = useState(null);
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [deleting, setDeleting]           = useState(false);
  const [menuOpen, setMenuOpen]           = useState(false);
  const [menuClosing, setMenuClosing]     = useState(false);
  const [consoleOpen, setConsoleOpen]     = useState(false);
  const menuBtnRef = useRef(null);

  function closeMenu() {
    setMenuClosing(true);
    setTimeout(() => { setMenuOpen(false); setMenuClosing(false); }, 130);
  }

  const type  = TYPE_MAP[resource.type] ?? { label: resource.type, icon: "computer" };
  const isLxc = resource.type === "lxc";
  const canControl = resource.can_control !== false && resource.vmid != null && resource.vmid > 0;
  const isLive = canControl && LIVE_STATUSES.has(resource.status);

  async function handleControl(action) {
    setActionLoading(action);
    try {
      await ResourcesService[action](resource.vmid);
      toast.success(`已送出「${ACTION_LABEL[action]}」指令（${resource.name}）`);
      onUpdated({ ...resource, status: statusAfterAction(action) });
    } catch (err) {
      toast.error(err?.message ?? `${ACTION_LABEL[action]}失敗`);
    } finally {
      setActionLoading(null);
    }
  }

  async function handleDelete() {
    setDeleting(true);
    try {
      await ResourcesService.delete(resource.vmid);
      toast.success(`已送出刪除請求（${resource.name}）`);
      onDeleted(resource.vmid);
    } catch (err) {
      toast.error(err?.message ?? "刪除失敗");
    } finally {
      setDeleting(false);
      setDeleteConfirm(false);
    }
  }

  return (
    <>
      <tr className={styles.tr}>
        {/* 勾選 */}
        <td className={`${styles.td} ${styles.checkCell}`}>
          {onToggleSelect && canControl ? (
            <input
              type="checkbox"
              className={styles.checkbox}
              checked={selected}
              onChange={() => onToggleSelect(resource.vmid)}
              aria-label={`選取 ${resource.name}`}
            />
          ) : null}
        </td>
        {/* 名稱 */}
        <td className={styles.td}>
          <div className={styles.nameCell}>
            <div className={styles.nameIcon}>
              <MIcon name={type.icon} size={18} />
            </div>
            <div>
              {resource.vmid > 0 ? (
                <button
                  type="button"
                  className={`${styles.namePrimary} ${styles.nameLink}`}
                  title="查看詳情"
                  onClick={() => navigate(`/resource-mgmt/${resource.vmid}`)}
                >
                  {resource.name}
                </button>
              ) : (
                <div className={styles.namePrimary}>{resource.name}</div>
              )}
              <div className={styles.nameSub}>
                {type.label}
                {resource.vmid > 0 && ` · VMID ${resource.vmid}`}
              </div>
            </div>
          </div>
        </td>

        {/* 環境 / 系統 */}
        <td className={styles.td}>
          <div className={styles.envPrimary}>{resource.environment_type ?? "—"}</div>
          {resource.os_info && <div className={styles.envSub}>{resource.os_info}</div>}
        </td>

        {/* 狀態 */}
        <td className={styles.td}>
          <StatusBadge status={resource.status} />
        </td>

        {/* IP */}
        <td className={styles.td}>
          <span className={styles.mono}>{resource.ip_address ?? "N/A"}</span>
        </td>

        {/* 到期日 */}
        <td className={styles.td}>
          {resource.expiry_date
            ? formatDate(resource.expiry_date)
            : <span className={styles.noExpiry}>∞ 無期限</span>
          }
        </td>

        {/* 節點 */}
        <td className={styles.td}>{resource.node ?? "—"}</td>

        {/* 動作 */}
        <td className={styles.td}>
          {isLive ? (
            <div className={styles.actions}>
              <button
                type="button"
                className={styles.consoleBtn}
                title={isLxc ? "終端機" : "控制台"}
                disabled={resource.status !== "running"}
                onClick={() => setConsoleOpen(true)}
              >
                <MIcon name={isLxc ? "terminal" : "desktop_windows"} size={14} />
                {isLxc ? "終端機" : "控制台"}
              </button>
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
          ) : (
            <span className={styles.noAction}>
              {STATUS_MAP[resource.status]?.label ?? "—"}
            </span>
          )}
        </td>
      </tr>

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
function SkeletonRow() {
  return (
    <tr className={styles.tr} aria-hidden>
      <td className={`${styles.td} ${styles.checkCell}`} />
      <td className={styles.td}>
        <div className={styles.nameCell}>
          <div className={`${styles.nameIcon} ${styles.skeleton}`} />
          <div className={styles.skMeta}>
            <div className={`${styles.skeleton} ${styles.skRow}`} style={{ width: 120, height: 13 }} />
            <div className={`${styles.skeleton} ${styles.skRow}`} style={{ width: 80, height: 11 }} />
          </div>
        </div>
      </td>
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <td key={i} className={styles.td}>
          <div className={`${styles.skeleton} ${styles.skRow}`} style={{ width: i === 1 ? 56 : 72, height: 12 }} />
        </td>
      ))}
    </tr>
  );
}

/* ── Empty / Error states ── */
function EmptyState() {
  return (
    <div className={styles.empty}>
      <div className={styles.emptyIcon}>
        <MIcon name="dns" size={40} />
      </div>
      <h2 className={styles.emptyTitle}>尚無虛擬機或容器</h2>
      <p className={styles.emptyDesc}>系統中還沒有任何虛擬機或 LXC 容器</p>
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
export default function ResourceMgmtPage() {
  const navigate = useNavigate();
  const [resources, setResources] = useState([]);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState(false);
  const [selectedVmids, setSelectedVmids] = useState(() => new Set());

  const selectableVmids = resources
    .filter((r) => r.can_control !== false && r.vmid != null && r.vmid > 0)
    .map((r) => r.vmid);
  const allSelected = selectableVmids.length > 0 && selectableVmids.every((v) => selectedVmids.has(v));

  function toggleSelect(vmid) {
    setSelectedVmids((prev) => {
      const next = new Set(prev);
      if (next.has(vmid)) next.delete(vmid);
      else next.add(vmid);
      return next;
    });
  }

  function toggleSelectAll() {
    setSelectedVmids(allSelected ? new Set() : new Set(selectableVmids));
  }

  const fetchResources = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const data = await ResourcesService.listAll();
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
    setSelectedVmids((prev) => {
      if (!prev.has(vmid)) return prev;
      const next = new Set(prev);
      next.delete(vmid);
      return next;
    });
  }

  return (
    <div className={styles.page}>
      {/* ── 頁首 ── */}
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>虛擬機與容器</h1>
          <p className={styles.pageSubtitle}>查看與管理系統中所有虛擬機與 LXC 容器</p>
        </div>
        <div className={styles.pageActions}>
          <button type="button" className={styles.btnSecondary} onClick={fetchResources} disabled={loading}>
            <MIcon name="sync" size={16} />
            重新整理
          </button>
          <button type="button" className={styles.btnPrimary} onClick={() => navigate("/my-requests")}>
            <MIcon name="add" size={16} />
            建立資源
          </button>
        </div>
      </div>

      {/* ── 批次操作 ── */}
      <BatchActionBar
        selectedVmids={[...selectedVmids]}
        onDone={() => {
          setSelectedVmids(new Set());
          fetchResources();
        }}
        onClear={() => setSelectedVmids(new Set())}
      />

      {/* ── 內容 ── */}
      <div className={styles.content}>
        {error ? (
          <ErrorState onRetry={fetchResources} />
        ) : !loading && resources.length === 0 ? (
          <EmptyState />
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th className={`${styles.th} ${styles.checkCell}`}>
                    <input
                      type="checkbox"
                      className={styles.checkbox}
                      checked={allSelected}
                      disabled={selectableVmids.length === 0}
                      onChange={toggleSelectAll}
                      aria-label="全選"
                    />
                  </th>
                  {COLUMNS.map((col) => (
                    <th key={col} className={styles.th}>{col}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {loading
                  ? [0, 1, 2, 3].map((i) => <SkeletonRow key={i} />)
                  : resources.map((r, index) => (
                      <ResourceRow
                        key={resourceRowKey(r, index)}
                        resource={r}
                        onUpdated={handleUpdated}
                        onDeleted={handleDeleted}
                        selected={selectedVmids.has(r.vmid)}
                        onToggleSelect={toggleSelect}
                      />
                    ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
