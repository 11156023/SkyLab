import { Fragment, useCallback, useEffect, useState } from "react";
import styles from "./MonitoringPage.module.scss";
import MIcon from "../../../components/MIcon";
import RrdChart from "../../../components/RrdChart/RrdChart";
import MiningIncidentsPanel from "./MiningIncidentsPanel";
import { MonitoringService } from "../../../services/monitoring";
import { useToast } from "../../../hooks/useToast";

const TIMEFRAMES = [
  { value: "hour", label: "最近 1 小時" },
  { value: "day",  label: "最近 1 天" },
  { value: "week", label: "最近 1 週" },
];

const METRIC_LABELS = { cpu: "CPU", memory: "記憶體", disk: "磁碟" };
const SCOPE_LABELS  = { cluster: "叢集", node: "節點", vm: "VM" };

const RRD_SERIES = [
  { key: "cpu",    label: "CPU %",    color: "--color-info" },
  { key: "memory", label: "記憶體 %", color: "--color-success" },
];

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const tb = bytes / 1024 ** 4;
  if (tb >= 1) return `${tb.toFixed(2)} TB`;
  const gb = bytes / 1024 ** 3;
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  const mb = bytes / 1024 ** 2;
  return `${mb.toFixed(0)} MB`;
}

function formatUptime(seconds) {
  if (!seconds) return "—";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  if (days > 0) return `${days} 天 ${hours} 小時`;
  const minutes = Math.floor((seconds % 3600) / 60);
  return `${hours} 小時 ${minutes} 分`;
}

