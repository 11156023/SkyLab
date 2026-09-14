import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import styles from "./ResourceMgmtPage.module.scss";
import MIcon from "../../../components/MIcon";
import PowerMenu from "../../../components/PowerMenu/PowerMenu";
import TemplateConvertDialog from "../../../components/TemplateConvertDialog/TemplateConvertDialog";
import useDialogPresence from "../../../hooks/useDialogPresence";
import SharedEmptyState from "../../../components/EmptyState/EmptyState";
import { useToast } from "../../../hooks/useToast";
import useAutoRefresh from "../../../hooks/useAutoRefresh";
import LoadingState from "../../../components/LoadingState/LoadingState";
import { ResourcesService } from "../../../services/resources";
import TerminalDialog from "../../personal/resources/TerminalDialog";
import VncDialog from "../../personal/resources/VncDialog";
import PageHeader from "../../../components/PageHeader/PageHeader";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { QuickPracticeService } from "../../../services/quickPractice";
import { buildEnvironmentGroups, groupedResourceKeys } from "../../../utils/environmentGroups";
import { formatDate } from "../../../utils/formatDate";

/* ── Constants ── */
function useStatusMap() {
  const { t } = useTranslation("resource");
  return {
    scheduled:    { label: t("ResourceMgmtPage.statusScheduled"),    color: "info"    },
    provisioning: { label: t("ResourceMgmtPage.statusProvisioning"), color: "info"    },
    running:      { label: t("ResourceMgmtPage.statusRunning"),      color: "success" },
    stopped:      { label: t("ResourceMgmtPage.statusStopped"),      color: "muted"   },
    paused:       { label: t("ResourceMgmtPage.statusPaused"),       color: "muted"   },
    failed:       { label: t("ResourceMgmtPage.statusFailed"),       color: "danger"  },
    deleted:      { label: t("ResourceMgmtPage.statusDeleted"),      color: "danger"  },
    unknown:      { label: t("ResourceMgmtPage.statusUnknown"),      color: "muted"   },
  };
}

function useTypeMap() {
  const { t } = useTranslation("resource");
  return {
    lxc:  { label: t("ResourceMgmtPage.typeLxc"), icon: "terminal" },
    qemu: { label: t("ResourceMgmtPage.typeVm"),  icon: "computer" },
  };
}

function useActionLabel() {
  const { t } = useTranslation("resource");
  return {
    start:    t("ResourceMgmtPage.actionStart"),
    stop:     t("ResourceMgmtPage.actionStop"),
    shutdown: t("ResourceMgmtPage.actionShutdown"),
    reset:    t("ResourceMgmtPage.actionReset"),
    reboot:   t("ResourceMgmtPage.actionReboot"),
  };
}

function useColumns() {
  const { t } = useTranslation("resource");
  return [
    t("ResourceMgmtPage.columnName"),
    t("ResourceMgmtPage.columnEnvOs"),
    t("ResourceMgmtPage.columnStatus"),
    t("ResourceMgmtPage.columnIp"),
    t("ResourceMgmtPage.columnExpiry"),
    t("ResourceMgmtPage.columnNode"),
    t("ResourceMgmtPage.columnActions"),
  ];
}

function useBatchActions() {
  const { t } = useTranslation("resource");
  return [
    { action: "start",    label: t("ResourceMgmtPage.actionStart"),    icon: "play_arrow" },
    { action: "shutdown", label: t("ResourceMgmtPage.actionShutdown"), icon: "power_settings_new" },
    { action: "reboot",   label: t("ResourceMgmtPage.actionReboot"),   icon: "restart_alt" },
    { action: "stop",     label: t("ResourceMgmtPage.actionStop"),     icon: "stop" },
    { action: "reset",    label: t("ResourceMgmtPage.actionReset"),    icon: "cancel" },
  ];
}

const LIVE_STATUSES = new Set(["running", "stopped", "paused"]);

/* ── Helpers ── */
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

