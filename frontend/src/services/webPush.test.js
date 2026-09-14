/**
 * webPush.test.js
 * 驗證推播訂閱流程：支援判斷、金鑰轉換、訂閱／退訂與後端回報（不依賴瀏覽器）。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("./api", () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiDeleteJson: vi.fn(),
}));

import { apiDeleteJson, apiGet, apiPost } from "./api";
import {
  isPushSubscribed,
  isPushSupported,
  subscribePush,
  unsubscribePush,
  urlBase64ToUint8Array,
} from "./webPush";

/** 可控的 ServiceWorkerRegistration 假物件 */
function fakeRegistration({ subscription = null, subscribeError = null } = {}) {
  const sub = subscription;
  const registration = {
    pushManager: {
      getSubscription: vi.fn(async () => sub),
      subscribe: vi.fn(async () => {
        if (subscribeError) throw subscribeError;
        return {
          endpoint: "https://push.example/abc",
          toJSON: () => ({ endpoint: "https://push.example/abc", keys: { p256dh: "P", auth: "A" } }),
          unsubscribe: vi.fn(async () => true),
        };
      }),
    },
  };
  return registration;
}

let registration;
let windowMock;

beforeEach(() => {
  vi.clearAllMocks();
  registration = fakeRegistration();
  windowMock = { PushManager: function PushManager() {}, isSecureContext: true };
  vi.stubGlobal("window", windowMock);
  vi.stubGlobal("navigator", {
    userAgent: "vitest",
    serviceWorker: {
      getRegistration: vi.fn(async () => registration),
      register: vi.fn(async () => registration),
    },
  });
  vi.stubGlobal("atob", (s) => Buffer.from(s, "base64").toString("binary"));
});

describe("isPushSupported", () => {
  test("缺 serviceWorker、缺 PushManager 或非安全來源都視為不支援", () => {
    expect(isPushSupported()).toBe(true);
    vi.stubGlobal("navigator", {});
    expect(isPushSupported()).toBe(false);
    vi.stubGlobal("navigator", { serviceWorker: {} });
    windowMock.PushManager = undefined;
    expect(isPushSupported()).toBe(false);
    windowMock.PushManager = function PushManager() {};
    windowMock.isSecureContext = false;
    expect(isPushSupported()).toBe(false);
  });
});

describe("urlBase64ToUint8Array", () => {
  test("base64url 轉成位元組並補齊 padding", () => {
    // "hi" 的 base64 是 aGk=，base64url 去掉 padding 後為 aGk
    expect(Array.from(urlBase64ToUint8Array("aGk"))).toEqual([104, 105]);
    // '-' 與 '_' 要換回 '+' 與 '/'
    expect(Array.from(urlBase64ToUint8Array("-_8"))).toEqual([251, 255]);
  });
});

describe("subscribePush", () => {
  test("後端未啟用推播（沒有 VAPID 金鑰）→ disabled，不建立訂閱", async () => {
    apiGet.mockResolvedValueOnce({ enabled: false, public_key: null });
    await expect(subscribePush()).resolves.toBe("disabled");
    expect(registration.pushManager.subscribe).not.toHaveBeenCalled();
    expect(apiPost).not.toHaveBeenCalled();
  });

  test("成功：以 VAPID 公鑰訂閱並把 endpoint／keys 回報後端", async () => {
    apiGet.mockResolvedValueOnce({ enabled: true, public_key: "aGk" });
    apiPost.mockResolvedValueOnce({ id: "x" });
    await expect(subscribePush()).resolves.toBe("subscribed");
    const [[opts]] = registration.pushManager.subscribe.mock.calls;
    expect(opts.userVisibleOnly).toBe(true);
    expect(Array.from(opts.applicationServerKey)).toEqual([104, 105]);
    expect(apiPost).toHaveBeenCalledWith("/api/v1/push/subscriptions", {
      endpoint: "https://push.example/abc",
      keys: { p256dh: "P", auth: "A" },
      user_agent: "vitest",
    });
  });

  test("瀏覽器已有訂閱時直接沿用，不重新 subscribe", async () => {
    const existing = {
      endpoint: "https://push.example/old",
      toJSON: () => ({ endpoint: "https://push.example/old", keys: { p256dh: "p", auth: "a" } }),
    };
    registration = fakeRegistration({ subscription: existing });
    apiGet.mockResolvedValueOnce({ enabled: true, public_key: "aGk" });
    apiPost.mockResolvedValueOnce({});
    await expect(subscribePush()).resolves.toBe("subscribed");
    expect(registration.pushManager.subscribe).not.toHaveBeenCalled();
    expect(apiPost.mock.calls[0][1].endpoint).toBe("https://push.example/old");
  });

  test("瀏覽器拒絕（NotAllowedError）→ denied；其他例外 → error", async () => {
    const notAllowed = new Error("blocked");
    notAllowed.name = "NotAllowedError";
    registration = fakeRegistration({ subscribeError: notAllowed });
    apiGet.mockResolvedValueOnce({ enabled: true, public_key: "aGk" });
    await expect(subscribePush()).resolves.toBe("denied");

    registration = fakeRegistration({ subscribeError: new Error("boom") });
    apiGet.mockResolvedValueOnce({ enabled: true, public_key: "aGk" });
    await expect(subscribePush()).resolves.toBe("error");
  });

  test("不支援的環境 → unsupported，不打後端", async () => {
    vi.stubGlobal("navigator", {});
    await expect(subscribePush()).resolves.toBe("unsupported");
    expect(apiGet).not.toHaveBeenCalled();
  });
});

describe("unsubscribePush / isPushSubscribed", () => {
  test("有訂閱：先請後端刪除，再解除瀏覽器端訂閱", async () => {
    const unsubscribe = vi.fn(async () => true);
    registration = fakeRegistration({
      subscription: { endpoint: "https://push.example/abc", unsubscribe },
    });
    apiDeleteJson.mockResolvedValueOnce(undefined);
    expect(await isPushSubscribed()).toBe(true);
    await expect(unsubscribePush()).resolves.toBe(true);
    expect(apiDeleteJson).toHaveBeenCalledWith("/api/v1/push/subscriptions", {
      endpoint: "https://push.example/abc",
    });
    expect(unsubscribe).toHaveBeenCalled();
  });

  test("後端刪除失敗仍解除本機訂閱", async () => {
    const unsubscribe = vi.fn(async () => true);
    registration = fakeRegistration({
      subscription: { endpoint: "https://push.example/abc", unsubscribe },
    });
    apiDeleteJson.mockRejectedValueOnce(new Error("offline"));
    await expect(unsubscribePush()).resolves.toBe(true);
    expect(unsubscribe).toHaveBeenCalled();
  });

  test("沒有訂閱 → false", async () => {
    expect(await isPushSubscribed()).toBe(false);
    await expect(unsubscribePush()).resolves.toBe(false);
    expect(apiDeleteJson).not.toHaveBeenCalled();
  });
});
