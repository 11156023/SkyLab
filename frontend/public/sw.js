/* eslint-disable no-restricted-globals */
/**
 * sw.js — SkyLab Web Push Service Worker
 *
 * 只做一件事：接收後端經推播服務（FCM／Mozilla autopush 等）送來的訊息，
 * 在分頁關掉時也能顯示系統通知；點通知時把使用者帶回站內對應位置。
 * 不做離線快取、不攔截 fetch，避免影響開發與部署時的資源更新。
 *
 * 推播 payload（JSON，由後端 web_push_service 產生）：
 *   { title, body, tag, url, kind: "job" | "reminder" | "test", id }
 *
 * 分頁在前景且聚焦時不顯示系統通知（頁面自己會出 toast），改用 postMessage
 * 通知頁面刷新；其餘情況顯示通知。
 */

const OPEN_JOB_MESSAGE = "skylab:open-job";
const NAVIGATE_MESSAGE = "skylab:navigate";
const PUSH_RECEIVED_MESSAGE = "skylab:push-received";

self.addEventListener("install", () => {
  // 新版本立刻接手，不等舊的 SW 釋放
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

function parsePayload(event) {
  if (!event.data) return null;
  try {
    return event.data.json();
  } catch {
    const text = event.data.text();
    return text ? { title: text } : null;
  }
}

async function hasFocusedClient() {
  const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  return clients.some((c) => c.visibilityState === "visible" && c.focused);
}

async function broadcast(message) {
  const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  for (const client of clients) client.postMessage(message);
}

self.addEventListener("push", (event) => {
  const payload = parsePayload(event);
  if (!payload) return;

  event.waitUntil(
    (async () => {
      await broadcast({ type: PUSH_RECEIVED_MESSAGE, payload });
      // 使用者正看著頁面：頁面的 toast 已經足夠，不重複跳系統通知
      if (payload.kind !== "test" && (await hasFocusedClient())) return;
      await self.registration.showNotification(payload.title || "SkyLab", {
        body: payload.body || "",
        tag: payload.tag || undefined,
        icon: "/favicon.png",
        badge: "/favicon.png",
        data: { url: payload.url || "/", kind: payload.kind, id: payload.id },
        // 同 tag 的通知會互相取代；renotify 讓取代時仍提示一次
        renotify: Boolean(payload.tag),
      });
    })(),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  const url = data.url || "/";

  event.waitUntil(
    (async () => {
      const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      // 已有站內分頁：聚焦並請頁面自己開詳情／跳轉（SPA 內導航，不重新載入）
      const existing = clients.find((c) => "focus" in c);
      if (existing) {
        await existing.focus();
        if (data.kind === "job" && data.id) {
          existing.postMessage({ type: OPEN_JOB_MESSAGE, jobId: data.id, url });
        } else {
          existing.postMessage({ type: NAVIGATE_MESSAGE, url });
        }
        return;
      }
      // 沒有任何分頁：開新視窗到目標路徑（任務會帶 ?job= 讓頁面開詳情）
      await self.clients.openWindow(url);
    })(),
  );
});