function machineSpecLabel(machine) {
  const parts = [];
  if (machine.cpu) parts.push(`${machine.cpu} CPU`);
  if (machine.memoryBytes) parts.push(`${Math.round(machine.memoryBytes / 1024 ** 3)} GB`);
  return parts.join(" · ");
}

/* ── Primitive sub-components ── */
function StatusBadge({ status }) {
  const statusMap = useStatusMap();
  const s = statusMap[status] ?? { label: status, color: "muted" };
  return (
    <span className={`${styles.badge} ${styles[`badge_${s.color}`]}`}>
      {s.label}
    </span>
  );
}

function EnvironmentMachineRow({ machine, onUpdated }) {
  const { t } = useTranslation("resource");
  const toast = useToast();
  const navigate = useNavigate();
  const typeMap = useTypeMap();
  const type = typeMap[machine.type] ?? { label: machine.type, icon: "computer" };
  const [consoleOpen, setConsoleOpen] = useState(false);
  const [actionLoading, setActionLoading] = useState(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuClosing, setMenuClosing] = useState(false);
  const menuBtnRef = useRef(null);
  const resource = machine.resource;
  const isLxc = machine.type === "lxc";
  const canControl = Boolean(resource?.vmid && resource.can_control !== false);
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
      toast.success(t("ResourceMgmtPage.machineCommandSent"));
    } catch (error) {
      toast.error(error?.message ?? t("ResourceMgmtPage.machineActionFailed"));
    } finally {
      setActionLoading(null);
    }
  }

  return <>
    <tr className={`${styles.tr} ${styles.environmentMachineRow}`}>
      <td className={`${styles.td} ${styles.checkCell}`} />
      <td className={styles.td}>
        <div className={`${styles.nameCell} ${styles.environmentMachineName}`}>
          <span className={styles.machineBranch} aria-hidden="true">└</span>
          <div>
            {resource?.vmid > 0
              ? <button type="button" className={`${styles.namePrimary} ${styles.nameLink}`} title={t("ResourceMgmtPage.viewDetailTitle")} onClick={() => navigate(`/resource-mgmt/${resource.vmid}`)}>{machine.name}</button>
              : <div className={styles.namePrimary}>{machine.name}</div>}
            <div className={styles.nameSub}>{machine.role} · {type.label}{specLabel ? ` · ${specLabel}` : ""}</div>
          </div>
        </div>
      </td>
      <td className={styles.td}>
        <div className={styles.envPrimary}>{machine.os}</div>
        <div className={styles.envSub}>{machine.resource ? t("ResourceMgmtPage.envConnected") : t("ResourceMgmtPage.envProvisioning")}</div>
      </td>
      <td className={styles.td}><StatusBadge status={machine.status} /></td>
      <td className={styles.td}><span className={styles.mono}>{machine.ip}</span></td>
      <td className={styles.td}><span className={styles.noAction}>{t("ResourceMgmtPage.unifiedManagement")}</span></td>
      <td className={styles.td}>{machine.node}</td>
      <td className={styles.td}><div className={styles.actions}>
        <button type="button" className={styles.consoleBtn} disabled={!canOpen} title={canOpen ? (isLxc ? t("ResourceMgmtPage.terminalTitle") : t("ResourceMgmtPage.consoleTitle")) : t("ResourceMgmtPage.machineNotReadyTitle")} onClick={() => setConsoleOpen(true)}>
          <MIcon name={isLxc ? "terminal" : "desktop_windows"} size={14} />
          {isLxc ? t("ResourceMgmtPage.terminalTitle") : t("ResourceMgmtPage.consoleTitle")}
        </button>
        {actionLoading && <MIcon name="hourglass_empty" size={16} />}
        {canControl && <div className={styles.menuWrap}>
          {menuOpen && <PowerMenu resource={resource} actionLoading={actionLoading} onControl={handleControl} onClose={closeMenu} anchorRef={menuBtnRef} closing={menuClosing} />}
          <button ref={menuBtnRef} type="button" className={`${styles.menuBtn} ${menuOpen ? styles.menuBtnActive : ""}`} onClick={() => menuOpen ? closeMenu() : setMenuOpen(true)} title={t("ResourceMgmtPage.powerControlTitle")}><MIcon name="more_vert" size={18} /></button>
        </div>}
      </div></td>
    </tr>
    {consoleOpen && isLxc && createPortal(<TerminalDialog resource={resource} onClose={() => setConsoleOpen(false)} />, document.body)}
    {consoleOpen && !isLxc && createPortal(<VncDialog resource={resource} onClose={() => setConsoleOpen(false)} />, document.body)}
  </>;
}

