/* 課程票券的條碼就是練習進度：一段代表一題（題目多時一段代表數題），做完的排在前面。
   條寬與間距由課程 id 決定，同一門課每次都畫出同一組條碼，不同課程一眼分得出來。 */

export const MAX_BARS = 40;

/* 題目少時（例如 3 題）一題一條只剩幾塊粗色塊、不像條碼：
   改成每題畫一小叢細條、叢與叢之間留較寬的空隙，整張條碼至少這麼多條，仍看得出分成幾題 */
export const MIN_BARS = 24;

const WIDTHS = [1, 1, 2, 3];
const GROUP_GAP = 4;

/* FNV-1a 雜湊當種子，xorshift32 產生 0–1 的序列 */
function seeded(text) {
  let hash = 2166136261;
  for (const char of text) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619);
  return () => {
    hash ^= hash << 13;
    hash ^= hash >>> 17;
    hash ^= hash << 5;
    return (hash >>> 0) / 4294967296;
  };
}

/**
 * @returns {{ width: number, gap: number, done: boolean }[]} 相對寬度（flex-grow）與是否已完成；沒有題目時回空陣列
 */
export function ticketBarcode(id, total, completed) {
  const questions = Math.max(0, Math.floor(Number(total) || 0));
  const segments = Math.min(questions, MAX_BARS);
  if (segments === 0) return [];

  const finished = Math.min(Math.max(0, Number(completed) || 0), questions);
  let done = Math.round((finished / questions) * segments);
  if (finished > 0) done = Math.max(1, done); // 做了一題就看得到
  if (finished < questions) done = Math.min(segments - 1, done); // 沒做完就不會看起來全滿

  const perSegment = Math.ceil(MIN_BARS / segments);
  const random = seeded(String(id ?? ""));
  const bars = [];
  for (let segment = 0; segment < segments; segment += 1) {
    for (let index = 0; index < perSegment; index += 1) {
      const endOfSegment = index === perSegment - 1;
      const last = endOfSegment && segment === segments - 1;
      const width = WIDTHS[Math.floor(random() * WIDTHS.length)];
      let gap = 0;
      if (!last) gap = endOfSegment && perSegment > 1 ? GROUP_GAP + Math.floor(random() * 2) : 1 + Math.floor(random() * 2);
      bars.push({ width, gap, done: segment < done });
    }
  }
  return bars;
}
