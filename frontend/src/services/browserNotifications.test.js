/**
 * browserNotifications.test.js
 * 驗證 Web Notifications 封裝的權限閘門、偏好旗標與點擊行為（不依賴瀏覽器）。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import {
  canNotify,
  dismissPrompt,
  getPermission,
  isPageInBackground,
  isPromptDismissed,
  requestPermission,
  setEnabled,
  showNotification,
} from "./browserNotifications";

function fakeStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
  };
}

/** 可控的 Notification 假物件：記錄建構參數，permission 由測試設定 */
function fakeNotificationClass({ permission = "default", request } = {}) {
  const instances = [];
  class FakeNotification {
    constructor(title, options) {
      this.title = title;
      this.options = options;
      this.close = vi.fn();
      instances.push(this);
    }
  }
  FakeNotification.permission = permission;
  FakeNotification.requestPermission = request ?? vi.fn(() => Promise.resolve("granted"));
  FakeNotification.instances = instances;
  return FakeNotification;
}

let windowMock;

beforeEach(() => {
  vi.stubGlobal("localStorage", fakeStorage());
  windowMock = { focus: vi.fn() };
  vi.stubGlobal("window", windowMock);
  vi.stubGlobal("document", { hidden: false, hasFocus: () => true });
});

describe("不支援 Notification 的環境", () => {
  test("權限回 unsupported、要求權限直接 resolve、不會建立通知", async () => {
    expect(getPermission()).toBe("unsupported");
    await expect(requestPermission()).resolves.toBe("unsupported");
    expect(showNotification("x")).toBeNull();
  });
});

describe("requestPermission", () => {
  test("Promise 簽章：回傳瀏覽器結果", async () => {
    windowMock.Notification = fakeNotificationClass({
      request: vi.fn(() => Promise.resolve("granted")),
    });
    await expect(requestPermission()).resolves.toBe("granted");
  });

  test("舊版 callback 簽章：以 callback 結果 resolve", async () => {
    windowMock.Notification = fakeNotificationClass({
      request: vi.fn((cb) => {
        cb("denied");
      }),
    });
    await expect(requestPermission()).resolves.toBe("denied");
  });
});

describe("不安全來源（http://IP 之類，非 https／localhost）", () => {
  test("權限回 insecure、不向瀏覽器要權限、不建立通知", async () => {
    const request = vi.fn(() => Promise.resolve("granted"));
    windowMock.isSecureContext = false;
    windowMock.Notification = fakeNotificationClass({ permission: "granted", request });
    expect(getPermission()).toBe("insecure");
    await expect(requestPermission()).resolves.toBe("insecure");
    expect(request).not.toHaveBeenCalled();
    expect(canNotify()).toBe(false);
    expect(showNotification("x")).toBeNull();
  });

  test("安全來源不受影響", () => {
    windowMock.isSecureContext = true;
    windowMock.Notification = fakeNotificationClass({ permission: "granted" });
    expect(getPermission()).toBe("granted");
    expect(canNotify()).toBe(true);
  });
});

describe("canNotify 與使用者偏好", () => {
  test("權限 granted 且未關閉偏好才可發", () => {
    windowMock.Notification = fakeNotificationClass({ permission: "granted" });
    expect(canNotify()).toBe(true);
    setEnabled(false);
    expect(canNotify()).toBe(false);
    setEnabled(true);
    expect(canNotify()).toBe(true);
  });

  test("權限 default 或 denied 一律不可發", () => {
    windowMock.Notification = fakeNotificationClass({ permission: "default" });
    expect(canNotify()).toBe(false);
    windowMock.Notification = fakeNotificationClass({ permission: "denied" });
    expect(canNotify()).toBe(false);
  });

  test("詢問提示的忽略旗標會持久化", () => {
    expect(isPromptDismissed()).toBe(false);
    dismissPrompt();
    expect(isPromptDismissed()).toBe(true);
  });
});

describe("showNotification", () => {
  test("建立通知並帶 body / tag / icon；點擊會聚焦視窗、執行 onClick 並關閉", () => {
    const Fake = fakeNotificationClass({ permission: "granted" });
    windowMock.Notification = Fake;
    const onClick = vi.fn();

    const n = showNotification("任務完成", { body: "VM 123", tag: "vm_request:1", onClick });

    expect(n).toBe(Fake.instances[0]);
    expect(n.title).toBe("任務完成");
    expect(n.options).toMatchObject({ body: "VM 123", tag: "vm_request:1", icon: "/favicon.png" });

    const preventDefault = vi.fn();
    n.onclick({ preventDefault });
    expect(preventDefault).toHaveBeenCalled();
    expect(windowMock.focus).toHaveBeenCalled();
    expect(onClick).toHaveBeenCalled();
    expect(n.close).toHaveBeenCalled();
  });

  test("偏好關閉時不建立通知", () => {
    const Fake = fakeNotificationClass({ permission: "granted" });
    windowMock.Notification = Fake;
    setEnabled(false);
    expect(showNotification("x")).toBeNull();
    expect(Fake.instances).toHaveLength(0);
  });
});

describe("isPageInBackground", () => {
  test("分頁隱藏或視窗失焦視為背景", () => {
    expect(isPageInBackground()).toBe(false);
    vi.stubGlobal("document", { hidden: true, hasFocus: () => true });
    expect(isPageInBackground()).toBe(true);
    vi.stubGlobal("document", { hidden: false, hasFocus: () => false });
    expect(isPageInBackground()).toBe(true);
  });
});