function EnvironmentGroupRows({ group, onUpdated, onRefresh }) {
  const { t } = useTranslation("resource");
  const toast = useToast();
  const [expanded, setExpanded] = useState(true);
  const [groupAction, setGroupAction] = useState(null);
  const running = group.machines.filter((machine) => machine.status === "running").length;
  const allRunning = running === group.machines.length;
  const controllableVmids = group.machines
    .filter((machine) => machine.resource?.vmid && machine.resource.can_control !== false)
    .map((machine) => machine.resource.vmid);

  async function runGroupAction(action) {
    if (!controllableVmids.length || groupAction) return;
    setGroupAction(action);
    try {
      await ResourcesService.batchAction(controllableVmids, action);
      toast.success(t("ResourceMgmtPage.groupCommandSent"));
      onRefresh?.();
    } catch (error) {
      toast.error(error?.message ?? t("ResourceMgmtPage.groupCommandFailed"));
    } finally {
      setGroupAction(null);
    }
  }
  return (
    <>
      <tr
        className={`${styles.tr} ${styles.environmentGroupRow}`}
        onClick={(event) => {
          /* 整列都可以開合，但列內的按鈕與勾選框各自處理自己的點擊 */
          if (event.target.closest("button, input, label")) return;
          setExpanded((value) => !value);
        }}
      >
        <td className={`${styles.td} ${styles.checkCell}`} />
        <td className={styles.td}>
          <button
            type="button"
            className={styles.environmentToggle}
            aria-expanded={expanded}
            onClick={() => setExpanded((value) => !value)}
          >
            <MIcon name={expanded ? "expand_more" : "chevron_right"} size={20} />
            <span><strong>{group.kindLabel}｜{group.title}</strong><small>{t("ResourceMgmtPage.machineCountLabel", { count: group.machines.length })}</small></span>
          </button>
        </td>
        <td className={styles.td}>
          <div className={styles.envPrimary}>{group.kind === "course" ? t("ResourceMgmtPage.courseEnvironment") : t("ResourceMgmtPage.quickPracticeEnvironment")}</div>
          <div className={styles.envSub}>{t("ResourceMgmtPage.groupView")}</div>
        </td>
        <td className={styles.td}>
          <span className={`${styles.badge} ${styles[`badge_${allRunning ? "success" : "info"}`]}`}>{t("ResourceMgmtPage.runningCount", { running, total: group.machines.length })}</span>
        </td>
        <td className={styles.td}><span className={styles.noAction}>—</span></td>
        <td className={styles.td}><strong className={styles.environmentTiming}>{group.timingLabel}</strong></td>
        <td className={styles.td}>{group.nodeLabel}</td>
        <td className={styles.td}>{controllableVmids.length > 0
          ? <div className={styles.actions}>
              <button type="button" className={styles.consoleBtn} disabled={Boolean(groupAction) || allRunning} onClick={() => runGroupAction("start")}><MIcon name={groupAction === "start" ? "hourglass_empty" : "play_arrow"} size={14} />{t("ResourceMgmtPage.startAll")}</button>
              <button type="button" className={styles.consoleBtn} disabled={Boolean(groupAction) || running === 0} onClick={() => runGroupAction("shutdown")}><MIcon name={groupAction === "shutdown" ? "hourglass_empty" : "power_settings_new"} size={14} />{t("ResourceMgmtPage.shutdownAll")}</button>
            </div>
          : <span className={styles.noAction}>—</span>}</td>
      </tr>
      {expanded && group.machines.map((machine) => (
        <EnvironmentMachineRow key={machine.id} machine={machine} onUpdated={onUpdated} />
      ))}
    </>
  );
}

