/* 申請表單的硬體滑桿上限要跟配額同步。
   後端送單時檢查的是「上限 − 已用（含尚未佈建的申請單預約）」，
   所以表單能拉到的最大值就是這個剩餘量；上限 0 代表無限制，維持表單原本的上限。 */

/** /quotas/my-usage 回應 → 各項剩餘量；null 表示無限制（或還沒載入）。 */
export function quotaRemaining(usage) {
  if (!usage?.quota) {
    return { cores: null, memoryMb: null, diskGb: null, instances: null };
  }
  const left = (max, used) => (
    max > 0 ? Math.max(0, Number(max) - Number(used || 0)) : null
  );
  const { quota } = usage;
  return {
    cores: left(quota.max_cpu_cores, usage.used_cpu_cores),
    memoryMb: left(quota.max_memory_mb, usage.used_memory_mb),
    diskGb: left(quota.max_disk_gb, usage.used_disk_gb),
    instances: left(quota.max_instances, usage.used_instances),
  };
}

/** 滑桿範圍：上限取「表單原本上限」與「剩餘配額（對齊步進）」較小者。
 *  剩餘不到下限時 short=true，滑桿停在下限（這張單送出去一定被擋）。 */
export function sliderRange({ min, max, step = 1, remaining = null }) {
  if (remaining == null) return { min, max, quotaCapped: false, short: false };
  if (remaining < min) return { min, max: min, quotaCapped: true, short: true };
  const aligned = min + Math.floor((remaining - min) / step) * step;
  if (aligned >= max) return { min, max, quotaCapped: false, short: false };
  return { min, max: aligned, quotaCapped: true, short: false };
}

/** 規格調整的範圍：後端只對「調大的部分」扣配額，所以上限是「目前值＋剩餘配額」
 *  （對齊步進），下限不變（調小永遠可以）。剩餘 0 時上限就是目前值。
 *  目前值本來就超過表單上限時不往下砍，讓滑桿至少容得下目前值。 */
export function growthRange({ min, max, step = 1, current, remaining = null }) {
  const ceiling = Math.max(max, current);
  if (remaining == null) return { min, max: ceiling, quotaCapped: false, exhausted: false };
  const grow = Math.floor(Math.max(0, remaining) / step) * step;
  const capped = Math.max(min, current + grow);
  if (capped >= ceiling) return { min, max: ceiling, quotaCapped: false, exhausted: false };
  return { min, max: capped, quotaCapped: true, exhausted: grow === 0 };
}

/** 把值夾進範圍內（只往下壓超出上限的部分，低於下限的交給原本的邏輯）。 */
export function clampToRange(value, range) {
  return Math.min(Number(value), range.max);
}

/** 刻度：保留落在範圍內的預設刻度，最後一格固定是上限本身；
 *  離上限太近的刻度會跟上限的標籤疊在一起，拿掉。 */
export function sliderTicks(candidates, range, format = String) {
  const { min, max } = range;
  if (max <= min) return [{ value: min, label: format(min), left: 0 }];
  const span = max - min;
  const minGap = span * 0.12;
  const ticks = candidates
    .filter((v) => v >= min && v < max && max - v >= minGap)
    .map((v) => ({ value: v, label: format(v), left: ((v - min) / span) * 100 }));
  ticks.push({ value: max, label: format(max), left: 100 });
  return ticks;
}
