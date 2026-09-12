import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../../contexts/AuthContext";
import styles from "./ResourcesPage.module.scss";
import MIcon from "../../../components/MIcon";
import PowerMenu from "../../../components/PowerMenu/PowerMenu";
import TemplateConvertDialog from "../../../components/TemplateConvertDialog/TemplateConvertDialog";
import useDialogPresence from "../../../hooks/useDialogPresence";
import SharedEmptyState from "../../../components/EmptyState/EmptyState";
import LoadingState from "../../../components/LoadingState/LoadingState";
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
import QuotaUsageBar from "../../../components/Teaching/QuotaUsageBar";
import PageHeader from "../../../components/PageHeader/PageHeader";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { QuickPracticeService } from "../../../services/quickPractice";
import { buildEnvironmentGroups, groupedResourceKeys } from "../../../utils/environmentGroups";
import * as fmt from "../../../utils/formatDate";

/* ── Constants ── */
const STATUS_MAP = {
  scheduled:    { labelKey: "ResourcesPage.statusScheduled",     color: "info",    icon: "event"          },
  provisioning: { labelKey: "ResourcesPage.statusProvisioning",  color: "info",    icon: "settings"       },
  partial_failed:{ labelKey: "ResourcesPage.statusPartialFailed",color: "danger",  icon: "error_outline"  },
  running:      { labelKey: "ResourcesPage.statusRunning",       color: "success", icon: "play_circle"    },
  stopping:     { labelKey: "ResourcesPage.statusStopping",      color: "muted",   icon: "power_settings_new" },
  reclaiming:   { labelKey: "ResourcesPage.statusReclaiming",    color: "danger",  icon: "delete_sweep"   },
  stopped:      { labelKey: "ResourcesPage.statusStopped",       color: "muted",   icon: "stop_circle"    },
  paused:       { labelKey: "ResourcesPage.statusPaused",        color: "muted",   icon: "pause_circle"   },
  deleting:     { labelKey: "ResourcesPage.statusDeleting",      color: "danger",  icon: "hourglass_empty"},
  failed:       { labelKey: "ResourcesPage.statusFailed",        color: "danger",  icon: "error_outline"  },
  deleted:      { labelKey: "ResourcesPage.statusDeleted",       color: "danger",  icon: "delete_forever" },
  unknown:      { labelKey: "ResourcesPage.statusUnknown",       color: "muted",   icon: "help_outline"   },
};

const TYPE_MAP = {
  lxc:   { labelKey: "ResourcesPage.typeLxc", icon: "terminal" },
  qemu:  { labelKey: "ResourcesPage.typeQemu", icon: "computer" },
};

const DESKTOP_CLIENT_DOWNLOAD_URL = import.meta.env.VITE_DESKTOP_CLIENT_DOWNLOAD_URL
  || "https://github.com/1Ray0/SkyLab-Connect-Releases/releases/latest/download/SkyLab-Connect-Setup.exe";

/* ── Helpers ── */
function formatDate(isoStr) {
  return fmt.formatDate(isoStr, null);
}

function formatDatetime(isoStr) {
  return fmt.formatDateTime(isoStr, null);
}

/* ── Primitive sub-components ── */
/* reboot / reset 之後機器仍是開著的；原本一律當成 stopped 會讓列上的狀態說謊。 */
function statusAfterAction(action) {
  return action === "stop" || action === "shutdown" ? "stopped" : "running";
}

function StatusBadge({ status }) {
  const { t } = useTranslation("personal");
  const s = STATUS_MAP[status] ?? { label: status, color: "muted", icon: "help_outline" };
  return (
    <span className={`${styles.badge} ${styles[`badge_${s.color}`]}`}>
      {s.labelKey ? t(s.labelKey) : s.label}
    </span>
  );
}

/* ── Confirm Modal ── */
/* ── Creating placeholder row ── */

