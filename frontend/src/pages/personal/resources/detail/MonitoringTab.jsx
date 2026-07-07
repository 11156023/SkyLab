import { useEffect, useState } from "react";
import styles from "./ResourceDetailPage.module.scss";
import MIcon from "../../../../components/MIcon";
import RrdChart from "../../../../components/RrdChart/RrdChart";
import { ResourcesService } from "../../../../services/resources";

const TIMEFRAMES = [
  { value: "hour",  label: "最近 1 小時" },
  { value: "day",   label: "最近 1 天" },
  { value: "week",  label: "最近 1 週" },
  { value: "month", label: "最近 1 月" },
  { value: "year",  label: "最近 1 年" },
];

const CHART_TABS = [
  { key: "cpu",     label: "CPU" },
  { key: "memory",  label: "記憶體" },
  { key: "network", label: "網路" },
];

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const gb = bytes / 1024 ** 3;
  if (gb >= 1) return `${gb.toFixed(2)} GB`;
  const mb = bytes / 1024 ** 2;
  return `${mb.toFixed(2)} MB`;
}

function StatCard({ title, pct, detail, icon }) {
  const num = Number.parseFloat(pct);
  return (
    <div className={styles.statCard}>
      <div className={styles.overviewTop}>
        <div className={styles.overviewInfo}>
          <span className={styles.factLabel}>{title}</span>
          <span className={styles.statPct}>
            {pct}
            <span className={styles.mutedText}>%</span>
          </span>
          <span className={styles.mutedText}>{detail}</span>
        </div>
        <span className={styles.specIcon}>
          <MIcon name={icon} size={18} />
        </span>
      </div>
      <div className={styles.usageBar}>
        <div
          className={`${styles.usageFill} ${num >= 90 ? styles.usageFill_danger : ""}`}
          style={{ width: `${Math.min(num, 100)}%` }}
        />
      </div>
    </div>
  );
}

export default function MonitoringTab({ vmid }) {
  const [timeframe, setTimeframe] = useState("hour");
  const [chartTab, setChartTab] = useState("cpu");
  const [current, setCurrent] = useState(null);
  const [rrd, setRrd] = useState(null);

  /* 即時狀態：每 5 秒輪詢 */
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const stats = await ResourcesService.getCurrentStats(vmid);
        if (!cancelled) setCurrent(stats);
      } catch {
        /* 下一輪再試 */
      }
    };
    load();
    const timer = setInterval(load, 5_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [vmid]);

  /* RRD 趨勢：每 30 秒輪詢 */
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await ResourcesService.getStats(vmid, timeframe);
        if (!cancelled) setRrd(res?.data ?? []);
      } catch {
        if (!cancelled) setRrd((prev) => prev ?? []);
      }
    };
    load();
    const timer = setInterval(load, 30_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [vmid, timeframe]);

  if (!current) return <p className={styles.stateText}>載入監控資料中…</p>;

  const cpuPct = current.cpu ? (current.cpu * 100).toFixed(2) : "0.00";
  const memPct =
    current.mem && current.maxmem
      ? ((current.mem / current.maxmem) * 100).toFixed(2)
      : "0.00";
  const diskPct =
    current.disk && current.maxdisk
      ? ((current.disk / current.maxdisk) * 100).toFixed(2)
      : "0.00";

  const chartData = (rrd ?? [])
    .filter((p) => typeof p.time === "number")
    .map((p) => ({
      time: new Date(p.time * 1000).toLocaleTimeString("zh-TW", {
        hour: "2-digit",
        minute: "2-digit",
      }),
      cpu: p.cpu != null ? Number((p.cpu * 100).toFixed(2)) : null,
      memory:
        p.mem != null && p.maxmem ? Number(((p.mem / p.maxmem) * 100).toFixed(2)) : null,
      netinKB: p.netin != null ? Number((p.netin / 1024).toFixed(2)) : null,
      netoutKB: p.netout != null ? Number((p.netout / 1024).toFixed(2)) : null,
    }));

  // 網路單位自適應（KB / MB）
  const maxNetKB = Math.max(
    ...chartData.map((d) => Math.max(d.netinKB ?? 0, d.netoutKB ?? 0)),
    0,
  );
  const useNetMB = maxNetKB >= 500;
  const netUnit = useNetMB ? "MB" : "KB";
  const netChartData = chartData.map((d) => ({
    ...d,
    netin: d.netinKB != null ? Number((d.netinKB / (useNetMB ? 1024 : 1)).toFixed(2)) : null,
    netout:
      d.netoutKB != null ? Number((d.netoutKB / (useNetMB ? 1024 : 1)).toFixed(2)) : null,
  }));

  return (
    <div className={styles.tabStack}>
      <div className={styles.monHead}>
        <h2 className={styles.cardTitle}>資源監控</h2>
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

      {/* 即時狀態卡片 */}
      <div className={styles.statGrid}>
        <StatCard
          title="CPU 用量"
          pct={cpuPct}
          detail={`${current.maxcpu ?? "—"} 核心`}
          icon="memory"
        />
        <StatCard
          title="記憶體"
          pct={memPct}
          detail={`${formatBytes(current.mem)} / ${formatBytes(current.maxmem)}`}
          icon="sd_card"
        />
        <StatCard
          title="磁碟"
          pct={diskPct}
          detail={`${formatBytes(current.disk)} / ${formatBytes(current.maxdisk)}`}
          icon="storage"
        />
        <div className={styles.statCard}>
          <div className={styles.overviewTop}>
            <div className={styles.overviewInfo}>
              <span className={styles.factLabel}>網路</span>
              <span className={styles.netLine}>↓ {formatBytes(current.netin)}</span>
              <span className={styles.netLine}>↑ {formatBytes(current.netout)}</span>
            </div>
            <span className={styles.specIcon}>
              <MIcon name="swap_vert" size={18} />
            </span>
          </div>
        </div>
      </div>

      {/* 歷史趨勢 */}
      <div className={styles.card}>
        <div className={styles.cardHeader}>
          <div>
            <h2 className={styles.cardTitle}>歷史資料</h2>
            <p className={styles.cardDesc}>共 {chartData.length} 個資料點</p>
          </div>
          <div className={styles.segment}>
            {CHART_TABS.map((t) => (
              <button
                key={t.key}
                type="button"
                className={`${styles.segmentBtn} ${chartTab === t.key ? styles.segmentActive : ""}`}
                onClick={() => setChartTab(t.key)}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>
        <div className={styles.cardBody}>
          {chartTab === "cpu" && (
            <RrdChart
              data={chartData}
              series={[{ key: "cpu", label: "CPU %", color: "--color-info" }]}
              unit="%"
              height={260}
            />
          )}
          {chartTab === "memory" && (
            <RrdChart
              data={chartData}
              series={[{ key: "memory", label: "記憶體 %", color: "--color-success" }]}
              unit="%"
              height={260}
            />
          )}
          {chartTab === "network" && (
            <RrdChart
              data={netChartData}
              series={[
                { key: "netin",  label: "下載", color: "--color-info" },
                { key: "netout", label: "上傳", color: "--color-danger" },
              ]}
              unit={netUnit}
              height={260}
            />
          )}
        </div>
      </div>
    </div>
  );
}
