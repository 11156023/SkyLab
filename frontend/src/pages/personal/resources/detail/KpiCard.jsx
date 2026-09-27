import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import MIcon from "../../../../components/MIcon";
import { DANGER_PCT, nextPeak, visiblePeak } from "./kpiBar";
import styles from "./KpiCard.module.scss";

/**
 * 總覽的單一指標卡（CPU／記憶體／磁碟／運行時間）。
 * - segments：用量條依單位切格（一顆核心、幾 GB 一格），數得出用掉幾個單位；0 表示不切。
 *   沒有讀數（關機、未回報）時仍畫空的格子，看得出配置了幾顆核心、幾 GB。
 * - trackPeak：記下開著這頁以來的最高讀數，在條上留刻痕、說明右端寫「最高 N%」（像音響的峰值保持）。
 * - live／sample：執行中時說明前有綠點，每拿到一筆新讀數（sample 換成新物件）閃一下。
 * 用量達 DANGER_PCT 時條與圖示一起轉紅。
 */
export default function KpiCard({
  icon, label, value, unit, caption, pct, text = false, segments = 0, trackPeak = false, live = false, sample = null,
}) {
  const { t } = useTranslation("personal");
  const hasBar = typeof pct === "number" && Number.isFinite(pct);
  const danger = hasBar && pct >= DANGER_PCT;
  /* 沒有讀數但切得出格子時，只畫配置的容量；不是進度條，不報給讀螢幕軟體當成 0% */
  const capacityOnly = !hasBar && segments > 1;

  const [peak, setPeak] = useState(null);
  useEffect(() => {
    setPeak((previous) => (trackPeak && hasBar ? nextPeak(previous, pct) : null));
  }, [pct, hasBar, trackPeak]);
  const shownPeak = visiblePeak(peak, hasBar ? pct : null);

  /* 換 key 讓綠點重新掛上，CSS 動畫才會再跑一次 */
  const [beat, setBeat] = useState(0);
  useEffect(() => {
    if (sample) setBeat((count) => count + 1);
  }, [sample]);

  return (
    <div className={`${styles.kpi} ${danger ? styles.danger : ""}`}>
      <div className={styles.head}>
        <span className={styles.label}>{label}</span>
        <span className={styles.icon}>
          <MIcon name={icon} size={18} />
        </span>
      </div>
      <div className={`${styles.value} ${text ? styles.valueText : ""}`}>
        {value}
        {unit && <span className={styles.unit}>{unit}</span>}
      </div>
      {(caption || shownPeak != null) && (
        <div className={styles.captionRow}>
          {live && <span key={beat} className={`${styles.liveDot} ${beat > 0 ? styles.liveDotFresh : ""}`} aria-hidden="true" />}
          {caption && <span className={styles.caption}>{caption}</span>}
          {shownPeak != null && <span className={styles.peak}>{t("OverviewTab.peak", { pct: shownPeak })}</span>}
        </div>
      )}
      {capacityOnly && (
        <div className={`${styles.bar} ${styles.barSegmented}`} style={{ "--segments": segments }} aria-hidden="true" />
      )}
      {hasBar && (
        <div
          className={`${styles.bar} ${segments > 1 ? styles.barSegmented : ""}`}
          style={segments > 1 ? { "--segments": segments } : undefined}
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div className={styles.barFill} style={{ width: `${Math.min(pct, 100)}%` }} />
          {shownPeak != null && <span className={styles.peakTick} style={{ left: `${Math.min(shownPeak, 100)}%` }} aria-hidden="true" />}
        </div>
      )}
    </div>
  );
}
