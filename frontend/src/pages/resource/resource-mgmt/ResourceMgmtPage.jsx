import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import styles from "./ResourceMgmtPage.module.scss";
import MIcon from "../../../components/MIcon";
import MachineKindBadge from "../../../components/MachineKindBadge/MachineKindBadge";
import PowerMenu from "../../../components/PowerMenu/PowerMenu";
import TemplateFormDialog from "../templates/TemplateFormDialog";
import useDialogPresence from "../../../hooks/useDialogPresence";
import useVirtualRows from "../../../hooks/useVirtualRows";
import SharedEmptyState from "../../../components/EmptyState/EmptyState";
import ErrorState from "../../../components/ErrorState/ErrorState";
import { useToast } from "../../../hooks/useToast";
import useAutoRefresh from "../../../hooks/useAutoRefresh";
import LoadingState from "../../../components/LoadingState/LoadingState";
import { ResourcesService } from "../../../services/resources";
import TerminalDialog from "../../personal/resources/TerminalDialog";
import VncDialog from "../../personal/resources/VncDialog";
import { BOOTING_POLL_INTERVAL, LIVE_STATUSES, machineSpecLabel, resourceRowKey, statusAfterAction } from "../../personal/resources/resourceRows";
import PageHeader from "../../../components/PageHeader/PageHeader";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { QuickPracticeService } from "../../../services/quickPractice";
import { buildEnvironmentGroups, groupedResourceKeys } from "../../../utils/environmentGroups";
import { formatExpiryDate } from "../../../utils/formatDate";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import DataToolbar from "../../../components/DataToolbar/DataToolbar";
import SelectCheckbox from "../../../components/DataToolbar/SelectCheckbox";
import ToolbarSearch from "../../../components/DataToolbar/ToolbarSearch";
import useRowSelection from "../../../hooks/useRowSelection";
import BatchActionBar from "./BatchActionBar";
import { filterGroups, filterResources, statusCounts } from "./resourceFilters";

/* ── Constants ── */
function useStatusMap() {
  const { t } = useTranslation("resource");
  return {
    scheduled:    { label: t("ResourceMgmtPage.statusScheduled"),    color: "info"    },
    provisioning: { label: t("ResourceMgmtPage.statusProvisioning"), color: "info"    },
    starting:     { label: t("ResourceMgmtPage.statusStarting"),     color: "info"    },
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
    t("ResourceMgmtPage.columnKind"),
    t("ResourceMgmtPage.columnEnvOs"),
    t("ResourceMgmtPage.columnStatus"),
    t("ResourceMgmtPage.columnIp"),
    t("ResourceMgmtPage.columnExpiry"),
    t("ResourceMgmtPage.columnNode"),
    t("ResourceMgmtPage.columnActions"),
  ];
}

