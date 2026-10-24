import { Fragment, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { createColumnHelper, useTable } from "@tanstack/react-table";
import styles from "./MonitoringPage.module.scss";
import MIcon from "../../../components/MIcon";
import DataTable, { DataTableCells } from "../../../components/DataTable/DataTable";
import { baseFeatures } from "../../../components/DataTable/features";
import LoadingState from "../../../components/LoadingState/LoadingState";
import EmptyState from "../../../components/EmptyState/EmptyState";
import RrdChart from "../../../components/RrdChart/RrdChart";
import MiningIncidentsPanel from "./MiningIncidentsPanel";
import SystemHealthCard from "./SystemHealthCard";
import usePersistedToggle from "./usePersistedToggle";
import { MonitoringService } from "../../../services/monitoring";
import { useAlertsQuery, useMonitoringCacheUpdater, useNodeRrdQuery } from "../../../queries/monitoring";
import { useToast } from "../../../hooks/useToast";
import { useLoadError } from "../../../hooks/useQueryLoadState";
import usePveOverview from "../../../hooks/usePveOverview";
import PageHeader from "../../../components/PageHeader/PageHeader";
import { formatDateTime, formatTime } from "../../../utils/formatDate";

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const tb = bytes / 1024 ** 4;
  if (tb >= 1) return `${tb.toFixed(2)} TB`;
  const gb = bytes / 1024 ** 3;
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  const mb = bytes / 1024 ** 2;
  return `${mb.toFixed(0)} MB`;
}

function formatUptime(seconds, t) {
  if (!seconds) return "—";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  if (days > 0) return t("MonitoringPage.uptimeDaysHours", { days, hours });
  const minutes = Math.floor((seconds % 3600) / 60);
  return t("MonitoringPage.uptimeHoursMinutes", { hours, minutes });
}

/** 將 PVE 節點 RRD 原始點位轉為圖表資料（CPU%、記憶體%） */
function mapNodeRrd(points) {
  return (points ?? [])
    .filter((p) => typeof p.time === "number")
    .map((p) => ({
      time: formatTime(p.time * 1000),
      cpu: typeof p.cpu === "number" ? Number((p.cpu * 100).toFixed(2)) : null,
      memory:
        typeof p.memused === "number" && typeof p.memtotal === "number" && p.memtotal > 0
          ? Number(((p.memused / p.memtotal) * 100).toFixed(2))
          : null,
    }));
}

/* 節點用量整卡收合的偏好記在本機，重整後維持使用者的選擇 */
const NODES_OPEN_STORAGE_KEY = "skylab.monitoringNodesOpen";

/* 節點趨勢每 60 秒刷新一次（分頁隱藏時暫停）；固定看最近 1 天 */
const NODE_TRENDS_REFRESH_INTERVAL = 60_000;
const NODE_TRENDS_TIMEFRAME = "day";
/* 未解除的告警（分頁角標與清單共用） */
const ACTIVE_ALERTS_PARAMS = Object.freeze({ active: true });
const EMPTY = Object.freeze([]);

function UsageBar({ pct }) {
  return (
    <div className={styles.usageBar}>
      <div
        className={`${styles.usageFill} ${pct >= 90 ? styles.usageFill_danger : ""}`}
        style={{ width: `${Math.min(pct, 100)}%` }}
      />
    </div>
  );
}

function OverviewCard({ title, pct, detail }) {
  return (
    <div className={styles.overviewCard}>
      <div className={styles.overviewTop}>
        <div className={styles.overviewInfo}>
          <span className={styles.overviewLabel}>{title}</span>
          <span className={styles.overviewDetail}>{detail}</span>
        </div>
        <span className={styles.overviewValue}>
          {pct.toFixed(1)}
          <span className={styles.overviewUnit}>%</span>
        </span>
      </div>
      <UsageBar pct={pct} />
    </div>
  );
}

