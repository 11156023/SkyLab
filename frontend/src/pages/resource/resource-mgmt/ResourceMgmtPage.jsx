import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { createColumnHelper, useTable } from "@tanstack/react-table";
import styles from "./ResourceMgmtPage.module.scss";
import MIcon from "../../../components/MIcon";
import { DataTableCells, DataTableHead } from "../../../components/DataTable/DataTable";
import { baseFeatures } from "../../../components/DataTable/features";
import MachineKindBadge from "../../../components/MachineKindBadge/MachineKindBadge";
import PowerMenu from "../../../components/PowerMenu/PowerMenu";
import TemplateFormDialog from "../templates/TemplateFormDialog";
import useDialogPresence from "../../../hooks/useDialogPresence";
import useVirtualRows from "../../../hooks/useVirtualRows";
import { useQueryView } from "../../../hooks/useQueryLoadState";
import SharedEmptyState from "../../../components/EmptyState/EmptyState";
import ErrorState from "../../../components/ErrorState/ErrorState";
import { useToast } from "../../../hooks/useToast";
import LoadingState from "../../../components/LoadingState/LoadingState";
import { ResourcesService } from "../../../services/resources";
import { resourceKeys, useAllResourcesQuery, useResourceCacheUpdater } from "../../../queries/resources";
import TerminalDialog from "../../personal/resources/TerminalDialog";
import VncDialog from "../../personal/resources/VncDialog";
import { LIVE_STATUSES, machineSpecLabel, resourceRowKey, statusAfterAction } from "../../personal/resources/resourceRows";
import PageHeader from "../../../components/PageHeader/PageHeader";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
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

/* ── 散機列的欄位（TanStack Table）──
   v9 的 header／cell 會被當元件掛載，欄位定義放模組層級才不會每次 render 整格重新掛載。
   列本身的狀態（選單、操作中、主控台…）在 ResourceRow，經 rowState 交給各欄；
   環境群組列不屬於這份資料，仍由 EnvironmentGroupRows 自己畫 */
const columnHelper = createColumnHelper();

/* 欄名來自 useColumns()；index 是去掉勾選欄後的順序 */
function ColumnTitle({ index }) {
  return useColumns()[index];
}

function SelectAllHeader({ checked, indeterminate, disabled, onToggle }) {
  const { t } = useTranslation("resource");
  return (
    <SelectCheckbox
      cell
      checked={checked}
      indeterminate={indeterminate}
      disabled={disabled}
      onToggle={onToggle}
      ariaLabel={t("ResourceMgmtPage.selectAllAria")}
    />
  );
}

function RowSelectCell({ resource, selected, canSelect, onToggleSelect }) {
  const { t } = useTranslation("resource");
  if (!canSelect) return null;
  return (
    <SelectCheckbox
      cell
      checked={selected}
      onToggle={(shiftKey) => onToggleSelect(resource.vmid, shiftKey)}
      ariaLabel={t("ResourceMgmtPage.selectRowAria", { name: resource.name })}
    />
  );
}

function ResourceNameCell({ resource }) {
  const { t } = useTranslation("resource");
  const typeMap = useTypeMap();
  const type = typeMap[resource.type] ?? { label: resource.type, icon: "computer" };
  return (
    <div className={styles.nameCell}>
      <div>
        <div className={styles.namePrimary}>{resource.name}</div>
        <div className={styles.nameSub}>
          {type.label}
          {resource.vmid > 0 && t("ResourceMgmtPage.vmidSuffix", { vmid: resource.vmid })}
        </div>
      </div>
    </div>
  );
}

function ResourceExpiryCell({ value }) {
  const { t } = useTranslation("resource");
  return value
    ? <ExpiryDateValue value={value} />
    : <span className={styles.noExpiry}>{t("ResourceMgmtPage.noExpiry")}</span>;
}

function ResourceActionsCell({
  resource,
  isLive,
  isLxc,
  actionLoading,
  menuOpen,
  menuClosing,
  menuBtnRef,
  onToggleMenu,
  onCloseMenu,
  onControl,
  onDelete,
  onConvertTemplate,
  onOpenConsole,
}) {
  const { t } = useTranslation("resource");
  const statusMap = useStatusMap();
  if (!isLive) {
    return (
      <span className={styles.noAction}>
        {statusMap[resource.status]?.label ?? "—"}
      </span>
    );
  }
  return (
    <div className={styles.actions}>
      <button
        type="button"
        className={styles.consoleBtn}
        title={resource.status === "starting" ? t("ResourceMgmtPage.consoleBootingTitle") : isLxc ? t("ResourceMgmtPage.terminalTitle") : t("ResourceMgmtPage.consoleTitle")}
        disabled={resource.status !== "running"}
        onClick={onOpenConsole}
      >
        <MIcon name={isLxc ? "terminal" : "desktop_windows"} size={14} />
        {isLxc ? t("ResourceMgmtPage.terminalTitle") : t("ResourceMgmtPage.consoleTitle")}
      </button>
      {actionLoading && <MIcon name="hourglass_empty" size={16} spin />}
      <div className={styles.menuWrap}>
        {menuOpen && (
          <PowerMenu
            resource={resource}
            actionLoading={actionLoading}
            onControl={onControl}
            onDeleteClick={onDelete}
            onConvertTemplate={onConvertTemplate}
            onClose={onCloseMenu}
            anchorRef={menuBtnRef}
            closing={menuClosing}
          />
        )}
        <button
          ref={menuBtnRef}
          type="button"
          className={`${styles.menuBtn} ${menuOpen ? styles.menuBtnActive : ""}`}
          onClick={onToggleMenu}
          title={t("ResourceMgmtPage.powerControlTitle")}
          aria-label={t("ResourceMgmtPage.powerControlTitle")}
        >
          <MIcon name="more_vert" size={18} />
        </button>
      </div>
    </div>
  );
}

