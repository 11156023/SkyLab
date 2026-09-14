/**
 * webPush.js
 * Web Push 訂閱管理：註冊 Service Worker、向瀏覽器要推播訂閱、把訂閱交給後端。
 *
 * 與 browserNotifications.js 的分工：
 * - browserNotifications：頁面開著時由頁面自己發 Notification（不需後端）。
 * - webPush：分頁關掉後仍能收到通知，靠後端經推播服務送到瀏覽器的 Service Worker。
 * 兩者共用同一個瀏覽器通知權限；推播另外需要 Service Worker（只在 https／localhost 可用）。
 *
 * 所有函式在不支援的環境安全回退（回 null／false），不丟例外。
 */

import { apiGet, apiPost, apiDeleteJson } from "./api";

const SW_URL = "/sw.js";

export function isPushSupported() {
  return (
    typeof window !== "undefined" &&
    typeof navigator !== "undefined" &&
    "serviceWorker" in navigator &&
    typeof window.PushManager === "function" &&
    window.isSecureContext !== false
  );
}

/** 後端 VAPID 公鑰；後端未啟用推播時 enabled=false */
export function fetchVapidPublicKey() {
  return apiGet("/api/v1/push/vapid-public-key");
}

export function saveSubscription(subscription) {
  const json = typeof subscription?.toJSON === "function" ? subscription.toJSON() : subscription;
  return apiPost("/api/v1/push/subscriptions", {
    endpoint: json.endpoint,
    keys: { p256dh: json.keys?.p256dh ?? "", auth: json.keys?.auth ?? "" },
    user_agent: typeof navigator !== "undefined" ? navigator.userAgent : null,
  });
}

export function removeSubscription(endpoint) {
  return apiDeleteJson("/api/v1/push/subscriptions", { endpoint });
}

/** 請後端對目前使用者的所有訂閱發一則測試推播 */
export function sendTestPush() {
  return apiPost("/api/v1/push/test", {});
}

/** VAPID 公鑰是 base64url 字串，PushManager 要 Uint8Array */
export function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
  return out;
}

/** 取得（必要時註冊）Service Worker；不支援時回 null */
export async function getRegistration() {
  if (!isPushSupported()) return null;
  try {
    const existing = await navigator.serviceWorker.getRegistration(SW_URL);
    if (existing) return existing;
    return await navigator.serviceWorker.register(SW_URL, { scope: "/" });
  } catch {
    return null;
  }
}

/** 瀏覽器端目前的推播訂閱；沒有或不支援時回 null */
export async function getCurrentSubscription() {
  const registration = await getRegistration();
  if (!registration) return null;
  try {
    return await registration.pushManager.getSubscription();
  } catch {
    return null;
  }
}

/**
 * 訂閱推播並回報後端。回傳：
 *   "subscribed"   成功
 *   "unsupported"  瀏覽器不支援或非安全來源
 *   "disabled"     後端未啟用推播（沒有 VAPID 金鑰）
 *   "denied"       瀏覽器拒絕訂閱（多半是通知權限被封鎖）
 *   "error"        其他失敗（網路、後端錯誤）
 */
export async function subscribePush() {
  if (!isPushSupported()) return "unsupported";
  let vapid;
  try {
    vapid = await fetchVapidPublicKey();
  } catch {
    return "error";
  }
  if (!vapid?.enabled || !vapid.public_key) return "disabled";

  const registration = await getRegistration();
  if (!registration) return "unsupported";

  let subscription;
  try {
    subscription = await registration.pushManager.getSubscription();
    if (!subscription) {
      subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(vapid.public_key),
      });
    }
  } catch (err) {
    return err?.name === "NotAllowedError" ? "denied" : "error";
  }

  try {
    await saveSubscription(subscription);
  } catch {
    return "error";
  }
  return "subscribed";
}

/** 退訂：先通知後端刪除，再解除瀏覽器端訂閱。任一步失敗都不丟例外。 */
export async function unsubscribePush() {
  const subscription = await getCurrentSubscription();
  if (!subscription) return false;
  try {
    await removeSubscription(subscription.endpoint);
  } catch {
    // 後端刪不掉（離線、已登出）也要解除本機訂閱；後端送不到時會自行清掉
  }
  try {
    await subscription.unsubscribe();
  } catch {
    return false;
  }
  return true;
}

/**
 * 訂閱是否已存在於瀏覽器端（不打後端）。
 * 用於頁面載入時決定：已訂閱 → 背景通知交給推播，頁面不再自己發 Notification。
 */
export async function isPushSubscribed() {
  return Boolean(await getCurrentSubscription());
}