/* ── Primitive sub-components ── */
function ExpiryDateValue({ value }) {
  const expiry = formatExpiryDate(value);
  if (!expiry) return "—";
  return (
    <>
      {expiry.date}
      {expiry.cutoff && <div className={styles.envSub} title={expiry.title}>{expiry.cutoff}</div>}
    </>
  );
}

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
  const menu = useDialogPresence(menuOpen, 130);
  const menuBtnRef = useRef(null);
  const resource = machine.resource;
  const isLxc = machine.type === "lxc";
  const canControl = Boolean(resource?.vmid && resource.can_control !== false);
  const canOpen = canControl && resource.status === "running";
  const specLabel = machineSpecLabel(machine);

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

  const canOpenDetail = resource?.vmid > 0;
  /* 整列可點進詳情；列內按鈕／選單的點擊不觸發導頁 */
  const openDetail = (event) => {
    if (event.target.closest("button, a, input, select, label")) return;
    navigate(`/resource-mgmt/${resource.vmid}`);
  };

  return <>
    <tr
      className={`${styles.tr} ${styles.environmentMachineRow} ${canOpenDetail ? styles.trClickable : ""}`}
      onClick={canOpenDetail ? openDetail : undefined}
    >
      <td className={`${styles.td} ${styles.checkCell}`} />
      <td className={styles.td}>
        <div className={`${styles.nameCell} ${styles.environmentMachineName}`}>
          <span className={styles.machineBranch} aria-hidden="true">└</span>
          <div>
            <div className={styles.namePrimary}>{machine.name}</div>
            <div className={styles.nameSub}>{machine.role} · {type.label}{specLabel ? ` · ${specLabel}` : ""}</div>
          </div>
        </div>
      </td>
      <td className={styles.td}><span className={styles.noAction}>—</span></td>
      <td className={styles.td}>
        <div className={styles.envPrimary}>{machine.os}</div>
        <div className={styles.envSub}>{machine.resource ? t("ResourceMgmtPage.envConnected") : t("ResourceMgmtPage.envProvisioning")}</div>
      </td>
      <td className={styles.td}><StatusBadge status={machine.status} /></td>
      <td className={styles.td}>
        <span className={styles.mono}>{machine.ip}</span>
        {machine.publicUrl && (
          <div className={styles.publicUrlList}>
            <a className={styles.publicUrlLink} href={machine.publicUrl} target="_blank" rel="noreferrer" title={machine.publicUrl}>
              <MIcon name="open_in_new" size={13} /><span className={styles.publicUrlText}>{machine.publicUrl.replace(/^https?:\/\//, "")}</span>
            </a>
          </div>
        )}
      </td>
      <td className={styles.td}><span className={styles.noAction}>{t("ResourceMgmtPage.unifiedManagement")}</span></td>
      <td className={styles.td}>{machine.node}</td>
      <td className={styles.td}><div className={styles.actions}>
        <button type="button" className={styles.consoleBtn} disabled={!canOpen} title={canOpen ? (isLxc ? t("ResourceMgmtPage.terminalTitle") : t("ResourceMgmtPage.consoleTitle")) : resource?.status === "starting" ? t("ResourceMgmtPage.consoleBootingTitle") : t("ResourceMgmtPage.machineNotReadyTitle")} onClick={() => setConsoleOpen(true)}>
          <MIcon name={isLxc ? "terminal" : "desktop_windows"} size={14} />
          {isLxc ? t("ResourceMgmtPage.terminalTitle") : t("ResourceMgmtPage.consoleTitle")}
        </button>
        {actionLoading && <MIcon name="hourglass_empty" size={16} spin />}
        {canControl && <div className={styles.menuWrap}>
          {menu.open && <PowerMenu resource={resource} actionLoading={actionLoading} onControl={handleControl} onClose={() => setMenuOpen(false)} anchorRef={menuBtnRef} closing={menu.closing} />}
          <button ref={menuBtnRef} type="button" className={`${styles.menuBtn} ${menu.open ? styles.menuBtnActive : ""}`} onClick={() => setMenuOpen((value) => !value)} title={t("ResourceMgmtPage.powerControlTitle")} aria-label={t("ResourceMgmtPage.powerControlTitle")}><MIcon name="more_vert" size={18} /></button>
        </div>}
      </div></td>
    </tr>
    {consoleOpen && isLxc && createPortal(<TerminalDialog resource={resource} onClose={() => setConsoleOpen(false)} />, document.body)}
    {consoleOpen && !isLxc && createPortal(<VncDialog resource={resource} onClose={() => setConsoleOpen(false)} />, document.body)}
  </>;
}

/* machines：篩選後要列出的機器（預設整組）；群組列的台數與整組開關機仍看整組 */
function EnvironmentGroupRows({ group, machines = group.machines, onUpdated, onRefresh }) {
  const { t } = useTranslation("resource");
  const toast = useToast();
  const actionLabel = useActionLabel();
  const [expanded, setExpanded] = useState(true);
  const [groupAction, setGroupAction] = useState(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const menu = useDialogPresence(menuOpen, 130);
  const menuBtnRef = useRef(null);
  const running = group.machines.filter((machine) => machine.status === "running").length;
  const allRunning = running === group.machines.length;
  const controllableVmids = group.machines
    .filter((machine) => machine.resource?.vmid && machine.resource.can_control !== false)
    .map((machine) => machine.resource.vmid);

  async function runGroupAction(action) {
    if (!controllableVmids.length || groupAction) return;
    setGroupAction(action);
    try {
      const res = await ResourcesService.batchActionInChunks(controllableVmids, action);
      /* 分批送出；部分失敗也不會丟例外，要看 failed 才知道有沒有機器沒動 */
      if ((res?.failed ?? 0) > 0) {
        toast.error(t("ResourceMgmtPage.batchPartialFailToast", {
          label: actionLabel[action] ?? action,
          succeeded: res?.succeeded ?? 0,
          failed: res.failed,
        }));
      } else {
        toast.success(t("ResourceMgmtPage.groupCommandSent"));
      }
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
        data-guide="resource-mgmt-env-group"
        onClick={(event) => {
          /* 整列都可以開合，但列內的按鈕與勾選框各自處理自己的點擊 */
          if (event.target.closest("button, input, label")) return;
          setExpanded((value) => !value);
        }}
      >
        <td className={`${styles.td} ${styles.checkCell}`}>
          <button
            type="button"
            className={styles.environmentToggle}
            aria-expanded={expanded}
            aria-label={t("ResourceMgmtPage.toggleGroupAria", { name: group.title })}
            onClick={() => setExpanded((value) => !value)}
          >
            <MIcon name={expanded ? "expand_more" : "chevron_right"} size={20} />
          </button>
        </td>
        <td className={styles.td}>
          <div className={styles.nameCell}>
            <div>
              <div className={styles.namePrimary}>{group.title}</div>
              <div className={styles.nameSub}>
                {machines.length < group.machines.length
                  ? t("ResourceMgmtPage.groupMatchCount", { shown: machines.length, count: group.machines.length })
                  : t("ResourceMgmtPage.machineCountLabel", { count: group.machines.length })}
              </div>
            </div>
          </div>
        </td>
        <td className={styles.td}>
          <MachineKindBadge kind={group.kind === "quick_practice" ? "quick_practice" : "teaching_class"} classRelation={group.classRelation} title={group.title} />
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
              {groupAction && <MIcon name="hourglass_empty" size={16} spin />}
              <div className={styles.menuWrap}>
                {menu.open && <PowerMenu
                  title={t("ResourceMgmtPage.groupPowerTitle")}
                  items={[
                    { action: "start", label: t("ResourceMgmtPage.startAll"), icon: "play_arrow", tone: "ok", disabled: allRunning },
                    { action: "shutdown", label: t("ResourceMgmtPage.shutdownAll"), icon: "power_settings_new", disabled: running === 0 },
                  ]}
                  actionLoading={groupAction}
                  onControl={runGroupAction}
                  onClose={() => setMenuOpen(false)}
                  anchorRef={menuBtnRef}
                  closing={menu.closing}
                />}
                <button ref={menuBtnRef} type="button" className={`${styles.menuBtn} ${menu.open ? styles.menuBtnActive : ""}`} onClick={() => setMenuOpen((value) => !value)} title={t("ResourceMgmtPage.groupPowerTitle")} aria-label={t("ResourceMgmtPage.groupPowerTitle")}><MIcon name="more_vert" size={18} /></button>
              </div>
            </div>
          : <span className={styles.noAction}>—</span>}</td>
      </tr>
      {expanded && machines.map((machine) => (
        <EnvironmentMachineRow key={machine.id} machine={machine} onUpdated={onUpdated} />
      ))}
    </>
  );
}

/* rowRef／rowIndex：清單虛擬化時由 useVirtualRows 量列高用（data-index 是它找回列的依據） */
function ResourceRow({ resource, onUpdated, onDeleted, selected = false, selecting = false, onToggleSelect = null, onRowClick = null, rowRef = undefined, rowIndex = undefined }) {
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
  const menu = useDialogPresence(menuOpen, 130);
  const [consoleOpen, setConsoleOpen]     = useState(false);
  const [convertOpen, setConvertOpen]     = useState(false);
  const convertDialog = useDialogPresence(convertOpen);
  /* 課堂機器由老師統一管理，不轉範本；申請中的佔位列也沒有實體可轉 */
  const canConvertTemplate = resource.allocation_scope !== "teaching_class"
    && !resource.is_placeholder
    && resource.vmid > 0;
  const menuBtnRef = useRef(null);
  const closeMenu = () => setMenuOpen(false);

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

  const canOpenDetail = resource.vmid > 0;
  /* 整列可點進詳情；勾選、主控台與電源選單的點擊不觸發導頁。
     勾選狀態下（已勾了至少一台）點整列改成勾選，不進詳情 */
  const handleRowClick = (event) => {
    if (onRowClick?.(resource.vmid, event)) return;
    if (!canOpenDetail || event.target.closest("button, a, input, select, label")) return;
    navigate(`/resource-mgmt/${resource.vmid}`);
  };

  return (
    <>
      <tr
        ref={rowRef}
        data-index={rowIndex}
        className={`${styles.tr} ${canOpenDetail || selecting ? styles.trClickable : ""} ${selected ? styles.trSelected : ""}`}
        data-guide="resource-mgmt-row"
        onClick={handleRowClick}
      >
        {/* 勾選：整格都是點擊範圍，點偏了不會變成點進詳情頁；Shift 點選連選一段 */}
        <td className={`${styles.td} ${styles.checkCell}`} data-guide="resource-mgmt-row-select">
          {onToggleSelect && canControl ? (
            <SelectCheckbox
              cell
              checked={selected}
              onToggle={(shiftKey) => onToggleSelect(resource.vmid, shiftKey)}
              ariaLabel={t("ResourceMgmtPage.selectRowAria", { name: resource.name })}
            />
          ) : null}
        </td>
        {/* 名稱 */}
        <td className={styles.td}>
          <div className={styles.nameCell}>
            <div>
              <div className={styles.namePrimary}>{resource.name}</div>
              <div className={styles.nameSub}>
                {type.label}
                {resource.vmid > 0 && t("ResourceMgmtPage.vmidSuffix", { vmid: resource.vmid })}
              </div>
            </div>
          </div>
        </td>

        {/* 來源：管理員清單每台都標擁有者 */}
        <td className={styles.td}>
          <MachineKindBadge
            kind={resource.machine_kind}
            classRelation={resource.class_relation}
            ownerName={resource.owner_name}
            teachingClassName={resource.teaching_class_name}
            showOwner
          />
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

        {/* IP；底下列出反向代理發布的對外網址，管理員不必進詳情頁就能點 */}
        <td className={styles.td}>
          <span className={styles.mono}>{resource.ip_address ?? "N/A"}</span>
          {(resource.public_urls ?? []).length > 0 && (
            <div className={styles.publicUrlList}>
              {resource.public_urls.map((url) => (
                <a key={url} className={styles.publicUrlLink} href={url} target="_blank" rel="noreferrer" title={url}>
                  <MIcon name="open_in_new" size={13} /><span className={styles.publicUrlText}>{url.replace(/^https?:\/\//, "")}</span>
                </a>
              ))}
            </div>
          )}
        </td>

        {/* 到期日：日期照字面，小字附本地時間的實際截止時刻（後端以到期日 00:00 UTC 為界） */}
        <td className={styles.td}>
          {resource.expiry_date
            ? <ExpiryDateValue value={resource.expiry_date} />
            : <span className={styles.noExpiry}>{t("ResourceMgmtPage.noExpiry")}</span>
          }
        </td>

        {/* 節點 */}
        <td className={styles.td}>{resource.node ?? "—"}</td>

        {/* 動作 */}
        <td className={styles.td} data-guide="resource-mgmt-row-actions">
          {isLive ? (
            <div className={styles.actions}>
              <button
                type="button"
                className={styles.consoleBtn}
                title={resource.status === "starting" ? t("ResourceMgmtPage.consoleBootingTitle") : isLxc ? t("ResourceMgmtPage.terminalTitle") : t("ResourceMgmtPage.consoleTitle")}
                disabled={resource.status !== "running"}
                onClick={() => setConsoleOpen(true)}
              >
                <MIcon name={isLxc ? "terminal" : "desktop_windows"} size={14} />
                {isLxc ? t("ResourceMgmtPage.terminalTitle") : t("ResourceMgmtPage.consoleTitle")}
              </button>
              {actionLoading && <MIcon name="hourglass_empty" size={16} spin />}
              <div className={styles.menuWrap}>
                {menu.open && (
                  <PowerMenu
                    resource={resource}
                    actionLoading={actionLoading}
                    onControl={handleControl}
                    onDeleteClick={() => { closeMenu(); handleDelete(); }}
                    onConvertTemplate={canConvertTemplate ? () => { closeMenu(); setConvertOpen(true); } : undefined}
                    onClose={closeMenu}
                    anchorRef={menuBtnRef}
                    closing={menu.closing}
                  />
                )}
                <button
                  ref={menuBtnRef}
                  type="button"
                  className={`${styles.menuBtn} ${menu.open ? styles.menuBtnActive : ""}`}
                  onClick={() => setMenuOpen((value) => !value)}
                  title={t("ResourceMgmtPage.powerControlTitle")}
                  aria-label={t("ResourceMgmtPage.powerControlTitle")}
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
        <TemplateFormDialog
          sourceResource={resource}
          closing={convertDialog.closing}
          onClose={() => setConvertOpen(false)}
          onSaved={() => {
            onDeleted(resource.vmid);
            navigate("/templates");
          }}
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

/* ── Page ── */
export default function ResourceMgmtPage() {
  const { t } = useTranslation("resource");
  const navigate = useNavigate();
  const [resources, setResources] = useState([]);
  const [quickSessions, setQuickSessions] = useState([]);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState(false);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");

  const environmentGroups = buildEnvironmentGroups(resources, quickSessions);
  const grouped = groupedResourceKeys(environmentGroups);
  const visibleResources = resources.filter((resource) => (
    !grouped.vmids.has(resource.vmid)
    && !grouped.requestIds.has(String(resource.request_id))
  ));
  /* 搜尋／狀態篩過後實際列在表格上的；勾選、全選、批量都只看這些 */
  const filters = { query, status: statusFilter };
  const shownGroups = filterGroups(environmentGroups, filters);
  const shownResources = filterResources(visibleResources, filters);
  const counts = statusCounts(resources);
  const selectableVmids = shownResources
    .filter((r) => r.can_control !== false && r.vmid != null && r.vmid > 0)
    .map((r) => r.vmid);
  /* 看不到的機器（篩掉了、刷新後消失）自動取消勾選；交給批量列的是還在清單上的那些 */
  const selection = useRowSelection(selectableVmids);
  const selectedResources = shownResources.filter((r) => selection.isSelected(r.vmid));
  const clearFilters = () => {
    setQuery("");
    setStatusFilter("all");
  };

  /* 只採用最後一次發出的清單請求：清單經 PVE 要好幾秒，操作前送出的背景刷新可能比
     操作後的重新載入晚回來，舊快照不能蓋掉新清單。前景載入被背景刷新超車時，
     由最新那次接手收掉 loading／顯示錯誤，首次載入失敗不會被吞掉 */
  const fetchSeqRef = useRef(0);
  const foregroundPendingRef = useRef(false);

  /** silent = true 時不觸發 loading / error state，供背景自動刷新使用 */
  const fetchResources = useCallback(async (silent = false, signal) => {
    const seq = ++fetchSeqRef.current;
    const isLatest = () => seq === fetchSeqRef.current;
    if (!silent) {
      foregroundPendingRef.current = true;
      setLoading(true);
      setError(false);
    }
    try {
      const [data, sessions] = await Promise.all([
        ResourcesService.listAll({ signal }),
        QuickPracticeService.listAllSessions({ signal }).catch(() => []),
      ]);
      if (!isLatest()) return;
      setResources(data ?? []);
      setQuickSessions(sessions ?? []);
      /* 背景刷新成功也要能把先前的錯誤畫面收掉 */
      setError(false);
    } catch (err) {
      if (isLatest() && foregroundPendingRef.current && !err?.cancelled) setError(true);
    } finally {
      if (isLatest() && foregroundPendingRef.current && !signal?.aborted) {
        foregroundPendingRef.current = false;
        setLoading(false);
      }
    }
  }, []);

  /* 單列操作後就地更新了清單：操作前送出、還在路上的背景刷新回來時不能把它蓋回去 */
  const discardInFlightRefresh = useCallback(() => {
    if (!foregroundPendingRef.current) fetchSeqRef.current += 1;
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    fetchResources(false, controller.signal);
    return () => controller.abort();
  }, [fetchResources]);
  const anyBooting = resources.some((r) => r.status === "starting");
  useAutoRefresh(() => fetchResources(true), anyBooting ? BOOTING_POLL_INTERVAL : undefined);

  function handleUpdated(updated) {
    discardInFlightRefresh();
    setResources((prev) => prev.map((r) => r.vmid === updated.vmid ? updated : r));
  }

  /* 刪掉的那台若勾著，從清單消失時 useRowSelection 會自動取消勾選 */
  function handleDeleted(vmid) {
    discardInFlightRefresh();
    setResources((prev) => prev.filter((r) => r.vmid !== vmid));
  }

  const columns = useColumns();
  const virtualRows = useVirtualRows({ count: shownResources.length });

  return (
    <div className={styles.page}>
      {/* ── 頁首 ── */}
      <PageHeader title={t("ResourceMgmtPage.pageTitle")}>
        <div className={styles.pageActions}>
          <button type="button" className={styles.btnPrimary} onClick={() => navigate("/my-requests")}>
            <MIcon name="add" size={16} />
            {t("ResourceMgmtPage.createResource")}
          </button>
        </div>
      </PageHeader>

      {/* ── 內容 ── */}
      <div className={styles.content}>
        {error ? (
          <ErrorState onRetry={() => fetchResources()} />
        ) : loading ? (
          <LoadingState fullPage />
        ) : visibleResources.length === 0 && environmentGroups.length === 0 ? (
          <EmptyState />
        ) : (
          <>
          {/* ── 表格工具列：平常是狀態切換＋搜尋，勾選後原地換成批量操作（同一個高度，表格不會跳）；黏在頂端，捲到哪都按得到 ── */}
          <DataToolbar
            data-guide="resource-mgmt-toolbar"
            className={styles.toolbar}
            selecting={selectedResources.length > 0}
            batch={(
              <BatchActionBar
                selected={selectedResources}
                onDone={(failedVmids) => {
                  if (failedVmids) selection.replace(failedVmids);
                  fetchResources(true);
                }}
                onClear={selection.clear}
              />
            )}
          >
            <SegmentedControl
              options={[
                { value: "all", label: t("ResourceMgmtPage.filterAll"), badge: counts.all },
                { value: "running", label: t("ResourceMgmtPage.statusRunning"), badge: counts.running },
                { value: "stopped", label: t("ResourceMgmtPage.statusStopped"), badge: counts.stopped },
              ]}
              value={statusFilter}
              onChange={setStatusFilter}
              ariaLabel={t("ResourceMgmtPage.statusFilterAria")}
            />
            <ToolbarSearch
              value={query}
              onChange={setQuery}
              placeholder={t("ResourceMgmtPage.searchPlaceholder")}
            />
          </DataToolbar>
          {shownGroups.length === 0 && shownResources.length === 0 ? (
            /* 有機器但篩不到：工具列留著讓人改條件，下面給清除鈕 */
            <SharedEmptyState
              icon="search_off"
              title={t("ResourceMgmtPage.emptyFilteredTitle")}
              action={(
                <button type="button" className={styles.btnSecondary} onClick={clearFilters}>
                  <MIcon name={statusFilter === "all" ? "search_off" : "filter_alt_off"} size={16} />
                  {statusFilter === "all"
                    ? t("EmptyState.clearSearch", { ns: "common" })
                    : t("EmptyState.clearFilters", { ns: "common" })}
                </button>
              )}
            />
          ) : (
          <div className={styles.tableWrap} data-guide="resource-mgmt-table">
            <table className={styles.table}>
              <colgroup>
                <col className={styles.colCheck} />
                <col className={styles.colName} />
                <col className={styles.colKind} />
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
                    <SelectCheckbox
                      cell
                      checked={selection.allSelected}
                      indeterminate={selection.someSelected}
                      disabled={selectableVmids.length === 0}
                      onToggle={selection.toggleAll}
                      ariaLabel={t("ResourceMgmtPage.selectAllAria")}
                    />
                  </th>
                  {columns.map((col) => (
                    <th key={col} className={styles.th}>{col}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {shownGroups.map(({ group, machines }) => <EnvironmentGroupRows key={group.id} group={group} machines={machines} onUpdated={handleUpdated} onRefresh={() => fetchResources(true)} />)}
              </tbody>
              {/* 散機列另開一個 tbody 給虛擬化：幾百台時只渲染視窗附近的列，上下用空白列撐高度；
                  列數不到門檻時 items 就是全部列，行為與以前相同 */}
              <tbody ref={virtualRows.bodyRef} data-virtualized={virtualRows.enabled ? "true" : undefined}>
                {virtualRows.paddingTop > 0 && <tr aria-hidden="true" style={{ height: virtualRows.paddingTop }} />}
                {virtualRows.items.map((item) => {
                  const r = shownResources[item.index];
                  return (
                    <ResourceRow
                      key={resourceRowKey(r, item.index)}
                      rowRef={virtualRows.measureElement}
                      rowIndex={item.index}
                      resource={r}
                      onUpdated={handleUpdated}
                      onDeleted={handleDeleted}
                      selected={selection.isSelected(r.vmid)}
                      selecting={selection.selecting && r.can_control !== false && r.vmid > 0}
                      onToggleSelect={selection.toggle}
                      onRowClick={selection.rowClick}
                    />
                  );
                })}
                {virtualRows.paddingBottom > 0 && <tr aria-hidden="true" style={{ height: virtualRows.paddingBottom }} />}
              </tbody>
            </table>
          </div>
          )}
          </>
        )}
      </div>
    </div>
  );
}