/* ── 批次操作列（有勾選才顯示） ── */
function BatchActionBar({ selectedVmids, onDone, onClear }) {
  const { t } = useTranslation("resource");
  const toast = useToast();
  const confirm = useConfirm();
  const actionLabel = useActionLabel();
  const batchActions = useBatchActions();
  const [pending, setPending] = useState(null); // 進行中的 action
  const count = selectedVmids.length;

  async function run(action) {
    setPending(action);
    try {
      const res = await ResourcesService.batchAction(selectedVmids, action);
      const label = action === "delete" ? t("ResourceMgmtPage.delete") : actionLabel[action];
      if ((res?.failed ?? 0) === 0) {
        toast.success(t("ResourceMgmtPage.batchSuccessToast", { count: res?.succeeded ?? count, label }));
      } else {
        toast.error(t("ResourceMgmtPage.batchPartialFailToast", { label, succeeded: res?.succeeded ?? 0, failed: res?.failed }));
      }
      onDone();
    } catch (err) {
      toast.error(err?.message ?? t("ResourceMgmtPage.batchActionFailed"));
    } finally {
      setPending(null);
    }
  }

  async function confirmBatchDelete() {
    const ok = await confirm({
      title: t("ResourceMgmtPage.batchDeleteTitle", { count }),
      message: t("ResourceMgmtPage.batchDeleteDesc"),
      confirmText: t("ResourceMgmtPage.confirmDelete"),
      danger: true,
    });
    if (ok) run("delete");
  }

  if (count === 0) return null;

  return (
    <div className={styles.batchBar}>
      <span className={styles.batchCount}>{t("ResourceMgmtPage.selectedCount", { count })}</span>
      <span className={styles.batchDivider} />
      {batchActions.map(({ action, label, icon }) => (
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
        onClick={confirmBatchDelete}
      >
        <MIcon name="delete" size={14} />
        {t("ResourceMgmtPage.delete")}
      </button>
      <button
        type="button"
        className={styles.btnGhost}
        disabled={pending !== null}
        onClick={onClear}
      >
        {t("ResourceMgmtPage.clearSelection")}
      </button>

    </div>
  );
}

function ResourceRow({ resource, onUpdated, onDeleted, selected = false, onToggleSelect = null }) {
  const { t } = useTranslation("resource");
  const toast = useToast();
  const navigate = useNavigate();
  const typeMap = useTypeMap();
  const statusMap = useStatusMap();
  const actionLabel = useActionLabel();
  const confirm = useConfirm();
  const [actionLoading, setActionLoading] = useState(null);
  const [deleting, setDeleting]           = useState(false);
  const [menuOpen, setMenuOpen]           = useState(false);
  const [menuClosing, setMenuClosing]     = useState(false);
  const [consoleOpen, setConsoleOpen]     = useState(false);
  const [convertOpen, setConvertOpen]     = useState(false);
  const convertDialog = useDialogPresence(convertOpen);
  /* 課堂機器由老師統一管理，不轉範本；申請中的佔位列也沒有實體可轉 */
  const canConvertTemplate = resource.allocation_scope !== "teaching_class"
    && !resource.is_placeholder
    && resource.vmid > 0;
  const menuBtnRef = useRef(null);

  function closeMenu() {
    setMenuClosing(true);
    setTimeout(() => { setMenuOpen(false); setMenuClosing(false); }, 130);
  }

  const type  = typeMap[resource.type] ?? { label: resource.type, icon: "computer" };
  const isLxc = resource.type === "lxc";
  const canControl = resource.can_control !== false && resource.vmid != null && resource.vmid > 0;
  const isLive = canControl && LIVE_STATUSES.has(resource.status);

  async function handleControl(action) {
    setActionLoading(action);
    try {
      await ResourcesService[action](resource.vmid);
      toast.success(t("ResourceMgmtPage.controlCommandSent", { action: actionLabel[action], name: resource.name }));
      onUpdated({ ...resource, status: statusAfterAction(action) });
    } catch (err) {
      toast.error(err?.message ?? t("ResourceMgmtPage.controlActionFailed", { action: actionLabel[action] }));
    } finally {
      setActionLoading(null);
    }
  }

  async function handleDelete() {
    if (deleting) return;
    const ok = await confirm({
      title: t("ResourceMgmtPage.deleteResourceTitle"),
      message: t("ResourceMgmtPage.deleteResourceDesc", { name: resource.name, vmid: resource.vmid }),
      confirmText: t("ResourceMgmtPage.delete"),
      danger: true,
    });
    if (!ok) return;
    setDeleting(true);
    try {
      await ResourcesService.delete(resource.vmid);
      toast.success(t("ResourceMgmtPage.deleteRequestSent", { name: resource.name }));
      onDeleted(resource.vmid);
    } catch (err) {
      toast.error(err?.message ?? t("ResourceMgmtPage.deleteFailed"));
    } finally {
      setDeleting(false);
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
              aria-label={t("ResourceMgmtPage.selectRowAria", { name: resource.name })}
            />
          ) : null}
        </td>
        {/* 名稱 */}
        <td className={styles.td}>
          <div className={styles.nameCell}>
            <div>
              {resource.vmid > 0 ? (
                <button
                  type="button"
                  className={`${styles.namePrimary} ${styles.nameLink}`}
                  title={t("ResourceMgmtPage.viewDetailTitle")}
                  onClick={() => navigate(`/resource-mgmt/${resource.vmid}`)}
                >
                  {resource.name}
                </button>
              ) : (
                <div className={styles.namePrimary}>{resource.name}</div>
              )}
              <div className={styles.nameSub}>
                {type.label}
                {resource.vmid > 0 && t("ResourceMgmtPage.vmidSuffix", { vmid: resource.vmid })}
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
            : <span className={styles.noExpiry}>{t("ResourceMgmtPage.noExpiry")}</span>
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
                title={isLxc ? t("ResourceMgmtPage.terminalTitle") : t("ResourceMgmtPage.consoleTitle")}
                disabled={resource.status !== "running"}
                onClick={() => setConsoleOpen(true)}
              >
                <MIcon name={isLxc ? "terminal" : "desktop_windows"} size={14} />
                {isLxc ? t("ResourceMgmtPage.terminalTitle") : t("ResourceMgmtPage.consoleTitle")}
              </button>
              {actionLoading && <MIcon name="hourglass_empty" size={16} />}
              <div className={styles.menuWrap}>
                {menuOpen && (
                  <PowerMenu
                    resource={resource}
                    actionLoading={actionLoading}
                    onControl={handleControl}
                    onDeleteClick={() => { closeMenu(); handleDelete(); }}
                    onConvertTemplate={canConvertTemplate ? () => { closeMenu(); setConvertOpen(true); } : undefined}
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
                  title={t("ResourceMgmtPage.powerControlTitle")}
                >
                  <MIcon name="more_vert" size={18} />
                </button>
              </div>
            </div>
          ) : (
            <span className={styles.noAction}>
              {statusMap[resource.status]?.label ?? "—"}
            </span>
          )}
        </td>
      </tr>

      {consoleOpen && isLxc && createPortal(
        <TerminalDialog resource={resource} onClose={() => setConsoleOpen(false)} />,
        document.body,
      )}
      {consoleOpen && !isLxc && createPortal(
        <VncDialog resource={resource} onClose={() => setConsoleOpen(false)} />,
        document.body,
      )}
      {convertDialog.open && createPortal(
        <TemplateConvertDialog
          resource={resource}
          closing={convertDialog.closing}
          onClose={() => setConvertOpen(false)}
          onDone={() => onDeleted(resource.vmid)}
        />,
        document.body,
      )}
    </>
  );
}

/* ── Empty / Error states ── */
function EmptyState() {
  const { t } = useTranslation("resource");
  return <SharedEmptyState icon="dns" title={t("ResourceMgmtPage.emptyTitle")} />;
}

function ErrorState({ onRetry }) {
  const { t } = useTranslation("resource");
  return (
    <EmptyState
      icon="error_outline"
      title={t("ResourceMgmtPage.loadErrorTitle")}
      action={
        <button type="button" className={styles.btnSecondary} onClick={onRetry}>
          <MIcon name="refresh" size={16} />
          {t("ResourceMgmtPage.retry")}
        </button>
      }
    />
  );
}

/* ── Page ── */
export default function ResourceMgmtPage() {
  const { t } = useTranslation("resource");
  const navigate = useNavigate();
  const [resources, setResources] = useState([]);
  const [quickSessions, setQuickSessions] = useState([]);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState(false);
  const [selectedVmids, setSelectedVmids] = useState(() => new Set());

  const environmentGroups = buildEnvironmentGroups(resources, quickSessions);
  const grouped = groupedResourceKeys(environmentGroups);
  const visibleResources = resources.filter((resource) => (
    !grouped.vmids.has(resource.vmid)
    && !grouped.requestIds.has(String(resource.request_id))
  ));
  const selectableVmids = visibleResources
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

  /** silent = true 時不觸發 loading / error state，供背景自動刷新使用 */
  const fetchResources = useCallback(async (silent = false, signal) => {
    if (!silent) {
      setLoading(true);
      setError(false);
    }
    try {
      const [data, sessions] = await Promise.all([
        ResourcesService.listAll({ signal }),
        QuickPracticeService.listAllSessions({ signal }).catch(() => []),
      ]);
      setResources(data ?? []);
      setQuickSessions(sessions ?? []);
    } catch (err) {
      if (!silent && !err?.cancelled) setError(true);
    } finally {
      if (!silent && !signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    fetchResources(false, controller.signal);
    return () => controller.abort();
  }, [fetchResources]);
  useAutoRefresh(() => fetchResources(true));

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

  const columns = useColumns();

  return (
    <div className={styles.page}>
      {/* ── 頁首 ── */}
      <PageHeader title={t("ResourceMgmtPage.pageTitle")} subtitle={t("ResourceMgmtPage.pageSubtitle")}>
        <div className={styles.pageActions}>
          <button type="button" className={styles.btnPrimary} onClick={() => navigate("/my-requests")}>
            <MIcon name="add" size={16} />
            {t("ResourceMgmtPage.createResource")}
          </button>
        </div>
      </PageHeader>

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
        ) : loading ? (
          <LoadingState fullPage />
        ) : visibleResources.length === 0 && environmentGroups.length === 0 ? (
          <EmptyState />
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <colgroup>
                <col className={styles.colCheck} />
                <col />
                <col className={styles.colEnv} />
                <col className={styles.colStatus} />
                <col className={styles.colIp} />
                <col className={styles.colExpiry} />
                <col className={styles.colNode} />
                <col className={styles.colActions} />
              </colgroup>
              <thead>
                <tr>
                  <th className={`${styles.th} ${styles.checkCell}`}>
                    <input
                      type="checkbox"
                      className={styles.checkbox}
                      checked={allSelected}
                      disabled={selectableVmids.length === 0}
                      onChange={toggleSelectAll}
                      aria-label={t("ResourceMgmtPage.selectAllAria")}
                    />
                  </th>
                  {columns.map((col) => (
                    <th key={col} className={styles.th}>{col}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {environmentGroups.map((group) => <EnvironmentGroupRows key={group.id} group={group} onUpdated={handleUpdated} onRefresh={() => fetchResources(true)} />)}
                {visibleResources.map((r, index) => (
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