/** 依申請階段決定 placeholder 的狀態顯示（開通中 / 超時 / 失敗…） */
function getCreatingDisplay(req, t) {
  if (req.status === "pending") {
    return { label: t("CreatingRow.statusPendingReview"), color: "info", spin: true };
  }
  if (req.provisioning_status === "failed") {
    return { label: t("CreatingRow.statusProvisionFailed"), color: "danger", spin: false };
  }
  if (req.provisioning_status === "running") {
    return { label: t("CreatingRow.statusProvisioning"), color: "info", spin: true };
  }
  // approved 等待排程開機：start_at 已過但仍未開始建立 → 超時
  if (req.start_at && new Date(req.start_at).getTime() < Date.now()) {
    const overdueMin = Math.floor((Date.now() - new Date(req.start_at).getTime()) / 60_000);
    const overdueLabel = overdueMin >= 60
      ? t("CreatingRow.overdueHoursLabel", { count: Math.floor(overdueMin / 60) })
      : t("CreatingRow.overdueMinutesLabel", { count: overdueMin });
    return { label: t("CreatingRow.statusOverdue", { label: overdueLabel }), color: "danger", spin: false };
  }
  return { label: t("CreatingRow.statusScheduling"), color: "info", spin: true };
}

function formatMemory(memoryMb) {
  if (memoryMb == null) return null;
  return memoryMb >= 1024 ? `${memoryMb / 1024} GB` : `${memoryMb} MB`;
}

function CreatingRow({ request, onCancelled }) {
  const { t } = useTranslation("personal");
  const toast = useToast();
  const confirm = useConfirm();
  const [cancelling, setCancelling]       = useState(false);

  const type    = TYPE_MAP[request.resource_type === "lxc" ? "lxc" : "qemu"];
  const display = getCreatingDisplay(request, t);
  // 開通流程一旦開始跑 Proxmox clone 就無法取消
  const canCancel = request.provisioning_status !== "running";

  async function handleCancel() {
    const ok = await confirm({
      title: t("CreatingRow.confirmCancelTitle"),
      message: t("CreatingRow.confirmCancelDesc", { hostname: request.hostname }),
      confirmText: t("CreatingRow.confirmCancelLabel"),
      danger: true,
    });
    if (!ok) return;
    setCancelling(true);
    try {
      await cancelVmRequest(request.id);
      toast.success(t("CreatingRow.cancelRequestSuccess", { hostname: request.hostname }));
      onCancelled();
    } catch (err) {
      toast.error(err?.message ?? t("CreatingRow.cancelRequestFailed"));
    } finally {
      setCancelling(false);
    }
  }

  const specs = [
    request.cores != null ? t("CreatingRow.coresLabel", { count: request.cores }) : null,
    formatMemory(request.memory),
  ].filter(Boolean).join(" / ");

  return <>
    <tr className={`${styles.tr} ${styles.pendingRow}`}>
      <td className={styles.td}>
        <div className={styles.nameCell}>
          <span className={styles.nameIcon}><MIcon name={type.icon} size={18} /></span>
          <div><strong>{request.hostname}</strong><small>{t(type.labelKey)} · {specs || t("CreatingRow.specsPending")}</small></div>
        </div>
      </td>
      <td className={styles.td}><div className={styles.envPrimary}>{t("CreatingRow.resourceRequestLabel")}</div><div className={styles.envSub}>{t("CreatingRow.creating")}</div></td>
      <td className={styles.td}>
        <span className={`${styles.badge} ${styles[`badge_${display.color}`]} ${styles.creatingBadge}`}>
          <span className={display.spin ? styles.spin : styles.badgeIcon}><MIcon name={display.spin ? "autorenew" : "error_outline"} size={12} /></span>{display.label}
        </span>
      </td>
      <td className={styles.td}><span className={styles.muted}>N/A</span></td>
      <td className={styles.td}>{formatDatetime(request.start_at) ?? formatDatetime(request.created_at)}</td>
      <td className={styles.td}>{request.assigned_node ?? request.desired_node ?? t("CreatingRow.notAssigned")}</td>
      <td className={styles.td}>
        <button type="button" className={styles.cancelBtn} disabled={!canCancel || cancelling} onClick={handleCancel}>
          <MIcon name="cancel" size={14} />{t("CreatingRow.cancelRequest")}
        </button>
      </td>
    </tr>
  </>;
}

const LIVE_STATUSES = new Set(["running", "stopped", "paused"]);

function resourceRowKey(resource, index) {
  const parts = [
    resource.type || "resource",
    resource.node || "unknown-node",
    resource.vmid ?? resource.request_id ?? resource.name ?? "unknown",
  ];
  return `${parts.join(":")}:${index}`;
}

