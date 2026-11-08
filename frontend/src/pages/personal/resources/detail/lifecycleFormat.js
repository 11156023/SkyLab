/**
 * lifecycleFormat — 總覽（OverviewTab）與進階設定生命週期卡（LifecycleCard）共用的
 * 自動關機原因文案與到期日／時間格式化。
 *
 * 延長到期日對話框的最小日期（LifecycleCard 的 tomorrowIso）刻意不放這裡：
 * 那段要跟後端一樣以 UTC 日期比較，和這裡「依本地時區顯示」的規則不同。
 */

/** 時間戳依瀏覽器時區拆成 YYYY-MM-DD，與總覽「使用至」的本地時間顯示同一套 */
export function localDateIso(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/**
 * 目前實際生效的到期（YYYY-MM-DD）：到期日（expiry_date，TTL）與核准使用時段迄
 * （window_end_at）取較早者，跟後端 spec_change_service._effective_expiry 同一套規則。
 * 自己申請的機器通常沒有 expiry_date，讓它過期的是 window_end_at，延期按鈕也要讓這種機器看到。
 * 時段迄是時間戳，日期依本地時區拆：台灣的「11/08 03:00」在 UTC 還是 11/07，若用 UTC 日期，
 * 生命週期卡會寫 11/07、總覽卻寫 11/08 03:00（2026-11-06 ntub 實測）。
 * 兩者都沒有＝不限期，回 null。
 */
export function effectiveExpiryIso(resource) {
  const candidates = [];
  if (resource?.expiry_date) candidates.push(String(resource.expiry_date).slice(0, 10));
  if (resource?.window_end_at) {
    const end = new Date(resource.window_end_at);
    if (!Number.isNaN(end.getTime())) candidates.push(localDateIso(end));
  }
  return candidates.length ? candidates.sort()[0] : null;
}

/** 生效的到期是否來自核准使用時段（沒有到期日，或時段迄日比到期日早；同一天以到期日為準） */
export function isExpiryFromWindow(resource) {
  const effective = effectiveExpiryIso(resource);
  return Boolean(effective) && effective !== String(resource?.expiry_date ?? "").slice(0, 10);
}

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
