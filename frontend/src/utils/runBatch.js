/**
 * 批量操作逐筆呼叫既有的單筆 API：同時最多 concurrency 筆，一筆失敗不影響其他筆。
 * 給沒有後端批量 API 的清單用（審核駁回、停用帳號、移除學生、封存班級…）。
 *
 * @param {Array} items 要處理的項目
 * @param {(item) => Promise} task 處理單筆；丟出例外＝這筆失敗
 * @param {{ concurrency?: number, onProgress?: ({ done, total }) => void }} options
 * @returns {Promise<{ total, succeeded, failed, results: Array<{ item, ok, value?, error? }> }>}
 *          results 依 items 原本的順序
 */
export async function runBatch(items, task, { concurrency = 4, onProgress } = {}) {
  const results = new Array(items.length);
  let next = 0;
  let done = 0;

  async function worker() {
    while (next < items.length) {
      const index = next;
      next += 1;
      const item = items[index];
      try {
        results[index] = { item, ok: true, value: await task(item) };
      } catch (error) {
        results[index] = { item, ok: false, error };
      }
      done += 1;
      onProgress?.({ done, total: items.length });
    }
  }

  const workers = Math.max(1, Math.min(concurrency, items.length));
  await Promise.all(Array.from({ length: workers }, worker));
  const failed = results.filter((result) => !result.ok).length;
  return { total: items.length, succeeded: items.length - failed, failed, results };
}
