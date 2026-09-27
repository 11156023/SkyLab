/**
 * lifecycleFormat — 總覽（OverviewTab）與進階設定生命週期卡（LifecycleCard）共用的
 * 自動關機原因文案與到期日／時間格式化。
 *
 * 延長到期日對話框的最小日期（LifecycleCard 的 tomorrowIso）刻意不放這裡：
 * 那段要跟後端一樣以 UTC 日期比較，和這裡「依本地時區顯示」的規則不同。
 */

export const AUTO_STOP_REASON_KEYS = {
  window_grace: "LifecycleCard.reasonWindowGrace",
  practice_quota: "LifecycleCard.reasonPracticeQuota",
  ttl_expired: "LifecycleCard.reasonTtlExpired",
  idle: "LifecycleCard.reasonIdle",
};

/* expiry_date 是純日期字串（YYYY-MM-DD）；用本地時區拆解，避免 UTC 解析在時區邊界差一天 */
export function parseDateOnly(value) {
  const [y, m, d] = String(value).slice(0, 10).split("-").map(Number);
  return y && m && d ? new Date(y, m - 1, d) : new Date(value);
}

/** 純日期（expiry_date）；空值回 null 讓呼叫端決定替代文字 */
export function formatDate(value, lang) {
  if (!value) return null;
  return parseDateOnly(value).toLocaleDateString(lang, { year: "numeric", month: "2-digit", day: "2-digit" });
}

/** 日期＋時分（auto_stop_at、idle_since 等時間戳）；空值回 null */
export function formatDateTime(value, lang) {
  if (!value) return null;
  return new Date(value).toLocaleString(lang, {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}
