import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import styles from "./QuotaUsageBar.module.scss";
import { useMyQuotaUsageQuery } from "../../queries/quotas";
import { useLoadError } from "../../queries/resourceDetail";

function Meter({ label, used, max, unit }) {
  const { t } = useTranslation("components");
  const unlimited = max === 0;
  const pct = !unlimited && max > 0 ? Math.min(100, (used / max) * 100) : 0;
  return (
    <div className={styles.meter}>
      <div className={styles.meterHead}>
        <span className={styles.meterLabel}>{label}</span>
        <span className={`${styles.meterValue} ${pct >= 90 ? styles.meterValue_over : ""}`}>
          {unlimited ? t("QuotaUsageBar.unlimited", { used, unit }) : t("QuotaUsageBar.usedOfMax", { used, max, unit })}
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

/** 任一項達 90% 就提示怎麼要更多資源 */
function nearLimit(data) {
  return [
    [data.used_cpu_cores, data.quota.max_cpu_cores],
    [data.used_memory_mb, data.quota.max_memory_mb],
    [data.used_disk_gb, data.quota.max_disk_gb],
    [data.used_instances, data.quota.max_instances],
  ].some(([used, max]) => max > 0 && used / max >= 0.9);
}

/** 我的配額用量條（掛在「我的資源」頁頂部） */
export default function QuotaUsageBar() {
  const { t } = useTranslation("components");

  /* 開通、刪機、規格調整後用量會變：跟下方機器列表一樣每 30 秒靜默刷新。
     已有數字時刷新失敗保留舊數字，不閃成錯誤；從沒拿到數字就失敗時顯示佔位，
     背景重試期間也維持佔位（不會每 30 秒消失一下） */
  const usageQuery = useMyQuotaUsageQuery();
  const data = usageQuery.data ?? null;
  const loadError = useLoadError(usageQuery).error;
  const failed = !data && loadError !== null;

  if (!data && !failed) return null;

  return (
    <div className={styles.card} data-guide="resource-quota">
      <div className={styles.cardHead}>
        <span className={styles.cardTitle}>{t("QuotaUsageBar.title")}</span>
        <span className={styles.cardDesc}>{t("QuotaUsageBar.desc")}</span>
      </div>
      {failed ? (
        /* 載入失敗顯示佔位而非整條消失，使用者才知道有配額這回事 */
        <p className={styles.loadFailed}>{t("Error.generic", { ns: "common" })}</p>
      ) : (
        <>
          <div className={styles.meterGrid}>
            <Meter
              label="CPU"
              used={data.used_cpu_cores}
              max={data.quota.max_cpu_cores}
              unit="cores"
            />
            <Meter
              label={t("QuotaUsageBar.memory")}
              used={Math.round(data.used_memory_mb / 1024)}
              max={Math.round(data.quota.max_memory_mb / 1024)}
              unit="GB"
            />
            <Meter label={t("QuotaUsageBar.disk")} used={data.used_disk_gb} max={data.quota.max_disk_gb} unit="GB" />
            <Meter label={t("QuotaUsageBar.instances")} used={data.used_instances} max={data.quota.max_instances} unit={t("QuotaUsageBar.unitInstances")} />
          </div>
          {nearLimit(data) && (
            <p className={styles.nearLimitHint}>
              {t("QuotaUsageBar.nearLimitHint")}
              <Link to="/my-requests">{t("QuotaUsageBar.nearLimitLink")}</Link>
            </p>
          )}
        </>
      )}
    </div>
  );
}
