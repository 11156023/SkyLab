import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { createColumnHelper, FlexRender, useTable } from "@tanstack/react-table";
import styles from "./IpManagementPage.module.scss";
import MIcon from "../../../components/MIcon";
import DataTable from "../../../components/DataTable/DataTable";
import { baseFeatures } from "../../../components/DataTable/features";
import LoadingState from "../../../components/LoadingState/LoadingState";
import SubnetConfigForm from "./SubnetConfigForm";
import { useAuth } from "../../../contexts/AuthContext";
import SharedEmptyState from "../../../components/EmptyState/EmptyState";
import { IpManagementService } from "../../../services/ipManagement";
import {
  useIpAllocationsQuery,
  useIpManagementCacheUpdater,
  useIpStatusQuery,
  useSubnetQuery,
} from "../../../queries/ipManagement";
import { LIST_REFRESH_INTERVAL } from "../../../queries/queryClient";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { useToast } from "../../../hooks/useToast";
import { useQueryView } from "../../../hooks/useQueryLoadState";
import useDialogPresence from "../../../hooks/useDialogPresence";
import PageHeader from "../../../components/PageHeader/PageHeader";
import Pagination from "../shared/Pagination";
import { formatDateTime } from "../../../utils/formatDate";
import { isAdminUser } from "../../../utils/roles";

function EmptyState({ variant, canConfigure, onConfigure, onClearFilter }) {
  const { t } = useTranslation("system");
  if (variant === "unconfigured") {
    return (
      <SharedEmptyState
        icon="lan"
        title={t("IpManagementPage.emptyUnconfigured")}
        action={
          canConfigure ? (
            <button type="button" className={styles.btnPrimary} onClick={onConfigure} data-guide="ip-create-subnet">
              <MIcon name="add" size={18} />
              {t("IpManagementPage.createSubnetConfig")}
            </button>
          ) : null
        }
      />
    );
  }

  const isNoMatch = variant === "no-match";
  return (
    <SharedEmptyState
      icon={isNoMatch ? "search_off" : "inbox"}
      title={isNoMatch ? t("IpManagementPage.emptyNoMatch") : t("IpManagementPage.emptyNoData")}
      action={isNoMatch ? <button type="button" className={styles.btnSecondary} onClick={onClearFilter}><MIcon name="filter_alt_off" size={16} />{t("EmptyState.clearFilters", { ns: "common" })}</button> : undefined}
    />
  );
}

function PurposeBadge({ purpose }) {
  const { t } = useTranslation("system");
  const PURPOSE_LABELS = {
    vm: "VM",
    lxc: "LXC",
    gateway_vm: "Gateway VM",
    subnet_gateway: t("IpManagementPage.purposeGateway"),
    platform_host: t("IpManagementPage.purposePlatformHost"),
    reserved: t("IpManagementPage.purposeReserved"),
    class_reserved: t("IpManagementPage.purposeClassReserved"),
    quick_practice_reserved: t("IpManagementPage.purposeQuickPracticeReserved"),
  };
  const label = PURPOSE_LABELS[purpose] ?? purpose ?? "—";
  return (
    <span className={`${styles.badge} ${styles[`badge_${purpose}`] ?? styles.badge_unknown}`}>
      {label}
    </span>
  );
}

/** 表頭文字（header 是元件，可以直接用 hook） */
function ColTitle({ name }) {
  const { t } = useTranslation("system");
  return t(`IpManagementPage.${name}`);
}

const columnHelper = createColumnHelper();

