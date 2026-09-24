/**
 * fetchWithTimeout.js
 * 帶逾時的 fetch 包裝。獨立成一支是為了讓 api.js 與 auth.js 都能用：
 * auth.js 的登入端點刻意不走 api.js 的 request()（登入失敗的 401 不該觸發
 * refresh 重試），但一樣需要逾時保護，兩邊互相 import 會形成循環相依。
 *
 * 逾時丟 { status: 408, timeout: true }，呼叫端取消丟 { status: 0, cancelled: true }，
 * 其餘錯誤（例如斷網）原樣往外拋。
 */

/** 一般請求的預設逾時 */
export const DEFAULT_REQUEST_TIMEOUT_MS = 15_000;
/** 檔案下載／匯出：後端要查完資料再串流，15 秒不夠，放寬到 120 秒 */
export const BLOB_REQUEST_TIMEOUT_MS = 120_000;
/** 登入相關請求：使用者正盯著畫面等，逾時要比一般請求晚一點但仍要有上限 */
export const LOGIN_REQUEST_TIMEOUT_MS = 20_000;

export async function fetchWithTimeout(url, init, timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS) {
  const controller = new AbortController();
  const upstreamSignal = init.signal;
  let timedOut = false;

  const abortFromUpstream = () => controller.abort(upstreamSignal?.reason);
  if (upstreamSignal?.aborted) abortFromUpstream();
  else upstreamSignal?.addEventListener("abort", abortFromUpstream, { once: true });

  const timeoutId = timeoutMs > 0
    ? setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, timeoutMs)
    : null;

  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } catch (error) {
    if (controller.signal.aborted) {
      if (timedOut) {
        throw { status: 408, message: "Request timed out", timeout: true };
      }
      throw { status: 0, message: "Request cancelled", cancelled: true };
    }
    throw error;
  } finally {
    if (timeoutId !== null) clearTimeout(timeoutId);
    upstreamSignal?.removeEventListener("abort", abortFromUpstream);
  }
}