const resourceColumns = columnHelper.columns([
  /* 勾選：整格都是點擊範圍，點偏了不會變成點進詳情頁；Shift 點選連選一段。全選的狀態經 table meta 傳入 */
  columnHelper.display({
    id: "select",
    header: ({ table }) => <SelectAllHeader {...table.options.meta.selectAll} />,
    cell: ({ row, rowState }) => (
      <RowSelectCell
        resource={row.original}
        selected={rowState.selected}
        canSelect={rowState.canSelect}
        onToggleSelect={rowState.onToggleSelect}
      />
    ),
    meta: {
      thClassName: `${styles.th} ${styles.checkCell}`,
      tdClassName: `${styles.td} ${styles.checkCell}`,
      tdProps: { "data-guide": "resource-mgmt-row-select" },
    },
  }),
  /* 名稱 */
  columnHelper.accessor("name", {
    header: () => <ColumnTitle index={0} />,
    cell: ({ row }) => <ResourceNameCell resource={row.original} />,
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
  /* 來源：管理員清單每台都標擁有者 */
  columnHelper.display({
    id: "kind",
    header: () => <ColumnTitle index={1} />,
    cell: ({ row }) => (
      <MachineKindBadge
        kind={row.original.machine_kind}
        classRelation={row.original.class_relation}
        ownerName={row.original.owner_name}
        teachingClassName={row.original.teaching_class_name}
        showOwner
      />
    ),
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
  /* 環境 / 系統 */
  columnHelper.display({
    id: "env",
    header: () => <ColumnTitle index={2} />,
    cell: ({ row }) => (
      <>
        <div className={styles.envPrimary}>{row.original.environment_type ?? "—"}</div>
        {row.original.os_info && <div className={styles.envSub}>{row.original.os_info}</div>}
      </>
    ),
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
  /* 狀態 */
  columnHelper.accessor("status", {
    header: () => <ColumnTitle index={3} />,
    cell: ({ row }) => <StatusBadge status={row.original.status} />,
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
  /* IP；底下列出反向代理發布的對外網址，管理員不必進詳情頁就能點 */
  columnHelper.display({
    id: "ip",
    header: () => <ColumnTitle index={4} />,
    cell: ({ row }) => (
      <>
        <span className={styles.mono}>{row.original.ip_address ?? "N/A"}</span>
        {(row.original.public_urls ?? []).length > 0 && (
          <div className={styles.publicUrlList}>
            {row.original.public_urls.map((url) => (
              <a key={url} className={styles.publicUrlLink} href={url} target="_blank" rel="noreferrer" title={url}>
                <MIcon name="open_in_new" size={13} /><span className={styles.publicUrlText}>{url.replace(/^https?:\/\//, "")}</span>
              </a>
            ))}
          </div>
        )}
      </>
    ),
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
  /* 到期日：日期照字面，小字附本地時間的實際截止時刻（後端以到期日 00:00 UTC 為界） */
  columnHelper.display({
    id: "expiry",
    header: () => <ColumnTitle index={5} />,
    cell: ({ row }) => <ResourceExpiryCell value={row.original.expiry_date} />,
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
  /* 節點 */
  columnHelper.display({
    id: "node",
    header: () => <ColumnTitle index={6} />,
    cell: ({ row }) => row.original.node ?? "—",
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
  /* 動作：電源選單與主控台的狀態在 ResourceRow */
  columnHelper.display({
    id: "actions",
    header: () => <ColumnTitle index={7} />,
    cell: ({ row, rowState }) => <ResourceActionsCell resource={row.original} {...rowState} />,
    meta: {
      thClassName: styles.th,
      tdClassName: styles.td,
      tdProps: { "data-guide": "resource-mgmt-row-actions" },
    },
  }),
]);

/* rowRef／rowIndex：清單虛擬化時由 useVirtualRows 量列高用（data-index 是它找回列的依據）；
   row 是 TanStack 的列，resource 是它的原始資料（一併傳進來，編譯後的元件才會跟著資料重繪） */
function ResourceRow({ row, resource, onUpdated, onDeleted, selected = false, selecting = false, onToggleSelect = null, onRowClick = null, rowRef = undefined, rowIndex = undefined }) {
  const { t } = useTranslation("resource");
  const toast = useToast();
  const navigate = useNavigate();
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
        <DataTableCells
          row={row}
          rowState={{
            selected,
            canSelect: Boolean(onToggleSelect) && canControl,
            onToggleSelect,
            isLive,
            isLxc,
            actionLoading,
            menuOpen: menu.open,
            menuClosing: menu.closing,
            menuBtnRef,
            onToggleMenu: () => setMenuOpen((value) => !value),
            onCloseMenu: closeMenu,
            onControl: handleControl,
            onDelete: () => { closeMenu(); handleDelete(); },
            onConvertTemplate: canConvertTemplate ? () => { closeMenu(); setConvertOpen(true); } : undefined,
            onOpenConsole: () => setConsoleOpen(true),
          }}
        />
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
const EMPTY = Object.freeze([]);

export default function ResourceMgmtPage() {
  const { t } = useTranslation("resource");
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");

  /* 清單走 TanStack Query：背景 30 秒刷新（有機器開機中時 5 秒）、分頁隱藏時暫停、
     換頁回來先顯示快取再背景更新。同一份快取同時只會有一個請求在路上，
     不再需要自己記請求序號；單列操作就地改快取前會先取消在途的背景刷新，
     操作前送出的舊快照不會把剛改好的那一列蓋回去 */
  const resourcesQuery = useAllResourcesQuery();
  const resourceCache = useResourceCacheUpdater(resourceKeys.everyone);
  const resources = resourcesQuery.data?.resources ?? EMPTY;
  const quickSessions = resourcesQuery.data?.sessions ?? EMPTY;
  /* 只有「還沒有任何資料」時才顯示 skeleton／錯誤畫面；背景刷新失敗沿用上一筆資料。
     錯誤畫面在背景重試期間不閃回 skeleton，按重試才回到 skeleton，背景刷新成功也會自動離開錯誤畫面 */
  const { loading, error, retry: retryResources } = useQueryView(resourcesQuery);

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

  function handleUpdated(updated) {
    resourceCache.update(updated);
  }

  /* 刪掉的那台若勾著，從清單消失時 useRowSelection 會自動取消勾選 */
  function handleDeleted(vmid) {
    resourceCache.remove(vmid);
  }

  /** 群組操作、批量操作做完：整份清單標記過期，背景重抓（畫面沿用現有資料不閃） */
  function refreshResources() {
    resourceCache.invalidate();
  }

  /* 散機列交給 TanStack Table；表頭的全選框經 meta 拿勾選狀態 */
  const table = useTable({
    features: baseFeatures,
    columns: resourceColumns,
    data: shownResources,
    getRowId: resourceRowKey,
    meta: {
      selectAll: {
        checked: selection.allSelected,
        indeterminate: selection.someSelected,
        disabled: selectableVmids.length === 0,
        onToggle: selection.toggleAll,
      },
    },
  });
  const rows = table.getRowModel().rows;
  /* 解構成各自的變數：React Compiler 會把「有欄位被傳給 ref」的物件整個當成 ref，讀其他欄位就整頁略過 */
  const {
    attachBody: attachVirtualBody,
    measureElement: measureVirtualRow,
    enabled: virtualized,
    items: virtualItems,
    paddingTop: virtualPadTop,
    paddingBottom: virtualPadBottom,
  } = useVirtualRows({ count: shownResources.length });

  return (
    <div className={styles.page}>
      {/* ── 頁首 ── */}
      <PageHeader title={t("ResourceMgmtPage.pageTitle")}>
        <div className={styles.pageActions}>
          {/* 跟其他「申請／建立」入口一樣直接打開申請表單，不是停在申請清單 */}
          <button type="button" className={styles.btnPrimary} onClick={() => navigate("/my-requests", { state: { create: true } })}>
            <MIcon name="add" size={16} />
            {t("ResourceMgmtPage.createResource")}
          </button>
        </div>
      </PageHeader>

      {/* ── 內容 ── */}
      <div className={styles.content}>
        {error ? (
          <ErrorState onRetry={retryResources} />
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
                  refreshResources();
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
              <DataTableHead table={table} />
              {/* 兩個 tbody 自己組：環境群組列不屬於散機那份資料，放第一個；
                  散機列的 tbody 要掛虛擬化的 ref，它量的是這個 tbody 距頁頂的位置，不能跟群組列混在一起 */}
              <tbody>
                {shownGroups.map(({ group, machines }) => <EnvironmentGroupRows key={group.id} group={group} machines={machines} onUpdated={handleUpdated} onRefresh={refreshResources} />)}
              </tbody>
              {/* 散機列另開一個 tbody 給虛擬化：幾百台時只渲染視窗附近的列，上下用空白列撐高度；
                  列數不到門檻時 items 就是全部列，行為與以前相同 */}
              <tbody ref={attachVirtualBody} data-virtualized={virtualized ? "true" : undefined}>
                {virtualPadTop > 0 && <tr aria-hidden="true" style={{ height: virtualPadTop }} />}
                {virtualItems.map((item) => {
                  const row = rows[item.index];
                  const r = row.original;
                  return (
                    <ResourceRow
                      key={row.id}
                      row={row}
                      rowRef={measureVirtualRow}
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
                {virtualPadBottom > 0 && <tr aria-hidden="true" style={{ height: virtualPadBottom }} />}
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