/* 欄位定義放模組層級：FlexRender 把函式型的 header／cell 當元件掛載，render 裡新建會整格重新掛載 */
const allocationColumns = columnHelper.columns([
  columnHelper.accessor("ip_address", {
    header: () => <ColTitle name="colIpAddress" />,
    cell: ({ row }) => (
      <div className={styles.nameCell}>
        <div className={styles.nameIcon}>
          <MIcon name="device_hub" size={18} />
        </div>
        <div>
          <div className={styles.namePrimary}>{row.original.ip_address}</div>
        </div>
      </div>
    ),
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.accessor("purpose", {
    header: () => <ColTitle name="colPurpose" />,
    cell: ({ row }) => <PurposeBadge purpose={row.original.purpose} />,
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.accessor("vmid", {
    header: "VMID",
    cell: ({ row }) => row.original.vmid ?? "—",
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.accessor("allocated_at", {
    header: () => <ColTitle name="colAllocatedAt" />,
    cell: ({ row }) => formatDateTime(row.original.allocated_at),
    meta: { thClassName: styles.th, tdClassName: `${styles.td} ${styles.tdNowrap}` },
  }),
  columnHelper.accessor("description", {
    header: () => <ColTitle name="colDescription" />,
    cell: ({ row }) => row.original.description ?? "—",
    /* 全文放在 td 的 title，滑過可看被截斷的備註 */
    meta: { thClassName: styles.th, tdClassName: `${styles.td} ${styles.tdTruncate}`, tdTitle: (a) => a.description ?? undefined },
  }),
]);

/**
 * 備註欄的 td 要帶逐列不同的 title，共用的 DataTableCells 只吃固定的 tdProps，
 * 所以這裡自己畫儲存格（DOM 與 DataTableCells 相同，只多了 title）。
 */
function renderAllocationRow(row) {
  return (
    <tr key={row.id} className={styles.tr}>
      {row.getAllCells().map((cell) => {
        const meta = cell.column.columnDef.meta;
        return (
          <td key={cell.id} className={meta.tdClassName} title={meta.tdTitle?.(row.original)}>
            <FlexRender cell={cell} />
          </td>
        );
      })}
    </tr>
  );
}

const PAGE_SIZE = 50;

const EMPTY = Object.freeze([]);

/** 佔用網段、CIDR 不能再改的配發用途（與後端 ip_management_service 一致） */
const CIDR_LOCKING_PURPOSES = new Set(["vm", "lxc", "class_reserved", "quick_practice_reserved"]);

export default function IpManagementPage() {
  const { t } = useTranslation("system");
  const toast = useToast();
  const confirm = useConfirm();
  const { user } = useAuth();
  const isAdmin = isAdminUser(user);

  const [filter, setFilter] = useState("");
  /* 開窗時快照 config：Modal 內表單自持狀態，背景刷新不會洗掉輸入 */
  const [editing, setEditing] = useState(null); // null | { config }
  const editPresence = useDialogPresence(editing);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);

  /* 三份資料走 TanStack Query，背景 30 秒刷新、分頁隱藏時暫停；
     表單已改 Modal 且開窗時快照 config，背景刷新不會影響輸入。
     子網設定與狀態取不到時靜默當成沒有（與原本相同），配發清單失敗才提示 */
  const allocationsQuery = useIpAllocationsQuery();
  const subnetQuery = useSubnetQuery({ refetchInterval: LIST_REFRESH_INTERVAL });
  const statusQuery = useIpStatusQuery();
  const ipCache = useIpManagementCacheUpdater();
  const allocations = allocationsQuery.data?.allocations ?? EMPTY;
  const subnet = subnetQuery.data ?? null;
  const status = statusQuery.data ?? null;
  /* 第一次載入等三份都回來（或失敗）才顯示，免得子網還沒到就先閃「尚未設定」；
     之後的重抓保留舊畫面 */
  /* 沒資料時 v5 每次重抓都把 status 改回 pending：直接看 isPending 的話，首載失敗（例如非管理員打 subnet
     一律 403）後每次背景重試都會閃回整頁載入中，所以三份都看 useQueryView */
  const allocationsView = useQueryView(allocationsQuery);
  const subnetView = useQueryView(subnetQuery);
  const statusView = useQueryView(statusQuery);
  const loading = allocationsView.loading || subnetView.loading || statusView.loading;

  /* 背景刷新失敗維持靜默、沿用舊清單；只有手上沒資料時才提示。
     依賴用布林＋訊息字串，沒資料時的背景重試回同樣的錯誤不會每 30 秒再跳一次 */
  const allocationsLoadErrorMessage = allocationsView.error
    ? allocationsView.error?.message ?? t("Error.generic", { ns: "common" })
    : null;
  useEffect(() => {
    if (allocationsLoadErrorMessage) toast.error(allocationsLoadErrorMessage);
  }, [allocationsLoadErrorMessage, toast]);

  /* getSubnet 需要管理員權限，非管理員只能靠 status 判斷是否已設定 */
  const configured = Boolean(subnet) || Boolean(status?.configured);

  /* 已有 VM/LXC 佔用網段、或還有班級／快速練習的預留時後端不允許改 CIDR，先在表單擋下來
     （預留是依目前網段算好的 IP，改網段後班級建機才會失敗） */
  const cidrLocked = useMemo(
    () => allocations.some((a) => CIDR_LOCKING_PURPOSES.has(a.purpose)),
    [allocations],
  );

  const stats = useMemo(() => {
    const total = subnet?.total_ips ?? status?.total_ips ?? 0;
    const used  = subnet?.used_ips  ?? status?.used_ips  ?? allocations.length;
    const free  = subnet?.available_ips ?? status?.available_ips ?? Math.max(0, total - used);
    return { total, used, free };
  }, [allocations, subnet, status]);

  const visible = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return allocations;
    return allocations.filter(
      (a) =>
        (a.ip_address ?? "").toLowerCase().includes(q) ||
        (a.description ?? "").toLowerCase().includes(q) ||
        String(a.vmid ?? "").includes(q),
    );
  }, [allocations, filter]);

  /* 前端分頁：一個 /24 就是 254 列，不分頁會一次全部渲染 */
  const [page, setPage] = useState(0);
  useEffect(() => { setPage(0); }, [filter]);
  const totalPages = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages - 1);
  const pagedAllocations = useMemo(
    () => visible.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE),
    [visible, safePage],
  );
  /* 分頁仍由頁面切片（換頁、搜尋時回第一頁的規則照舊），表格只渲染當頁的列 */
  const table = useTable({
    features: baseFeatures,
    columns: allocationColumns,
    data: pagedAllocations,
    getRowId: (a) => a.ip_address,
  });

  const emptyVariant = !configured
    ? "unconfigured"
    : allocations.length === 0
      ? "no-data"
      : "no-match";

  async function handleSave(payload) {
    setSaving(true);
    try {
      const saved = await IpManagementService.upsertSubnet(payload);
      /* 封鎖網段是存檔後才逐台套用到防火牆；有機器套用失敗要讓管理員看到，
         否則會以為校內網段已封鎖、實際沒有 */
      const syncErrors = saved?.block_sync?.errors ?? [];
      if (syncErrors.length > 0) {
        const vmids = syncErrors
          .map((e) => e.vmid)
          .filter((v) => v != null)
          .slice(0, 10)
          .join(", ");
        toast.error(
          t("IpManagementPage.toastBlockSyncPartial", {
            count: syncErrors.length,
            vmids: vmids || "—",
          }),
        );
      } else {
        toast.success(t("IpManagementPage.toastSubnetSaved"));
      }
      setEditing(null);
      await ipCache.invalidate();
    } catch (e) {
      toast.error(e?.message ?? t("IpManagementPage.toastSaveSubnetFailed"));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    const ok = await confirm({
      title: t("IpManagementPage.deleteSubnetTitle"),
      message: t("IpManagementPage.deleteSubnetMessage"),
      confirmText: t("IpManagementPage.delete"),
      danger: true,
    });
    if (!ok) return;
    setDeleting(true);
    try {
      await IpManagementService.deleteSubnet();
      toast.success(t("IpManagementPage.toastSubnetDeleted"));
      setEditing(null);
      /* 後端已刪除：先把子網快取設成沒有，之後的重抓就算失敗也不會留著已刪的子網 */
      await ipCache.clearSubnet();
      await ipCache.invalidate();
    } catch (e) {
      toast.error(e?.message ?? t("IpManagementPage.toastDeleteSubnetFailed"));
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className={styles.page}>
      <PageHeader title={t("IpManagementPage.pageTitle")}>
        {/* 還沒設定子網路時，下方空狀態的「建立子網路設定」就是入口；頁首只在設定好後放「編輯」 */}
        {isAdmin && configured && (
          <div className={styles.pageActions}>
            <button
              type="button"
              className={styles.btnPrimary}
              onClick={() => setEditing({ config: subnet })}
              data-guide="ip-edit-subnet"
            >
              <MIcon name="edit" size={18} />
              {t("IpManagementPage.editSubnetConfig")}
            </button>
          </div>
        )}
      </PageHeader>

      <div className={styles.statRow} data-guide="ip-stats">
        <div className={styles.statCard}>
          <div className={styles.statIcon}>
            <MIcon name="public" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>{t("IpManagementPage.statTotal")}</span>
            <span className={styles.statValue}>{stats.total}</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconOk}`}>
            <MIcon name="check_circle" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>{t("IpManagementPage.statAvailable")}</span>
            <span className={styles.statValue}>{stats.free}</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconBusy}`}>
            <MIcon name="lan" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>{t("IpManagementPage.statAllocated")}</span>
            <span className={styles.statValue}>{stats.used}</span>
          </div>
        </div>
      </div>

      {editPresence.open && (
        <SubnetConfigForm
          config={editPresence.item.config}
          cidrLocked={cidrLocked}
          saving={saving}
          deleting={deleting}
          closing={editPresence.closing}
          onSubmit={handleSave}
          onCancel={() => setEditing(null)}
          onDelete={handleDelete}
        />
      )}

      <div className={styles.toolbar} data-guide="ip-toolbar">
        <div className={styles.search}>
          <MIcon name="search" size={16} />
          <input
            type="text"
            className={styles.searchInput}
            placeholder={t("IpManagementPage.searchPlaceholder")}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
        {subnet && (
          <span className={styles.muted}>
            {t("IpManagementPage.subnetLabel")} <code className={styles.code}>{subnet.cidr}</code> · Bridge: <code className={styles.code}>{subnet.bridge_name}</code>
            {subnet.vlan_tag != null && (
              <> · VLAN: <code className={styles.code}>{subnet.vlan_tag}</code></>
            )}
          </span>
        )}
      </div>

      <div className={styles.content}>
        {loading ? (
          <LoadingState fullPage text={t("IpManagementPage.loading")} />
        ) : visible.length === 0 ? (
          <EmptyState
            variant={emptyVariant}
            canConfigure={isAdmin && !editing}
            onConfigure={() => setEditing({ config: subnet })}
            onClearFilter={() => setFilter("")}
          />
        ) : (
          <div className={styles.tableWrap} data-guide="ip-table">
            <DataTable
              table={table}
              className={styles.table}
              /* 分頁與搜尋都會換掉整頁的列，欄寬固定才不會每翻一頁就跳動（樣式規範表格規則二） */
              colgroup={(
                <colgroup>
                  <col className={styles.colIp} />
                  <col className={styles.colPurpose} />
                  <col className={styles.colVmid} />
                  <col className={styles.colAllocatedAt} />
                  <col />
                </colgroup>
              )}
              renderRow={renderAllocationRow}
            />
          </div>
        )}
        {!loading && totalPages > 1 && (
          <Pagination
            page={safePage}
            totalPages={totalPages}
            info={t("IpManagementPage.paginationInfo", { count: visible.length, page: safePage + 1, totalPages })}
            prevLabel={t("IpManagementPage.prevPage")}
            nextLabel={t("IpManagementPage.nextPage")}
            onChange={setPage}
          />
        )}
      </div>
    </div>
  );
}