/** 節點展開後的趨勢圖（每 60 秒輪詢）；固定顯示最近 1 天，時間範圍切換器已移除（2026-09） */
function NodeTrends({ node }) {
  const { t } = useTranslation("system");
  const RRD_SERIES = [
    { key: "cpu",    label: "CPU %",    color: "--color-info" },
    { key: "memory", label: t("MonitoringPage.memoryPercentLabel"), color: "--color-success" },
  ];
  const rrdQuery = useNodeRrdQuery(node, NODE_TRENDS_TIMEFRAME, { refetchInterval: NODE_TRENDS_REFRESH_INTERVAL });
  /* 首次就抓不到時當作沒有趨勢資料（同原本）；背景刷新失敗沿用上一份圖表 */
  /* 首載失敗看 useLoadError（沒資料時 v5 每次重抓都把 status 改回 pending、清掉 error，看 isError 會在背景重試時閃回載入中） */
  const rrdLoadError = useLoadError(rrdQuery).error;
  const data = rrdQuery.data !== undefined
    ? mapNodeRrd(rrdQuery.data)
    : rrdLoadError ? EMPTY : null;

  if (data === null) {
    return <LoadingState text={t("MonitoringPage.loadingTrends")} />;
  }

  /* RRD 可能回傳只有時間戳、指標全 null 的點位（節點剛離線／剛加入）：
     畫出來是兩張只有座標軸的空圖，不如直接說沒資料 */
  const hasValues = data.some((point) => point.cpu != null || point.memory != null);
  if (data.length === 0 || !hasValues) {
    return <EmptyState icon="show_chart" title={t("MonitoringPage.noTrendData")} />;
  }

  return (
    <div className={styles.trendGrid}>
      {RRD_SERIES.map((s) => (
        <RrdChart
          key={s.key}
          title={s.label}
          data={data}
          series={[s]}
          unit="%"
          height={200}
        />
      ))}
    </div>
  );
}

