import { useEffect, useState } from "react";
import styles from "./Teaching.module.scss";
import { QuotasService } from "../../services/quotas";

function Meter({ label, used, max, unit }) {
  const pct = max > 0 ? Math.min(100, (used / max) * 100) : 0;
  return (
    <div className={styles.meter}>
      <div className={styles.meterHead}>
        <span className={styles.meterLabel}>{label}</span>
        <span className={`${styles.meterValue} ${pct >= 90 ? styles.meterValue_over : ""}`}>
          {used} / {max} {unit}
        </span>
      </div>
      <div className={styles.meterTrack}>
        <div
          className={`${styles.meterFill} ${pct >= 90 ? styles.meterFill_danger : ""}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

/** 我的配額用量條（掛在「我的資源」頁頂部） */
export default function QuotaUsageBar() {
  const [data, setData] = useState(null);

  useEffect(() => {
    let cancelled = false;
    QuotasService.getMyUsage()
      .then((res) => !cancelled && setData(res))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  if (!data) return null;

  return (
    <div className={styles.card}>
      <div className={styles.meterGrid}>
        <Meter
          label="CPU"
          used={data.used_cpu_cores}
          max={data.quota.max_cpu_cores}
          unit="cores"
        />
        <Meter
          label="記憶體"
          used={Math.round(data.used_memory_mb / 1024)}
          max={Math.round(data.quota.max_memory_mb / 1024)}
          unit="GB"
        />
        <Meter label="磁碟" used={data.used_disk_gb} max={data.quota.max_disk_gb} unit="GB" />
        <Meter label="實例" used={data.used_instances} max={data.quota.max_instances} unit="台" />
      </div>
    </div>
  );
}