/* ── Resource row ── */
function ResourceRow({ resource, onUpdated, onDeleted }) {
  const { t } = useTranslation("personal");
  const navigate = useNavigate();
  const { user } = useAuth();
  /* VMID 是系統內部編號，僅管理員／老師看得到 */
  const showVmid = user?.is_superuser || user?.role === "admin" || user?.role === "teacher";
  /* 轉成範本只給老師／管理員，且只有自己能管理的個人機器 */
  const canConvertTemplate = showVmid
    && resource.can_manage !== false
    && resource.allocation_scope !== "teaching_class"
    && !resource.is_placeholder
    && resource.vmid > 0;
  const confirm = useConfirm();
  const [actionLoading, setActionLoading] = useState(null);
  const [deleting, setDeleting]            = useState(false);
  const [menuOpen, setMenuOpen]            = useState(false);
  const [menuClosing, setMenuClosing]      = useState(false);
  const [consoleOpen, setConsoleOpen]      = useState(false);
  const [convertOpen, setConvertOpen]      = useState(false);
  const convertDialog = useDialogPresence(convertOpen);
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
      onUpdated({ ...resource, status: statusAfterAction(action) });
    } finally {
      setActionLoading(null);
    }
  }

  async function handleDelete() {
    if (deleting) return;
    const ok = await confirm({
      title: t("ResourceRow.confirmDeleteTitle"),
      message: showVmid
        ? t("ResourceRow.confirmDeleteDescWithVmid", { name: resource.name, vmid: resource.vmid })
        : t("ResourceRow.confirmDeleteDescNoVmid", { name: resource.name }),
      confirmText: t("ResourceRow.confirmDeleteLabel"),
      danger: true,
    });
    if (!ok) return;
    setDeleting(true);
    try {
      await ResourcesService.delete(resource.vmid);
      onDeleted(resource.vmid);
    } finally {
      setDeleting(false);
    }
  }

  return <>
    <tr className={styles.tr} data-guide="resource-card">
      <td className={styles.td}>
        <div className={styles.nameCell}>
          <span className={styles.nameIcon}><MIcon name={type.icon} size={18} /></span>
          <div>
            {resource.vmid > 0
              ? <button type="button" className={styles.nameLink} onClick={() => navigate(`/my-resources/${resource.vmid}`)}>{resource.name}</button>
              : <strong>{resource.name}</strong>}
            <small>{t(type.labelKey)}{showVmid && resource.vmid > 0 ? t("ResourceRow.vmidSuffix", { vmid: resource.vmid }) : ""}</small>
            {(resource.access_role === "shared" || (resource.tags ?? []).length > 0) && (
              <div className={styles.rowChips}>
                {resource.access_role === "shared" && (
                  <span className={`${styles.badge} ${styles.badge_info}`} title={t("ResourceRow.sharedByHint", { email: resource.owner_email ?? "—" })}>
                    <MIcon name="group" size={11} /> {t("ResourceRow.sharedBadge")}
                  </span>
                )}
                {(resource.tags ?? []).map((tag) => (
                  <span key={tag} className={styles.tagChip}>{tag}</span>
                ))}
              </div>
            )}
          </div>
        </div>
      </td>
      <td className={styles.td}><div className={styles.envPrimary}>{resource.environment_type || "Custom"}</div><div className={styles.envSub}>{resource.os_info || "—"}</div></td>
      <td className={styles.td}><StatusBadge status={resource.status} /></td>
      <td className={styles.td}><span className={styles.mono}>{resource.ip_address ?? "N/A"}</span></td>
      <td className={styles.td}>{resource.expiry_date ? formatDate(resource.expiry_date) : <span className={styles.cardPeriodUnlimited}>{t("ResourceRow.unlimited")}</span>}</td>
      <td className={styles.td}>{resource.node ?? "—"}</td>
      <td className={styles.td}>
        {isLive ? <div className={styles.rowActions}>
          <button type="button" className={styles.terminalBtn} disabled={resource.status !== "running"} onClick={() => setConsoleOpen(true)} data-guide="resource-console">
            <MIcon name={isLxc ? "terminal" : "desktop_windows"} size={14} />{isLxc ? t("ResourceRow.terminal") : t("ResourceRow.console")}
          </button>
          {actionLoading && <MIcon name="hourglass_empty" size={16} />}
          <div className={styles.menuWrap}>
            {menuOpen && <PowerMenu resource={resource} actionLoading={actionLoading} onControl={handleControl} onDeleteClick={resource.can_delete === false ? undefined : () => { closeMenu(); handleDelete(); }} onConvertTemplate={canConvertTemplate ? () => { closeMenu(); setConvertOpen(true); } : undefined} onClose={closeMenu} anchorRef={menuBtnRef} closing={menuClosing} />}
            <button ref={menuBtnRef} type="button" className={`${styles.menuBtn} ${menuOpen ? styles.menuBtnActive : ""}`} onClick={() => menuOpen ? closeMenu() : setMenuOpen(true)} title={t("ResourceRow.moreActions")}><MIcon name="more_vert" size={18} /></button>
          </div>
        </div> : <span className={styles.deletedNote}>{STATUS_MAP[resource.status]?.labelKey ? t(STATUS_MAP[resource.status].labelKey) : resource.status}</span>}
      </td>
    </tr>
    {consoleOpen && isLxc && createPortal(<TerminalDialog resource={resource} onClose={() => setConsoleOpen(false)} />, document.body)}
    {consoleOpen && !isLxc && createPortal(<VncDialog resource={resource} onClose={() => setConsoleOpen(false)} />, document.body)}
    {convertDialog.open && createPortal(<TemplateConvertDialog resource={resource} closing={convertDialog.closing} onClose={() => setConvertOpen(false)} onDone={() => onDeleted(resource.vmid)} />, document.body)}
  </>;
}

