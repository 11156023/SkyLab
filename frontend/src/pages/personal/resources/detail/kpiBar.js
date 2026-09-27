/* 指標卡（KpiCard）底部用量條的純邏輯：切幾格、峰值什麼時候值得標。 */

/** 用量達這個百分比，條與圖示轉紅（沿用原本 barFill_danger 的門檻） */
export const DANGER_PCT = 90;

/** 最多切幾格；再多就細到數不清，改回一條不分格 */
export const MAX_SEGMENTS = 16;

/** 峰值比目前讀數高出這麼多才標，刻痕貼著填色時沒有意義 */
export const PEAK_MIN_GAP = 3;

const GB = 1024 ** 3;
const GB_STEPS = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512];

/** CPU：一顆核心一格；只有一顆或多到數不清時不分格 */
export function coreSegments(cores) {
  const count = Number(cores);
  return Number.isInteger(count) && count >= 2 && count <= MAX_SEGMENTS ? count : 0;
}

/**
 * 記憶體／磁碟：每格 1、2、4… GB，取格數不超過 16 的最小一檔。
 * 容量除不盡（例如 1.5 GB）時不分格，免得最後一格比別格小。
 */
export function gbSegments(bytes) {
  const gb = Number(bytes) / GB;
  if (!Number.isFinite(gb) || gb <= 0) return 0;
  const step = GB_STEPS.find((size) => gb / size <= MAX_SEGMENTS);
  if (!step) return 0;
  const count = gb / step;
  const rounded = Math.round(count);
  return Math.abs(count - rounded) < 1e-6 && rounded >= 2 ? rounded : 0;
}

/** 累計到目前為止的最高讀數；沒有讀數（關機）時歸零 */
export function nextPeak(previous, pct) {
  if (pct == null) return null;
  return previous == null ? pct : Math.max(previous, pct);
}

/** 要不要把峰值標出來：比目前讀數高出 PEAK_MIN_GAP 以上才標 */
export function visiblePeak(peak, pct) {
  return peak != null && pct != null && peak - pct >= PEAK_MIN_GAP ? peak : null;
}
