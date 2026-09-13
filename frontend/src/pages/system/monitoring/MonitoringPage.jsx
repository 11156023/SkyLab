import { Fragment, useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./MonitoringPage.module.scss";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import EmptyState from "../../../components/EmptyState/EmptyState";
import RrdChart from "../../../components/RrdChart/RrdChart";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import MiningIncidentsPanel from "./MiningIncidentsPanel";
import { MonitoringService } from "../../../services/monitoring";
import { useToast } from "../../../hooks/useToast";
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

/* ── 節點用量表的異常優先收斂 ──
   監控頁的用途是找問題，不是逐一巡禮：離線或任一資源偏高的節點永遠
   顯示且排最前，其餘按用量取前幾名，剩下收進「顯示全部」。 */
const NODE_ATTENTION_SHARE = 80;
const NODE_COLLAPSED_EXTRA = 5;

function nodeMaxShare(node) {
  const cpu = node.maxcpu > 0 ? node.cpu * 100 : 0;
  const mem = node.maxmem > 0 ? (node.mem / node.maxmem) * 100 : 0;
  const disk = node.maxdisk > 0 ? (node.disk / node.maxdisk) * 100 : 0;
  return Math.max(cpu, mem, disk);
}

function nodeNeedsAttention(node) {
  return node.status !== "online" || nodeMaxShare(node) >= NODE_ATTENTION_SHARE;
}

/** 需注意的在前，其餘用量高的在前；同分按名稱穩定排序，輪詢間不跳動 */
function rankNodes(nodes) {
  return [...nodes].sort((a, b) => {
    const aa = nodeNeedsAttention(a);
    const ba = nodeNeedsAttention(b);
    if (aa !== ba) return aa ? -1 : 1;
    const diff = nodeMaxShare(b) - nodeMaxShare(a);
    if (Math.abs(diff) > 0.001) return diff;
    return a.node.localeCompare(b.node);
  });
}

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

/** 節點展開後的趨勢圖（每 60 秒輪詢） */
function NodeTrends({ node, timeframe }) {
  const { t } = useTranslation("system");
  const RRD_SERIES = [
    { key: "cpu",    label: "CPU %",    color: "--color-info" },
    { key: "memory", label: t("MonitoringPage.memoryPercentLabel"), color: "--color-success" },
  ];
  const [data, setData] = useState(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const rrd = await MonitoringService.getNodeRrd(node, timeframe);
        if (!cancelled) setData(mapNodeRrd(rrd));
      } catch {
        if (!cancelled) setData([]);
      }
    };
    load();
    const timer = setInterval(load, 60_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [node, timeframe]);

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

function AlertsCard({ onCountChange }) {
  const { t } = useTranslation("system");
  const toast = useToast();
  const METRIC_LABELS = { cpu: "CPU", memory: t("MonitoringPage.memoryLabel"), disk: t("MonitoringPage.diskLabel") };
  const SCOPE_LABELS  = { cluster: t("MonitoringPage.scopeCluster"), node: t("MonitoringPage.scopeNode"), vm: "VM" };
  const [alerts, setAlerts] = useState(null);
  const [ackBusy, setAckBusy] = useState(null);

  const load = useCallback(async () => {
    try {
      setAlerts(await MonitoringService.listAlerts({ active: true }));
    } catch {
      setAlerts((prev) => prev ?? []);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 30_000);
    return () => clearInterval(timer);
  }, [load]);

  /* 分頁角標要顯示筆數，載入後回報給頁面 */
  useEffect(() => {
    if (alerts !== null) onCountChange?.(alerts.length);
  }, [alerts, onCountChange]);

  const handleAck = async (alertId) => {
    setAckBusy(alertId);
    try {
      await MonitoringService.ackAlert(alertId);
      await load();
    } catch (e) {
      toast.error(e?.message ?? t("MonitoringPage.toastAckFailed"));
    } finally {
      setAckBusy(null);
    }
  };

  return (
    <div className={styles.card}>
      <div className={styles.cardHeader}>
        <div>
          <h2 className={styles.cardTitle}>
            <MIcon name="notifications" size={18} />
            {t("MonitoringPage.activeAlertsTitle")}
          </h2>
          <p className={styles.cardDesc}>{t("MonitoringPage.activeAlertsDesc")}</p>
        </div>
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
                    <span className={styles.alertTarget}>{alert.target}</span>
                    <span className={styles.alertMetric}>
                      {METRIC_LABELS[alert.metric] ?? alert.metric} {alert.value.toFixed(0)}%
                    </span>
                    <span className={styles.alertThreshold}>
                      {t("MonitoringPage.thresholdSuffix", { threshold: alert.threshold.toFixed(0) })}
                    </span>
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

function TopVmTable({ title, entries, metric }) {
  const { t } = useTranslation("system");
  return (
    <div className={styles.card}>
      <div className={styles.cardHeader}>
        <h2 className={styles.cardTitle}>{title}</h2>
      </div>
      {entries.length === 0 ? (
        <EmptyState icon="dns" title={t("MonitoringPage.emptyNoRunningResources")} />
      ) : (
        <div className={styles.tableScroll}>
        {/* 半版寬容不下 5 欄：VMID／名稱／類型合併成一欄（#24） */}
        <table className={styles.topTable}>
          <thead>
            <tr>
              <th className={styles.th}>{t("MonitoringPage.colMachine")}</th>
              <th className={styles.th}>{t("MonitoringPage.colNode")}</th>
              <th className={`${styles.th} ${styles.thRight}`}>
                {metric === "cpu" ? "CPU" : t("MonitoringPage.memoryLabel")}
              </th>
            </tr>
          </thead>
          <tbody>
            {entries.map((vm) => (
              <tr key={vm.vmid} className={styles.tr}>
                <td className={styles.td}>
                  <div className={styles.vmCell}>
                    <span className={styles.typeBadge}>
                      {vm.type === "qemu" ? "VM" : "LXC"}
                    </span>
                    <div className={styles.vmCellText}>
                      <strong>{vm.name}</strong>
                      <span className={styles.mutedText}>#{vm.vmid}</span>
                    </div>
                  </div>
                </td>
                <td className={`${styles.td} ${styles.mutedCell}`}>{vm.node}</td>
                <td className={`${styles.td} ${styles.numericCell}`}>
                  {metric === "cpu" ? `${(vm.cpu * 100).toFixed(1)}%` : formatBytes(vm.mem)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
    </div>
  );
}

export default function MonitoringPage() {
  const { t } = useTranslation("system");
  const TIMEFRAMES = [
    { value: "hour", label: t("MonitoringPage.timeframeHour") },
    { value: "day",  label: t("MonitoringPage.timeframeDay") },
    { value: "week", label: t("MonitoringPage.timeframeWeek") },
  ];
  const [timeframe, setTimeframe] = useState("hour");
  const [expandedNode, setExpandedNode] = useState(null);
  const [overview, setOverview] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  /* 警告與挖礦事件收進分頁（#24）；筆數由面板載入後回報 */
  const [panelTab, setPanelTab] = useState("alerts");
  const [alertCount, setAlertCount] = useState(null);
  const [miningCount, setMiningCount] = useState(null);
  /* 節點多時預設只列需注意與用量最高的，其餘收進「顯示全部」 */
  const [showAllNodes, setShowAllNodes] = useState(false);

  const load = useCallback(async (signal) => {
    try {
      setOverview(await MonitoringService.getOverview({ signal }));
      setError(false);
    } catch (err) {
      if (!err?.cancelled) setError(true);
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    const timer = setInterval(() => load(), 30_000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [load]);

  if (loading) {
    return <LoadingState fullPage text={t("MonitoringPage.loadingOverview")} />;
  }

  if (error || !overview) {
    return (
      <div className={styles.page}>
        <div className={`${styles.card} ${styles.cardEmpty}`}>
          <MIcon name="warning" size={24} />
          <p>{t("MonitoringPage.errorFetchOverview")}</p>
        </div>
      </div>
    );
  }

  const rankedNodes = rankNodes(overview.nodes);
  const collapsedCount = Math.min(
    rankedNodes.length,
    rankedNodes.filter(nodeNeedsAttention).length + NODE_COLLAPSED_EXTRA,
  );
  const hiddenNodeCount = rankedNodes.length - collapsedCount;
  const nodesToRender = showAllNodes || hiddenNodeCount === 0
    ? rankedNodes
    : rankedNodes.slice(0, collapsedCount);

  const cpuPct = overview.cpu_total > 0 ? (overview.cpu_used / overview.cpu_total) * 100 : 0;
  const memPct = overview.mem_total > 0 ? (overview.mem_used / overview.mem_total) * 100 : 0;
  const diskPct =
    overview.disk_total > 0 ? (overview.disk_used / overview.disk_total) * 100 : 0;

  return (
    <div className={styles.page}>
      <PageHeader title={t("MonitoringPage.pageTitle")} subtitle={t("MonitoringPage.pageSubtitle")}>
        <div className={styles.pageActions}>
          <div className={styles.segment}>
            {TIMEFRAMES.map((t) => (
              <button
                key={t.value}
                type="button"
                className={`${styles.segmentBtn} ${timeframe === t.value ? styles.segmentActive : ""}`}
                onClick={() => setTimeframe(t.value)}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>
      </PageHeader>

      {/* 叢集用量卡片 */}
      <div className={styles.statRow}>
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
          <span className={styles.overviewLabel}>{t("MonitoringPage.runningStatus")}</span>
          <div className={styles.statusGrid}>
            <div>
              <span>{t("MonitoringPage.nodesOnline")}</span>
              <strong>{overview.nodes_online}/{overview.nodes_total}</strong>
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

      {/* 警告與挖礦事件收進分頁（#24）；兩個面板保持掛載，輪詢與角標持續更新 */}
      <div className={styles.panelTabs}>
        <SegmentedControl
          options={[
            { value: "alerts", label: t("MonitoringPage.tabAlerts"), badge: alertCount ?? undefined },
            { value: "mining", label: t("MonitoringPage.tabMining"), badge: miningCount ?? undefined },
          ]}
          value={panelTab}
          onChange={setPanelTab}
          ariaLabel={t("MonitoringPage.panelTabsAria")}
        />
      </div>
      <div className={panelTab === "alerts" ? undefined : styles.tabHidden}>
        <AlertsCard onCountChange={setAlertCount} />
      </div>
      <div className={panelTab === "mining" ? undefined : styles.tabHidden}>
        <MiningIncidentsPanel onCountChange={setMiningCount} />
      </div>

      {/* 節點用量 */}
      <div className={styles.card}>
        <div className={styles.cardHeader}>
          <div>
            <h2 className={styles.cardTitle}>{t("MonitoringPage.nodeUsageTitle")}</h2>
            <p className={styles.cardDesc}>{t("MonitoringPage.nodeUsageDesc")}</p>
          </div>
        </div>
        <div className={styles.tableScroll}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th className={styles.th}>{t("MonitoringPage.colNode")}</th>
              <th className={styles.th}>{t("MonitoringPage.colStatus")}</th>
              <th className={`${styles.th} ${styles.thWide}`}>CPU</th>
              <th className={`${styles.th} ${styles.thWide}`}>{t("MonitoringPage.memoryLabel")}</th>
              <th className={`${styles.th} ${styles.thWide}`}>{t("MonitoringPage.diskLabel")}</th>
              <th className={`${styles.th} ${styles.thRight}`}>VM / LXC</th>
              <th className={styles.th}>{t("MonitoringPage.colUptime")}</th>
            </tr>
          </thead>
          <tbody>
            {nodesToRender.map((node) => {
              const online = node.status === "online";
              const nodeCpu = node.maxcpu > 0 ? node.cpu * 100 : 0;
              const nodeMem = node.maxmem > 0 ? (node.mem / node.maxmem) * 100 : 0;
              const nodeDisk = node.maxdisk > 0 ? (node.disk / node.maxdisk) * 100 : 0;
              /* 離線節點沒有趨勢可看：列不可展開，也不給可點的 hover 暗示 */
              const expanded = online && expandedNode === node.node;
              return (
                <Fragment key={node.node}>
                  <tr
                    className={`${styles.tr} ${online ? styles.trClickable : ""}`}
                    onClick={online ? () => setExpandedNode(expanded ? null : node.node) : undefined}
                  >
                    <td className={styles.td}>
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
                    </td>
                    <td className={styles.td}>
                      <span
                        className={`${styles.badge} ${online ? styles.badge_ok : styles.badge_err}`}
                      >
                        {online ? t("MonitoringPage.online") : node.status}
                      </span>
                    </td>
                    <td className={styles.td}>
                      <div className={styles.usageCell}>
                        <div className={styles.usageMeta}>
                          <span>{nodeCpu.toFixed(1)}%</span>
                          <span className={styles.mutedText}>{t("MonitoringPage.coresLabel", { count: node.maxcpu })}</span>
                        </div>
                        <UsageBar pct={nodeCpu} />
                      </div>
                    </td>
                    <td className={styles.td}>
                      <div className={styles.usageCell}>
                        <div className={styles.usageMeta}>
                          <span>{nodeMem.toFixed(1)}%</span>
                          <span className={styles.mutedText}>
                            {formatBytes(node.mem)} / {formatBytes(node.maxmem)}
                          </span>
                        </div>
                        <UsageBar pct={nodeMem} />
                      </div>
                    </td>
                    <td className={styles.td}>
                      <div className={styles.usageCell}>
                        <div className={styles.usageMeta}>
                          <span>{nodeDisk.toFixed(1)}%</span>
                          <span className={styles.mutedText}>
                            {formatBytes(node.disk)} / {formatBytes(node.maxdisk)}
                          </span>
                        </div>
                        <UsageBar pct={nodeDisk} />
                      </div>
                    </td>
                    <td className={`${styles.td} ${styles.numericCell}`}>
                      {node.vm_count}
                    </td>
                    <td className={`${styles.td} ${styles.mutedCell}`}>
                      {formatUptime(node.uptime, t)}
                    </td>
                  </tr>
                  {expanded && (
                    <tr className={styles.trExpand}>
                      <td colSpan={7} className={styles.tdExpand}>
                        <NodeTrends node={node.node} timeframe={timeframe} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
        </div>
        {hiddenNodeCount > 0 && (
          <button
            type="button"
            className={styles.showAllBtn}
            onClick={() => setShowAllNodes((value) => !value)}
          >
            <MIcon name={showAllNodes ? "expand_less" : "expand_more"} size={16} />
            {showAllNodes
              ? t("MonitoringPage.collapseNodes", { count: hiddenNodeCount })
              : t("MonitoringPage.showAllNodes", { count: rankedNodes.length })}
          </button>
        )}
      </div>

      {/* Top VMs */}
      <div className={styles.topGrid}>
        <TopVmTable title={t("MonitoringPage.topCpuTitle")} entries={overview.top_cpu} metric="cpu" />
        <TopVmTable title={t("MonitoringPage.topMemTitle")} entries={overview.top_mem} metric="mem" />
      </div>
    </div>
  );
}