function machineSpecLabel(machine) {
  const parts = [];
  if (machine.cpu) parts.push(`${machine.cpu} CPU`);
  if (machine.memoryBytes) parts.push(`${Math.round(machine.memoryBytes / 1024 ** 3)} GB`);
  return parts.join(" · ");
}

function EnvironmentMachineRow({ machine, groupStatus, onUpdated }) {
  const { t } = useTranslation("personal");
  const toast = useToast();
  const navigate = useNavigate();
  const type = TYPE_MAP[machine.type] ?? { label: machine.type, icon: "computer" };
  const [consoleOpen, setConsoleOpen] = useState(false);
  const [actionLoading, setActionLoading] = useState(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuClosing, setMenuClosing] = useState(false);
  const menuBtnRef = useRef(null);
  const resource = machine.resource;
  const isLxc = machine.type === "lxc";
  const environmentReady = ["running", "active"].includes(groupStatus);
  const canControl = Boolean(
    environmentReady && resource?.vmid && resource.can_control !== false,
  );
  const canOpen = canControl && resource.status === "running";
  const specLabel = machineSpecLabel(machine);

  function closeMenu() {
    setMenuClosing(true);
    setTimeout(() => { setMenuOpen(false); setMenuClosing(false); }, 130);
  }

  // 與單機列同一組電源控制；環境內的機器差別只在不能單台刪除。
  async function handleControl(action) {
    if (!canControl || actionLoading) return;
    setActionLoading(action);
    try {
      await ResourcesService[action](resource.vmid);
      onUpdated({ ...resource, status: statusAfterAction(action) });
      toast.success(t("EnvironmentMachineRow.commandSent"));
    } catch (error) {
      toast.error(error?.message ?? t("EnvironmentMachineRow.controlFailed"));
    } finally {
      setActionLoading(null);
    }
  }

  return <>
    <tr className={`${styles.tr} ${styles.environmentMachineRow}`}>
    <td className={styles.td}><div className={`${styles.nameCell} ${styles.environmentMachineName}`}><span className={styles.machineBranch}>└</span><div>{resource?.vmid > 0
      ? <button type="button" className={styles.nameLink} onClick={() => navigate(`/my-resources/${resource.vmid}`)}>{machine.name}</button>
      : <strong>{machine.name}</strong>}<small>{machine.role} · {t(type.labelKey ?? type.label)}{specLabel ? ` · ${specLabel}` : ""}</small></div></div></td>
    <td className={styles.td}><div className={styles.envPrimary}>{machine.os}</div><div className={styles.envSub}>{machine.resource ? t("EnvironmentMachineRow.resourceConnected") : t("EnvironmentMachineRow.creating")}</div></td>
    <td className={styles.td}><StatusBadge status={machine.status} /></td>
    <td className={styles.td}><span className={styles.mono}>{machine.ip}</span>
      {machine.publicUrl && <a className={styles.publicUrlLink} href={machine.publicUrl} target="_blank" rel="noreferrer"><MIcon name="open_in_new" size={13} />{machine.publicUrl.replace(/^https?:\/\//, "")}</a>}</td>
    <td className={styles.td}><span className={styles.muted}>{t("EnvironmentMachineRow.managedByEnvironment")}</span></td>
    <td className={styles.td}>{machine.node}</td>
    <td className={styles.td}><div className={styles.rowActions}>
      <button type="button" className={styles.terminalBtn} disabled={!canOpen} title={canOpen ? (isLxc ? t("EnvironmentMachineRow.terminal") : t("EnvironmentMachineRow.console")) : t("EnvironmentMachineRow.notReadyTitle")} onClick={() => setConsoleOpen(true)}><MIcon name={isLxc ? "terminal" : "desktop_windows"} size={14} />{isLxc ? t("EnvironmentMachineRow.terminal") : t("EnvironmentMachineRow.console")}</button>
      {actionLoading && <MIcon name="hourglass_empty" size={16} />}
      {canControl && <div className={styles.menuWrap}>
        {menuOpen && <PowerMenu resource={resource} actionLoading={actionLoading} onControl={handleControl} onClose={closeMenu} anchorRef={menuBtnRef} closing={menuClosing} />}
        <button ref={menuBtnRef} type="button" className={`${styles.menuBtn} ${menuOpen ? styles.menuBtnActive : ""}`} onClick={() => menuOpen ? closeMenu() : setMenuOpen(true)} title={t("ResourceRow.moreActions")}><MIcon name="more_vert" size={18} /></button>
      </div>}
    </div></td>
    </tr>
    {consoleOpen && isLxc && createPortal(<TerminalDialog resource={resource} onClose={() => setConsoleOpen(false)} />, document.body)}
    {consoleOpen && !isLxc && createPortal(<VncDialog resource={resource} onClose={() => setConsoleOpen(false)} />, document.body)}
  </>;
}

function EnvironmentGroupRows({ group, onUpdated, onEnded }) {
  const { t } = useTranslation("personal");
  const confirm = useConfirm();
  const [expanded, setExpanded] = useState(true);
  const [ending, setEnding] = useState(false);
  const [groupAction, setGroupAction] = useState(null);
  const toast = useToast();
  const canEnd = group.kind === "quick_practice" && !["reclaiming", "reclaimed"].includes(group.status);
  const controllableVmids = group.machines
    .filter((machine) => machine.resource?.vmid && machine.resource.can_control !== false)
    .map((machine) => machine.resource.vmid);
  const runningCount = group.machines.filter((machine) => machine.status === "running").length;

  async function runGroupAction(action) {
    if (!controllableVmids.length || groupAction) return;
    setGroupAction(action);
    try {
      await ResourcesService.batchAction(controllableVmids, action);
      toast.success(t("EnvironmentGroupRows.groupCommandSent"));
      onEnded?.();
    } catch (error) {
      toast.error(error?.message ?? t("EnvironmentGroupRows.groupCommandFailed"));
    } finally {
      setGroupAction(null);
    }
  }

  async function endPractice() {
    const ok = await confirm({
      title: t("EnvironmentGroupRows.confirmEndTitle"),
      message: t("EnvironmentGroupRows.confirmEndDesc"),
      confirmText: t("EnvironmentGroupRows.endPractice"),
      danger: true,
    });
    if (!ok) return;
    setEnding(true);
    try {
      await QuickPracticeService.endSession(group.id);
      toast.success(t("EnvironmentGroupRows.endPracticeSuccess"));
      onEnded?.();
    } catch (error) {
      toast.error(error?.message ?? t("EnvironmentGroupRows.endPracticeFailed"));
    } finally {
      setEnding(false);
    }
  }

  return <>
    <tr
      className={`${styles.tr} ${styles.environmentGroupRow}`}
      onClick={(event) => {
        /* 整列都可以開合，但列內的按鈕（結束練習、名稱區的 toggle）各自處理自己的點擊 */
        if (event.target.closest("button")) return;
        setExpanded((value) => !value);
      }}
    >
      <td className={styles.td}><button type="button" className={styles.environmentToggle} aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}><MIcon name={expanded ? "expand_more" : "chevron_right"} size={20} /><span><strong>{group.kindLabel}｜{group.title}</strong><small>{t("EnvironmentGroupRows.machineCount", { count: group.machines.length })}</small></span></button></td>
      <td className={styles.td}><div className={styles.envPrimary}>{group.kind === "course" ? t("EnvironmentGroupRows.courseEnv") : t("EnvironmentGroupRows.quickPracticeEnv")}</div><div className={styles.envSub}>{t("EnvironmentGroupRows.groupOverview")}</div></td>
      <td className={styles.td}><StatusBadge status={group.status} /></td>
      <td className={styles.td}><span className={styles.muted}>{t("EnvironmentGroupRows.runningCount", { running: runningCount, total: group.machines.length })}</span></td>
      <td className={styles.td}><strong className={styles.environmentTiming}>{group.timingLabel}</strong></td>
      <td className={styles.td}>{group.nodeLabel}</td>
      <td className={styles.td}><div className={styles.groupActions}>
        {controllableVmids.length > 0 && <>
          <button type="button" className={styles.terminalBtn} disabled={Boolean(groupAction) || runningCount === group.machines.length} onClick={() => runGroupAction("start")}><MIcon name={groupAction === "start" ? "hourglass_empty" : "play_arrow"} size={14} />{t("EnvironmentGroupRows.startAll")}</button>
          <button type="button" className={styles.terminalBtn} disabled={Boolean(groupAction) || runningCount === 0} onClick={() => runGroupAction("shutdown")}><MIcon name={groupAction === "shutdown" ? "hourglass_empty" : "power_settings_new"} size={14} />{t("EnvironmentGroupRows.shutdownAll")}</button>
        </>}
        {canEnd && <button type="button" className={styles.terminalBtn} disabled={ending} onClick={endPractice}><MIcon name="stop_circle" size={14} />{ending ? t("EnvironmentGroupRows.ending") : t("EnvironmentGroupRows.endPractice")}</button>}
      </div></td>
    </tr>
    {expanded && group.machines.map((machine) => <EnvironmentMachineRow key={machine.id} machine={machine} groupStatus={group.status} onUpdated={onUpdated} />)}
  </>;
}

/* ── Empty / Error states ── */
function EmptyState() {
  const { t } = useTranslation("personal");
  return <SharedEmptyState icon="dns" title={t("ResourcesPage.emptyTitle")} />;
}

function ErrorState({ onRetry }) {
  const { t } = useTranslation("personal");
  return (
    <EmptyState
      icon="error_outline"
      title={t("ResourcesPage.errorTitle")}
      action={
        <button type="button" className={styles.btnSecondary} onClick={onRetry}>
          <MIcon name="refresh" size={16} />
          {t("ResourcesPage.retry")}
        </button>
      }
    />
  );
}

/* ── Page ── */
export default function ResourcesPage() {
  const { t } = useTranslation("personal");
  const navigate = useNavigate();
  const [resources, setResources] = useState([]);
  const [quickSessions, setQuickSessions] = useState([]);
  const [pending, setPending]     = useState([]);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState(false);
  const [tagFilter, setTagFilter] = useState("");
  const pendingSigRef = useRef(null);

  /** silent = true 時不觸發 skeleton / error state，供背景同步使用 */
  const fetchResources = useCallback(async (silent = false, signal) => {
    if (!silent) {
      setLoading(true);
      setError(false);
    }
    try {
      const [data, sessions] = await Promise.all([
        ResourcesService.list({ signal }),
        QuickPracticeService.listMySessions({ signal }).catch(() => []),
      ]);
      setResources(data ?? []);
      setQuickSessions(sessions ?? []);
    } catch (err) {
      if (!silent && !err?.cancelled) setError(true);
    } finally {
      if (!silent && !signal?.aborted) setLoading(false);
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
    const controller = new AbortController();
    fetchResources(false, controller.signal);
    return () => controller.abort();
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

  // 建立中申請會同時出現在 pending 與資源 API；先移除 placeholder，避免重複列。
  const pendingRequestIds = new Set(pending.map((request) => String(request.id)));
  // 標籤篩選：Proxmox 上的 tags，由資源詳情的「標籤與備註」設定
  const allTags = [...new Set(resources.flatMap((resource) => resource.tags ?? []))].sort();
  const activeTag = allTags.includes(tagFilter) ? tagFilter : "";
  const resourcesForDisplay = resources.filter((resource) => !(
    resource.is_placeholder
    && resource.request_id != null
    && pendingRequestIds.has(String(resource.request_id))
  )).filter((resource) => !activeTag || (resource.tags ?? []).includes(activeTag));
  const environmentGroups = buildEnvironmentGroups(resourcesForDisplay, quickSessions);
  const grouped = groupedResourceKeys(environmentGroups);
  const visibleResources = resourcesForDisplay.filter((resource) => (
    !grouped.vmids.has(resource.vmid)
    && !grouped.requestIds.has(String(resource.request_id))
  ));
  const visiblePending = pending.filter((request) => !grouped.requestIds.has(String(request.id)));

  return (
    <div className={styles.page}>
      <PageHeader title={t("ResourcesPage.title")} subtitle={t("ResourcesPage.subtitle")}>
        <div className={styles.pageActions}>
          <a
            className={styles.btnSecondary}
            href={DESKTOP_CLIENT_DOWNLOAD_URL}
          >
            <MIcon name="download" size={16} />
            {t("ResourcesPage.downloadDesktopClient")}
          </a>
          <button
            type="button"
            className={styles.btnPrimary}
            onClick={() => navigate("/my-requests", { state: { create: true } })}
          >
            <MIcon name="add" size={16} />
            {t("ResourcesPage.requestResource")}
          </button>
        </div>
      </PageHeader>

      {/* 我的配額用量（模組 E） */}
      <QuotaUsageBar />

      {allTags.length > 0 && (
        <div className={styles.filterBar} data-guide="resource-tag-filter">
          <span className={styles.filterLabel}>
            <MIcon name="label" size={14} />
            {t("ResourcesPage.tagFilterLabel")}
          </span>
          <button
            type="button"
            className={`${styles.filterChip} ${!activeTag ? styles.filterChipActive : ""}`}
            onClick={() => setTagFilter("")}
          >
            {t("ResourcesPage.tagFilterAll")}
          </button>
          {allTags.map((tag) => (
            <button
              key={tag}
              type="button"
              className={`${styles.filterChip} ${activeTag === tag ? styles.filterChipActive : ""}`}
              onClick={() => setTagFilter(activeTag === tag ? "" : tag)}
            >
              {tag}
            </button>
          ))}
        </div>
      )}

      <div className={styles.content}>
        {error ? (
          <ErrorState onRetry={() => fetchResources()} />
        ) : loading ? (
          <LoadingState fullPage />
        ) : visibleResources.length === 0 && visiblePending.length === 0 && environmentGroups.length === 0 ? (
          <EmptyState />
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <colgroup>
                <col className={styles.colName} />
                <col className={styles.colEnv} />
                <col className={styles.colStatus} />
                <col className={styles.colIp} />
                <col className={styles.colExpiry} />
                <col className={styles.colNode} />
                <col className={styles.colActions} />
              </colgroup>
              <thead>
                <tr><th className={styles.th}>{t("ResourcesPage.colName")}</th><th className={styles.th}>{t("ResourcesPage.colEnvironment")}</th><th className={styles.th}>{t("ResourcesPage.colStatus")}</th><th className={styles.th}>{t("ResourcesPage.colIp")}</th><th className={styles.th}>{t("ResourcesPage.colExpiry")}</th><th className={styles.th}>{t("ResourcesPage.colNode")}</th><th className={styles.th}>{t("ResourcesPage.colActions")}</th></tr>
              </thead>
              <tbody>
                {environmentGroups.map((group) => <EnvironmentGroupRows key={group.id} group={group} onUpdated={handleUpdated} onEnded={() => fetchResources(true)} />)}
                {visiblePending.map((req) => <CreatingRow key={`creating:${req.id}`} request={req} onCancelled={refreshPending} />)}
                {visibleResources.map((r, index) => <ResourceRow key={resourceRowKey(r, index)} resource={r} onUpdated={handleUpdated} onDeleted={handleDeleted} />)}
              </tbody>
            </table>
          </div>
        )}
      </div>

    </div>
  );
}