/** 將 PVE 節點 RRD 原始點位轉為圖表資料（CPU%、記憶體%） */
function mapNodeRrd(points) {
  return (points ?? [])
    .filter((p) => typeof p.time === "number")
    .map((p) => ({
      time: new Date(p.time * 1000).toLocaleTimeString("zh-TW", {
        hour: "2-digit",
        minute: "2-digit",
      }),
      cpu: typeof p.cpu === "number" ? Number((p.cpu * 100).toFixed(2)) : null,
      memory:
        typeof p.memused === "number" && typeof p.memtotal === "number" && p.memtotal > 0
          ? Number(((p.memused / p.memtotal) * 100).toFixed(2))
          : null,
    }));
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

function OverviewCard({ title, icon, pct, detail }) {
  return (
    <div className={styles.overviewCard}>
      <div className={styles.overviewTop}>
        <div className={styles.overviewInfo}>
          <span className={styles.overviewLabel}>{title}</span>
          <span className={styles.overviewValue}>
            {pct.toFixed(1)}
            <span className={styles.overviewUnit}>%</span>
          </span>
          <span className={styles.overviewDetail}>{detail}</span>
        </div>
        <div className={styles.overviewIcon}>
          <MIcon name={icon} size={20} />
        </div>
      </div>
      <UsageBar pct={pct} />
    </div>
  );
}

/** 節點展開後的趨勢圖（每 60 秒輪詢） */
function NodeTrends({ node, timeframe }) {
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
    return <div className={styles.trendLoading}>載入趨勢中…</div>;
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

function AlertsCard() {
  const toast = useToast();
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

  const handleAck = async (alertId) => {
    setAckBusy(alertId);
    try {
      await MonitoringService.ackAlert(alertId);
      await load();
    } catch (e) {
      toast.error(e?.message ?? "確認告警失敗");
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
            活動告警
          </h2>
          <p className={styles.cardDesc}>超過閾值的資源使用告警（每 30 秒更新）</p>
        </div>
        {alerts && alerts.length > 0 && (
          <span className={styles.alertCount}>{alerts.length}</span>
        )}
      </div>

      {alerts === null ? (
        <p className={styles.cardEmpty}>載入中…</p>
      ) : alerts.length === 0 ? (
        <div className={styles.cardEmpty}>
          <MIcon name="notifications_off" size={24} />
          <p>目前沒有活動告警</p>
        </div>
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
                      （閾值 {alert.threshold.toFixed(0)}%）
                    </span>
                  </div>
                  <p className={styles.alertTime}>
                    {new Date(alert.created_at).toLocaleString("zh-TW")}
                    {alert.acknowledged_at && " · 已確認"}
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
                  確認
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
  return (
    <div className={styles.card}>
      <div className={styles.cardHeader}>
        <h2 className={styles.cardTitle}>{title}</h2>
      </div>
      {entries.length === 0 ? (
        <p className={styles.cardEmpty}>無運行中的資源</p>
      ) : (
        <table className={styles.table}>
          <thead>
            <tr>
              <th className={styles.th}>VMID</th>
              <th className={styles.th}>名稱</th>
              <th className={styles.th}>節點</th>
              <th className={styles.th}>類型</th>
              <th className={`${styles.th} ${styles.thRight}`}>
                {metric === "cpu" ? "CPU" : "記憶體"}
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
      )}
    </div>
  );
}

export default function MonitoringPage() {
  const [timeframe, setTimeframe] = useState("hour");
  const [expandedNode, setExpandedNode] = useState(null);
  const [overview, setOverview] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    try {
      setOverview(await MonitoringService.getOverview());
      setError(false);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 30_000);
    return () => clearInterval(timer);
  }, [load]);

  if (loading) {
    return <div className={styles.pageLoading}>載入監控資料中…</div>;
  }

  if (error || !overview) {
    return (
      <div className={styles.page}>
        <div className={`${styles.card} ${styles.cardEmpty}`}>
          <MIcon name="warning" size={24} />
          <p>無法取得監控資料，請確認 Proxmox 連線狀態。</p>
        </div>
      </div>
    );
  }

  const cpuPct = overview.cpu_total > 0 ? (overview.cpu_used / overview.cpu_total) * 100 : 0;
  const memPct = overview.mem_total > 0 ? (overview.mem_used / overview.mem_total) * 100 : 0;
  const diskPct =
    overview.disk_total > 0 ? (overview.disk_used / overview.disk_total) * 100 : 0;

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>資源監控</h1>
          <p className={styles.pageSubtitle}>叢集資源使用、節點趨勢與閾值告警</p>
        </div>
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
      </div>

      {/* 叢集用量卡片 */}
      <div className={styles.statRow}>
        <OverviewCard
          title="CPU 用量"
          icon="memory"
          pct={cpuPct}
          detail={`${overview.cpu_used.toFixed(1)} / ${overview.cpu_total} 核心`}
        />
        <OverviewCard
          title="記憶體用量"
          icon="sd_card"
          pct={memPct}
          detail={`${formatBytes(overview.mem_used)} / ${formatBytes(overview.mem_total)}`}
        />
        <OverviewCard
          title="磁碟用量"
          icon="storage"
          pct={diskPct}
          detail={`${formatBytes(overview.disk_used)} / ${formatBytes(overview.disk_total)}`}
        />
        <div className={styles.overviewCard}>
          <div className={styles.overviewTop}>
            <div className={styles.overviewInfo}>
              <span className={styles.overviewLabel}>運行狀態</span>
              <span className={styles.statusLine}>
                節點在線{" "}
                <strong>
                  {overview.nodes_online}/{overview.nodes_total}
                </strong>
              </span>
              <span className={styles.statusLine}>
                VM 運行 <strong>{overview.vms_running}</strong>
                <span className={styles.mutedText}>
                  /{overview.vms_running + overview.vms_stopped}
                </span>
              </span>
              <span className={styles.statusLine}>
                LXC 運行 <strong>{overview.lxc_running}</strong>
                <span className={styles.mutedText}>
                  /{overview.lxc_running + overview.lxc_stopped}
                </span>
              </span>
            </div>
            <div className={styles.overviewIcon}>
              <MIcon name="monitor_heart" size={20} />
            </div>
          </div>
        </div>
      </div>

      {/* 活動告警 */}
      <AlertsCard />

      {/* 挖礦事件（模組 D，位置比照舊版監控頁） */}
      <MiningIncidentsPanel />

      {/* 節點用量 */}
      <div className={styles.card}>
        <div className={styles.cardHeader}>
          <div>
            <h2 className={styles.cardTitle}>節點用量</h2>
            <p className={styles.cardDesc}>點擊節點列展開使用趨勢圖</p>
          </div>
        </div>
        <table className={styles.table}>
          <thead>
            <tr>
              <th className={styles.th}>節點</th>
              <th className={styles.th}>狀態</th>
              <th className={`${styles.th} ${styles.thWide}`}>CPU</th>
              <th className={`${styles.th} ${styles.thWide}`}>記憶體</th>
              <th className={`${styles.th} ${styles.thWide}`}>磁碟</th>
              <th className={styles.th}>運行時間</th>
            </tr>
          </thead>
          <tbody>
            {overview.nodes.map((node) => {
              const online = node.status === "online";
              const nodeCpu = node.maxcpu > 0 ? node.cpu * 100 : 0;
              const nodeMem = node.maxmem > 0 ? (node.mem / node.maxmem) * 100 : 0;
              const nodeDisk = node.maxdisk > 0 ? (node.disk / node.maxdisk) * 100 : 0;
              const expanded = expandedNode === node.node;
              return (
                <Fragment key={node.node}>
                  <tr
                    className={`${styles.tr} ${styles.trClickable}`}
                    onClick={() => setExpandedNode(expanded ? null : node.node)}
                  >
                    <td className={styles.td}>
                      <span className={styles.nodeCell}>
                        <MIcon name={expanded ? "expand_more" : "chevron_right"} size={16} />
                        <MIcon name="dns" size={16} />
                        <strong>{node.node}</strong>
                      </span>
                    </td>
                    <td className={styles.td}>
                      <span
                        className={`${styles.badge} ${online ? styles.badge_ok : styles.badge_err}`}
                      >
                        {online ? "在線" : node.status}
                      </span>
                    </td>
                    <td className={styles.td}>
                      <div className={styles.usageCell}>
                        <div className={styles.usageMeta}>
                          <span>{nodeCpu.toFixed(1)}%</span>
                          <span className={styles.mutedText}>{node.maxcpu} 核心</span>
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
                    <td className={`${styles.td} ${styles.mutedCell}`}>
                      {formatUptime(node.uptime)}
                    </td>
                  </tr>
                  {expanded && (
                    <tr className={styles.trExpand}>
                      <td colSpan={6} className={styles.tdExpand}>
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

      {/* Top VMs */}
      <div className={styles.topGrid}>
        <TopVmTable title="CPU 用量 Top 5" entries={overview.top_cpu} metric="cpu" />
        <TopVmTable title="記憶體用量 Top 5" entries={overview.top_mem} metric="mem" />
      </div>
    </div>
  );
}
