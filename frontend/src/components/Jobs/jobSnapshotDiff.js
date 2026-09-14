/**
 * jobSnapshotDiff.js
 * 比對 /ws/jobs 前後兩份快照，找出「應該通知使用者」的任務終態轉換。
 *
 * 兩種情況會被視為轉換：
 * 1. 上一份快照見過這個任務，且狀態從非終態變成終態（running → completed 之類）。
 * 2. 上一份快照沒見過，但它已是終態、且完成時間晚於上一份快照的高水位
 *    （所有任務 updated_at／completed_at 的最大值）。這是為了補上「任務在
 *    兩次推送之間就建立並完成」的情形：範本轉換、刪除等常在 3 秒內結束，
 *    只看狀態 diff 會永遠看不到它跑過。
 *
 * 高水位只用伺服器端時間戳互相比較，不碰瀏覽器時鐘，避免時鐘偏移誤判。
 * 純函式、無 DOM 依賴，方便單元測試。
 */

export const TERMINAL_STATUSES = new Set(["completed", "failed", "blocked", "cancelled"]);

function parseTime(value) {
  if (!value) return 0;
  const t = Date.parse(value);
  return Number.isNaN(t) ? 0 : t;
}

/** 任務最後一次變動的伺服器時間（ms）；缺欄位時回 0 */
export function jobTouchedAt(job) {
  return Math.max(parseTime(job?.updated_at), parseTime(job?.completed_at));
}

/**
 * @param {Array<{id:string,status:string,updated_at?:string,completed_at?:string}>} items 本次快照
 * @param {{statusMap: Map<string,string>, highWater: number} | null} baseline 上一份快照；null 表示第一次
 * @returns {{ transitions: Array<object>, baseline: {statusMap: Map<string,string>, highWater: number} }}
 *   transitions：需要通知的任務（依快照順序）；baseline：給下一次比對用
 */
export function diffJobSnapshot(items, baseline) {
  const list = Array.isArray(items) ? items : [];
  const statusMap = new Map();
  let highWater = baseline?.highWater ?? 0;
  const transitions = [];

  for (const job of list) {
    statusMap.set(job.id, job.status);
    highWater = Math.max(highWater, jobTouchedAt(job));
    // 第一次連線只建立基準，不通知（避免重整時把歷史任務全轟一遍）
    if (baseline === null || baseline === undefined) continue;
    if (!TERMINAL_STATUSES.has(job.status)) continue;

    const prevStatus = baseline.statusMap.get(job.id);
    if (prevStatus !== undefined) {
      if (prevStatus !== job.status) transitions.push(job);
      continue;
    }
    // 沒見過且已是終態：完成時間晚於上一份快照的高水位才算「剛剛結束」
    if (jobTouchedAt(job) > baseline.highWater) transitions.push(job);
  }

  return { transitions, baseline: { statusMap, highWater } };
}
