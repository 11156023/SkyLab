import { useEffect, useState } from "react";
import styles from "./TeachingPage.module.scss";
import { TeachingService } from "../../services/teaching";

/**
 * 熱圖格顏色（資料視覺化漸層語意，非 UI 警示色，
 * 比照 AvailabilityPanel 例外保留橘色）
 */
function cellClass(entry) {
  if (entry.activity === "stopped") return styles.cell_stopped;
  if (entry.activity === "stale") return styles.cell_stale;
  if (entry.cpu_percent >= 80) return styles.cell_hot;
  if (entry.cpu_percent >= 50) return styles.cell_warm;
  if (entry.cpu_percent >= 10) return styles.cell_active;
  return styles.cell_idle;
}

function formatUptime(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return h > 0 ? `${h} 小時 ${m} 分` : `${m} 分`;
}

function cellTitle(entry) {
  return [
    `${entry.owner_name ?? "—"}（${entry.name ?? entry.vmid}）`,
    `狀態：${entry.status}`,
    `CPU：${entry.cpu_percent}%　RAM：${entry.mem_percent}%`,
    `開機時長：${formatUptime(entry.uptime_seconds)}`,
    entry.activity === "stale" ? "⚠ 長期無動靜" : null,
  ]
    .filter(Boolean)
    .join("\n");
}

/** 學生進度熱圖：30 秒輪詢 */
export default function HeatmapPanel({ groupId }) {
  const [entries, setEntries] = useState(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await TeachingService.getHeatmap(groupId);
        if (!cancelled) setEntries(data ?? []);
      } catch {
        if (!cancelled) setEntries((prev) => prev ?? []);
      }
    };
    load();
    const timer = setInterval(load, 30_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [groupId]);

  return (
    <div className={styles.card}>
      <div className={styles.cardHeader}>
        <h2 className={styles.cardTitle}>學生進度熱圖（30 秒自動更新）</h2>
      </div>
      <div className={styles.cardBody}>
        <div className={styles.legend}>
          <span className={styles.legend_stopped}>■ 灰＝關機</span>
          <span className={styles.legend_active}>■ 綠＝運行</span>
          <span className={styles.legend_hot}>■ 橘/紅＝高 CPU</span>
          <span className={styles.legend_stale}>■ 深灰＝長期無動靜</span>
        </div>

        {entries === null ? (
          <p className={styles.stateText}>載入中…</p>
        ) : entries.length === 0 ? (
          <p className={styles.stateText}>此群組沒有學生 VM</p>
        ) : (
          <div className={styles.heatGrid}>
            {entries.map((entry) => (
              <div
                key={entry.vmid}
                className={`${styles.heatCell} ${cellClass(entry)}`}
                title={cellTitle(entry)}
              >
                {entry.vmid}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