export function AlertsCard({ onCountChange }) {
  const { t } = useTranslation("system");
  const toast = useToast();
  const METRIC_LABELS = { cpu: "CPU", memory: t("MonitoringPage.memoryLabel"), disk: t("MonitoringPage.diskLabel") };
  const SCOPE_LABELS  = {
    cluster: t("MonitoringPage.scopeCluster"),
    node: t("MonitoringPage.scopeNode"),
    vm: "VM",
    system: t("MonitoringPage.scopeSystem"),
  };
  const [ackBusy, setAckBusy] = useState(null);
  /* 30 秒背景刷新、分頁隱藏時暫停。首次就抓不到時當作沒有告警（同原本，不擋住面板）；
     背景刷新失敗沿用上一份清單 */
  const alertsQuery = useAlertsQuery(ACTIVE_ALERTS_PARAMS);
  const monitoringCache = useMonitoringCacheUpdater();
  /* 首載失敗看 useLoadError（沒資料時 v5 每次重抓都把 status 改回 pending、清掉 error，看 isError 會在背景重試時閃回載入中） */
  const alertsLoadError = useLoadError(alertsQuery).error;
  const alerts = alertsQuery.data !== undefined
    ? alertsQuery.data ?? EMPTY
    : alertsLoadError ? EMPTY : null;

  /* 分頁角標要顯示筆數，載入後回報給頁面 */
  useEffect(() => {
    if (alerts !== null) onCountChange?.(alerts.length);
  }, [alerts, onCountChange]);

  const handleAck = async (alertId) => {
    setAckBusy(alertId);
    try {
      await MonitoringService.ackAlert(alertId);
      /* 重抓完才解除按鈕的忙碌狀態（同原本 await load()）；告警清單的各種參數（含管理首頁）一起過期 */
      await monitoringCache.invalidateAlerts();
    } catch (e) {
      toast.error(e?.message ?? t("MonitoringPage.toastAckFailed"));
    } finally {
      setAckBusy(null);
    }
  };

  return (
    <div className={styles.card}>
      {/* 標題由外層頁籤承擔，卡內只留說明 */}
      <div className={styles.cardHeader}>
        <p className={styles.cardDesc}>{t("MonitoringPage.activeAlertsDesc")}</p>
        {alerts && alerts.length > 0 && (
          <span className={styles.alertCount}>{alerts.length}</span>
        )}
      </div>

      {alerts === null ? (
        <LoadingState />
      ) : alerts.length === 0 ? (
        <EmptyState icon="notifications_off" title={t("MonitoringPage.emptyNoAlerts")} />
      ) : (
        <div className={styles.alertList}>
          {alerts.map((alert) => (
            <div key={alert.id} className={styles.alertRow}>
              <div className={styles.alertMain}>
                <MIcon name="warning" size={16} />
                <div>
                  <div className={styles.alertHead}>
                    <span className={styles.alertScope}>
                      {SCOPE_LABELS[alert.scope] ?? alert.scope}
                    </span>
                    {/* 多叢集下 vmid 會重複：標出告警屬於哪個 PVE 連線 */}
                    {alert.connection_name && (
                      <span className={styles.alertCluster}>
                        {t("MonitoringPage.alertCluster", { name: alert.connection_name })}
                      </span>
                    )}
                    <span className={styles.alertTarget}>{alert.target}</span>
                    {/* 平台健康告警的 value 不是百分比（連續失敗次數等），直接顯示後端訊息 */}
                    {alert.scope === "system" ? (
                      <span className={styles.alertMetric}>{alert.message}</span>
                    ) : (
                      <>
                        <span className={styles.alertMetric}>
                          {METRIC_LABELS[alert.metric] ?? alert.metric} {alert.value.toFixed(0)}%
                        </span>
                        <span className={styles.alertThreshold}>
                          {t("MonitoringPage.thresholdSuffix", { threshold: alert.threshold.toFixed(0) })}
                        </span>
                      </>
                    )}
                  </div>
                  <p className={styles.alertTime}>
                    {formatDateTime(alert.created_at)}
                    {alert.acknowledged_at && ` · ${t("MonitoringPage.acknowledged")}`}
                  </p>
                </div>
              </div>
              {!alert.acknowledged_at && (
                <button
                  type="button"
                  className={styles.btnSecondary}
                  disabled={ackBusy === alert.id}
                  onClick={() => handleAck(alert.id)}
                >
                  <MIcon name="check" size={14} />
                  {t("MonitoringPage.acknowledge")}
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** 表頭文字（header 是元件，可以直接用 hook） */
function ColTitle({ name }) {
  const { t } = useTranslation("system");
  return t(`MonitoringPage.${name}`);
}

const columnHelper = createColumnHelper();

/* 欄位定義放模組層級：FlexRender 把函式型的 header／cell 當元件掛載，render 裡新建會整格重新掛載。
   CPU 與記憶體兩張排行只差最後一欄，各建一份固定的欄位 */
function topVmColumns(metric) {
  return columnHelper.columns([
    columnHelper.accessor("vmid", {
      header: "VMID",
      cell: ({ row }) => row.original.vmid,
      meta: { thClassName: styles.th, tdClassName: `${styles.td} ${styles.monoCell}` },
    }),
    columnHelper.accessor("name", {
      header: () => <ColTitle name="colName" />,
      cell: ({ row }) => row.original.name,
      meta: { thClassName: styles.th, tdClassName: styles.td },
    }),
    columnHelper.accessor("node", {
      header: () => <ColTitle name="colNode" />,
      cell: ({ row }) => row.original.node,
      meta: { thClassName: styles.th, tdClassName: `${styles.td} ${styles.mutedCell}` },
    }),
    columnHelper.accessor("type", {
      header: () => <ColTitle name="colType" />,
      cell: ({ row }) => (
        <span className={styles.typeBadge}>
          {row.original.type === "qemu" ? "VM" : "LXC"}
        </span>
      ),
      meta: { thClassName: styles.th, tdClassName: styles.td },
    }),
    columnHelper.display({
      id: metric,
      header: metric === "cpu" ? "CPU" : () => <ColTitle name="memoryLabel" />,
      cell: ({ row }) => (metric === "cpu" ? `${(row.original.cpu * 100).toFixed(1)}%` : formatBytes(row.original.mem)),
      meta: { thClassName: styles.th, tdClassName: `${styles.td} ${styles.numericCell}` },
    }),
  ]);
}

const TOP_VM_COLUMNS = Object.freeze({ cpu: topVmColumns("cpu"), mem: topVmColumns("mem") });

function TopVmTable({ title, entries, metric }) {
  const { t } = useTranslation("system");
  const table = useTable({
    features: baseFeatures,
    columns: TOP_VM_COLUMNS[metric],
    data: entries,
    getRowId: (vm) => String(vm.vmid),
  });
  return (
    <div className={styles.card}>
      <div className={styles.cardHeader}>
        <h2 className={styles.cardTitle}>{title}</h2>
      </div>
      {entries.length === 0 ? (
        <EmptyState icon="dns" title={t("MonitoringPage.emptyNoRunningResources")} />
      ) : (
        <div className={styles.tableScroll}>
        <DataTable table={table} className={styles.topTable} rowClassName={() => styles.tr} />
        </div>
      )}
    </div>
  );
}

function NodeNameCell({ node, online, expanded }) {
  return (
    <span className={styles.nodeCell}>
      {/* 離線列沒有展開箭頭，補同寬佔位讓圖示與名稱跟其他列對齊 */}
      {online
        ? <MIcon name={expanded ? "expand_more" : "chevron_right"} size={16} />
        : <span className={styles.chevronSpacer} />}
      <MIcon name="dns" size={16} />
      <strong>{node.node}</strong>
      {node.connection_name && (
        <span className={styles.typeBadge}>{node.connection_name}</span>
      )}
    </span>
  );
}

function NodeStatusCell({ status }) {
  const { t } = useTranslation("system");
  const online = status === "online";
  return (
    <span
      className={`${styles.badge} ${online ? styles.badge_success : styles.badge_danger}`}
    >
      {online ? t("MonitoringPage.online") : status}
    </span>
  );
}

function NodeCpuCell({ cpu, maxcpu }) {
  const { t } = useTranslation("system");
  const nodeCpu = maxcpu > 0 ? cpu * 100 : 0;
  return (
    <div className={styles.usageCell}>
      <div className={styles.usageMeta}>
        <span>{nodeCpu.toFixed(1)}%</span>
        <span className={styles.mutedText}>{t("MonitoringPage.coresLabel", { count: maxcpu })}</span>
      </div>
      <UsageBar pct={nodeCpu} />
    </div>
  );
}

function NodeBytesCell({ used, max }) {
  const pct = max > 0 ? (used / max) * 100 : 0;
  return (
    <div className={styles.usageCell}>
      <div className={styles.usageMeta}>
        <span>{pct.toFixed(1)}%</span>
        <span className={styles.mutedText}>
          {formatBytes(used)} / {formatBytes(max)}
        </span>
      </div>
      <UsageBar pct={pct} />
    </div>
  );
}

function NodeUptimeCell({ uptime }) {
  const { t } = useTranslation("system");
  return formatUptime(uptime, t);
}

/* 節點列的展開狀態由頁面管（同時只展開一台），經 rowState 傳進儲存格 */
const nodeColumns = columnHelper.columns([
  columnHelper.accessor("node", {
    header: () => <ColTitle name="colNode" />,
    cell: ({ row, rowState }) => (
      <NodeNameCell node={row.original} online={rowState.online} expanded={rowState.expanded} />
    ),
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.accessor("status", {
    header: () => <ColTitle name="colStatus" />,
    cell: ({ row }) => <NodeStatusCell status={row.original.status} />,
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.display({
    id: "cpu",
    header: "CPU",
    cell: ({ row }) => <NodeCpuCell cpu={row.original.cpu} maxcpu={row.original.maxcpu} />,
    meta: { thClassName: `${styles.th} ${styles.thWide}`, tdClassName: styles.td },
  }),
  columnHelper.display({
    id: "memory",
    header: () => <ColTitle name="memoryLabel" />,
    cell: ({ row }) => <NodeBytesCell used={row.original.mem} max={row.original.maxmem} />,
    meta: { thClassName: `${styles.th} ${styles.thWide}`, tdClassName: styles.td },
  }),
  columnHelper.display({
    id: "disk",
    header: () => <ColTitle name="diskLabel" />,
    cell: ({ row }) => <NodeBytesCell used={row.original.disk} max={row.original.maxdisk} />,
    meta: { thClassName: `${styles.th} ${styles.thWide}`, tdClassName: styles.td },
  }),
  columnHelper.accessor("vm_count", {
    header: "VM / LXC",
    cell: ({ row }) => row.original.vm_count,
    meta: { thClassName: styles.th, tdClassName: `${styles.td} ${styles.numericCell}` },
  }),
  columnHelper.accessor("uptime", {
    header: () => <ColTitle name="colUptime" />,
    cell: ({ row }) => <NodeUptimeCell uptime={row.original.uptime} />,
    meta: { thClassName: styles.th, tdClassName: `${styles.td} ${styles.mutedCell}` },
  }),
]);

export default function MonitoringPage() {
  const { t } = useTranslation("system");
  const [expandedNode, setExpandedNode] = useState(null);
  /* PVE 概況與首頁共用 usePveOverview（30 秒輪詢、分頁隱藏時暫停） */
  const { overview, loading, error } = usePveOverview();
  /* 警告與挖礦事件收進分頁（#24）；筆數由面板載入後回報 */
  const [panelTab, setPanelTab] = useState("alerts");
  const [alertCount, setAlertCount] = useState(null);
  const [miningCount, setMiningCount] = useState(null);
  /* 節點用量整卡收合：預設收起省版面，標題列保留在線摘要 */
  const [nodesOpen, toggleNodesOpen] = usePersistedToggle(NODES_OPEN_STORAGE_KEY);
  /* 監控 stack 有啟用（後端連得到 Grafana）才顯示連結；查詢失敗就當沒啟用。
     同一支 API 會設定 Grafana 免密碼登入的 cookie（效期數小時），頁面開著時定期續期 */
  const [grafanaUrl, setGrafanaUrl] = useState(null);

  useEffect(() => {
    const controller = new AbortController();
    const refresh = () =>
      MonitoringService.createGrafanaSession({ signal: controller.signal })
        .then((link) => setGrafanaUrl(link?.enabled ? link.url : null))
        .catch(() => {});
    refresh();
    const timer = setInterval(refresh, 30 * 60_000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, []);

  /* 系統健康卡與警告面板不依賴 PVE：概況還在載入或 PVE 連不上時照樣顯示——
     PVE 掛掉的當下，正是最需要看平台健康與系統告警的時候 */
  const ready = !loading && !error && overview != null;
  const cpuPct = ready && overview.cpu_total > 0 ? (overview.cpu_used / overview.cpu_total) * 100 : 0;
  const memPct = ready && overview.mem_total > 0 ? (overview.mem_used / overview.mem_total) * 100 : 0;
  const diskPct =
    ready && overview.disk_total > 0 ? (overview.disk_used / overview.disk_total) * 100 : 0;

  const nodeTable = useTable({
    features: baseFeatures,
    columns: nodeColumns,
    data: overview?.nodes ?? EMPTY,
    getRowId: (node) => node.node,
  });

  return (
    <div className={styles.page}>
      <PageHeader title={t("MonitoringPage.pageTitle")}>
        {grafanaUrl && (
          <a
            className={styles.linkBtn}
            data-guide="monitoring-grafana"
            href={grafanaUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            <MIcon name="open_in_new" size={16} />
            {t("MonitoringPage.openGrafana")}
          </a>
        )}
      </PageHeader>

      <SystemHealthCard />

      {loading ? (
        <LoadingState text={t("MonitoringPage.loadingOverview")} />
      ) : !ready ? (
        <div className={`${styles.card} ${styles.cardEmpty}`}>
          <MIcon name="warning" size={24} />
          <p>{t("MonitoringPage.errorFetchOverview")}</p>
        </div>
      ) : (
      /* 叢集用量卡片 */
      <div className={styles.statRow} data-guide="monitoring-overview">
        <OverviewCard
          title={t("MonitoringPage.cpuUsage")}
          pct={cpuPct}
          detail={t("MonitoringPage.coresDetail", { used: overview.cpu_used.toFixed(1), total: overview.cpu_total })}
        />
        <OverviewCard
          title={t("MonitoringPage.memoryUsage")}
          pct={memPct}
          detail={`${formatBytes(overview.mem_used)} / ${formatBytes(overview.mem_total)}`}
        />
        <OverviewCard
          title={t("MonitoringPage.diskUsage")}
          pct={diskPct}
          detail={`${formatBytes(overview.disk_used)} / ${formatBytes(overview.disk_total)}`}
        />
        {/* 運行狀態：三個數字排三欄，不再擠成三行小字（#24） */}
        <div className={styles.overviewCard}>
          <div className={styles.statusGrid}>
            <div>
              <span>{t("MonitoringPage.nodesOnline")}</span>
              <strong>
                {overview.nodes_online}
                <em>/{overview.nodes_total}</em>
              </strong>
            </div>
            <div>
              <span>{t("MonitoringPage.vmRunning")}</span>
              <strong>
                {overview.vms_running}
                <em>/{overview.vms_running + overview.vms_stopped}</em>
              </strong>
            </div>
            <div>
              <span>{t("MonitoringPage.lxcRunning")}</span>
              <strong>
                {overview.lxc_running}
                <em>/{overview.lxc_running + overview.lxc_stopped}</em>
              </strong>
            </div>
          </div>
        </div>
      </div>
      )}

      {/* 警告與挖礦事件收進分頁（#24）；兩個面板保持掛載，輪詢與角標持續更新 */}
      <div className={styles.tabbedPanels} data-guide="monitoring-panels">
        <div className={styles.panelTabBar} role="tablist" aria-label={t("MonitoringPage.panelTabsAria")}>
          <button
            type="button"
            role="tab"
            aria-selected={panelTab === "alerts"}
            data-guide-tab="monitoring-alerts"
            className={`${styles.panelTab} ${panelTab === "alerts" ? styles.panelTabActive : ""}`}
            onClick={() => setPanelTab("alerts")}
          >
            {t("MonitoringPage.tabAlerts")}
            {alertCount != null && <span className={styles.panelTabBadge}>{alertCount}</span>}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={panelTab === "mining"}
            data-guide-tab="monitoring-mining"
            className={`${styles.panelTab} ${panelTab === "mining" ? styles.panelTabActive : ""}`}
            onClick={() => setPanelTab("mining")}
          >
            {t("MonitoringPage.tabMining")}
            {miningCount != null && <span className={styles.panelTabBadge}>{miningCount}</span>}
          </button>
        </div>
        <div className={panelTab === "alerts" ? undefined : styles.tabHidden}>
          <AlertsCard onCountChange={setAlertCount} />
        </div>
        <div className={panelTab === "mining" ? undefined : styles.tabHidden}>
          <MiningIncidentsPanel onCountChange={setMiningCount} />
        </div>
      </div>

      {ready && (<>
      {/* 節點用量：整卡收合，收起時標題列仍看得到在線摘要 */}
      <div className={styles.card} data-guide="monitoring-nodes">
        <button
          type="button"
          className={styles.cardHeaderToggle}
          onClick={toggleNodesOpen}
          aria-expanded={nodesOpen}
        >
          <div>
            <h2 className={styles.cardTitle}>{t("MonitoringPage.nodeUsageTitle")}</h2>
            <p className={styles.cardDesc}>{t("MonitoringPage.nodeUsageDesc")}</p>
          </div>
          <span className={styles.cardHeaderMeta}>
            {t("MonitoringPage.nodesSummary", {
              online: overview.nodes_online,
              total: overview.nodes_total,
            })}
            <MIcon name={nodesOpen ? "expand_less" : "expand_more"} size={18} />
          </span>
        </button>
        {nodesOpen && (<>
        <div className={styles.tableScroll}>
        <DataTable
          table={nodeTable}
          className={styles.table}
          renderRow={(row) => {
            const node = row.original;
            const online = node.status === "online";
            /* 離線節點沒有趨勢可看：列不可展開，也不給可點的 hover 暗示 */
            const expanded = online && expandedNode === node.node;
            return (
              <Fragment key={row.id}>
                <tr
                  className={`${styles.tr} ${online ? styles.trClickable : ""}`}
                  onClick={online ? () => setExpandedNode(expanded ? null : node.node) : undefined}
                >
                  <DataTableCells row={row} rowState={{ online, expanded }} />
                </tr>
                {expanded && (
                  <tr className={styles.trExpand}>
                    <td colSpan={7} className={styles.tdExpand}>
                      <NodeTrends node={node.node} />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          }}
        />
        </div>
        </>)}
      </div>

      {/* Top VMs */}
      <div className={styles.topGrid} data-guide="monitoring-top">
        <TopVmTable title={t("MonitoringPage.topCpuTitle")} entries={overview.top_cpu} metric="cpu" />
        <TopVmTable title={t("MonitoringPage.topMemTitle")} entries={overview.top_mem} metric="mem" />
      </div>
      </>)}
    </div>
  );
}
