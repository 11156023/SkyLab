import { Fragment, useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./MonitoringPage.module.scss";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import EmptyState from "../../../components/EmptyState/EmptyState";
import RrdChart from "../../../components/RrdChart/RrdChart";
import MiningIncidentsPanel from "./MiningIncidentsPanel";
import SystemHealthCard from "./SystemHealthCard";
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

/* 節點用量整卡收合的偏好記在本機，重整後維持使用者的選擇 */
const NODES_OPEN_STORAGE_KEY = "skylab.monitoringNodesOpen";

function loadNodesOpen() {
  try {
    return window.localStorage.getItem(NODES_OPEN_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function saveNodesOpen(open) {
  try {
    window.localStorage.setItem(NODES_OPEN_STORAGE_KEY, open ? "1" : "0");
  } catch {
    // localStorage 不可用時偏好僅本次瀏覽生效
  }
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

/** 節點展開後的趨勢圖（每 60 秒輪詢）；固定顯示最近 1 天，時間範圍切換器已移除（2026-09） */
function NodeTrends({ node }) {
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
        const rrd = await MonitoringService.getNodeRrd(node, "day");
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
  }, [node]);

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
  const SCOPE_LABELS  = {
    cluster: t("MonitoringPage.scopeCluster"),
    node: t("MonitoringPage.scopeNode"),
    vm: "VM",
    system: t("MonitoringPage.scopeSystem"),
  };
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
        <table className={styles.topTable}>
          <thead>
            <tr>
              <th className={styles.th}>VMID</th>
              <th className={styles.th}>{t("MonitoringPage.colName")}</th>
              <th className={styles.th}>{t("MonitoringPage.colNode")}</th>
              <th className={styles.th}>{t("MonitoringPage.colType")}</th>
              <th className={styles.th}>
                {metric === "cpu" ? "CPU" : t("MonitoringPage.memoryLabel")}
              </th>
            </tr>
          </thead>
          <tbody>
            {entries.map((vm) => (
              <tr key={vm.vmid} className={styles.tr}>
                <td className={`${styles.td} ${styles.monoCell}`}>{vm.vmid}</td>
                <td className={styles.td}>{vm.name}</td>
                <td className={`${styles.td} ${styles.mutedCell}`}>{vm.node}</td>
                <td className={styles.td}>
                  <span className={styles.typeBadge}>
                    {vm.type === "qemu" ? "VM" : "LXC"}
                  </span>
                </td>
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
  const [expandedNode, setExpandedNode] = useState(null);
  const [overview, setOverview] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  /* 警告與挖礦事件收進分頁（#24）；筆數由面板載入後回報 */
  const [panelTab, setPanelTab] = useState("alerts");
  const [alertCount, setAlertCount] = useState(null);
  const [miningCount, setMiningCount] = useState(null);
  /* 節點用量整卡收合：預設收起省版面，標題列保留在線摘要 */
  const [nodesOpen, setNodesOpen] = useState(loadNodesOpen);
  /* 監控 stack 有啟用（後端連得到 Grafana）才顯示連結；查詢失敗就當沒啟用。
     同一支 API 會設定 Grafana 免密碼登入的 cookie（效期數小時），頁面開著時定期續期 */
  const [grafanaUrl, setGrafanaUrl] = useState(null);

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

  function toggleNodesOpen() {
    setNodesOpen((open) => {
      saveNodesOpen(!open);
      return !open;
    });
  }

  /* 系統健康卡與警告面板不依賴 PVE：概況還在載入或 PVE 連不上時照樣顯示——
     PVE 掛掉的當下，正是最需要看平台健康與系統告警的時候 */
  const ready = !loading && !error && overview != null;
  const cpuPct = ready && overview.cpu_total > 0 ? (overview.cpu_used / overview.cpu_total) * 100 : 0;
  const memPct = ready && overview.mem_total > 0 ? (overview.mem_used / overview.mem_total) * 100 : 0;
  const diskPct =
    ready && overview.disk_total > 0 ? (overview.disk_used / overview.disk_total) * 100 : 0;

  return (
    <div className={styles.page}>
      <PageHeader title={t("MonitoringPage.pageTitle")}>
        {grafanaUrl && (
          <a
            className={styles.linkBtn}
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
      <div className={styles.tabbedPanels}>
        <div className={styles.panelTabBar} role="tablist" aria-label={t("MonitoringPage.panelTabsAria")}>
          <button
            type="button"
            role="tab"
            aria-selected={panelTab === "alerts"}
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
      <div className={styles.card}>
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
        <table className={styles.table}>
          <thead>
            <tr>
              <th className={styles.th}>{t("MonitoringPage.colNode")}</th>
              <th className={styles.th}>{t("MonitoringPage.colStatus")}</th>
              <th className={`${styles.th} ${styles.thWide}`}>CPU</th>
              <th className={`${styles.th} ${styles.thWide}`}>{t("MonitoringPage.memoryLabel")}</th>
              <th className={`${styles.th} ${styles.thWide}`}>{t("MonitoringPage.diskLabel")}</th>
              <th className={styles.th}>VM / LXC</th>
              <th className={styles.th}>{t("MonitoringPage.colUptime")}</th>
            </tr>
          </thead>
          <tbody>
            {overview.nodes.map((node) => {
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
                        className={`${styles.badge} ${online ? styles.badge_success : styles.badge_danger}`}
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
                        <NodeTrends node={node.node} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
        </div>
        </>)}
      </div>

      {/* Top VMs */}
      <div className={styles.topGrid}>
        <TopVmTable title={t("MonitoringPage.topCpuTitle")} entries={overview.top_cpu} metric="cpu" />
        <TopVmTable title={t("MonitoringPage.topMemTitle")} entries={overview.top_mem} metric="mem" />
      </div>
      </>)}
    </div>
  );
}
