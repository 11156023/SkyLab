/**
 * responseMessage.js
 * 從失敗的 fetch Response 讀出可直接顯示給使用者的錯誤訊息。
 *
 * api.js 與 auth.js 共用；獨立成檔是為了不讓 auth.js 反向 import api.js（兩者會循環相依）。
 */

/**
 * 依序接受：字串 detail／message、FastAPI 422 的 detail 陣列（取第一筆 msg）、
 * { message } 物件；其餘（含 body 不是 JSON）一律回 `HTTP <status>`。
 * 保證回傳字串，頁面可以直接 toast 或 render。
 */
export async function readResponseMessage(res) {
  let message = `HTTP ${res.status}`;
  try {
    const body = await res.json();
    const rawMessage = body?.detail ?? body?.message;
    if (typeof rawMessage === "string") {
      message = rawMessage;
    } else if (Array.isArray(rawMessage)) {
      if (typeof rawMessage[0]?.msg === "string") message = rawMessage[0].msg;
    } else if (rawMessage && typeof rawMessage.message === "string") {
      message = rawMessage.message;
    }
  } catch {
    // 若 body 不是 JSON 就用預設訊息
  }
  return message;
}
