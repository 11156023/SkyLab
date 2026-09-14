/**
 * browserNotifications.js
 * Web Notifications API 封裝（Windows／macOS 的系統通知）。
 *
 * 兩層閘門：瀏覽器權限（Notification.permission，由瀏覽器管）與使用者偏好
 * （localStorage，預設開；關掉後即使權限已授予也不發）。
 * 所有函式在不支援的環境（SSR、舊瀏覽器、測試）都安全回退，不丟例外。
 */

const ENABLED_KEY = "jobs:desktopNotifications";
const PROMPT_DISMISSED_KEY = "jobs:desktopNotificationsPromptDismissed";
const ICON_URL = "/favicon.png";

export function isSupported() {
  return typeof window !== "undefined" && typeof window.Notification === "function";
}

/**
 * 是否為安全來源（https 或 localhost）。瀏覽器在不安全來源上不會詢問通知權限，
 * Chrome 會直接把 permission 回成 "denied"；用 http://IP 存取的部署會踩到這點。
 */
export function isSecureOrigin() {
  if (typeof window === "undefined") return false;
  return window.isSecureContext !== false;
}

/** "default" | "granted" | "denied" | "insecure" | "unsupported" */
export function getPermission() {
  if (!isSupported()) return "unsupported";
  if (!isSecureOrigin()) return "insecure";
  return window.Notification.permission;
}

function readFlag(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeFlag(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // localStorage 不可用時該設定僅本次瀏覽生效
  }
}

/** 使用者偏好：未設定視為開（真正的閘門是瀏覽器權限） */
export function isEnabled() {
  return readFlag(ENABLED_KEY) !== "0";
}

export function setEnabled(next) {
  writeFlag(ENABLED_KEY, next ? "1" : "0");
}

export function isPromptDismissed() {
  return readFlag(PROMPT_DISMISSED_KEY) === "1";
}

export function dismissPrompt() {
  writeFlag(PROMPT_DISMISSED_KEY, "1");
}

/**
 * 向瀏覽器要權限。必須在使用者手勢（點擊）內呼叫，否則 Chrome 會靜默忽略。
 * 同時相容 Promise 與舊版 Safari 的 callback 簽章。
 */
export function requestPermission() {
  if (!isSupported()) return Promise.resolve("unsupported");
  if (!isSecureOrigin()) return Promise.resolve("insecure");
  return new Promise((resolve) => {
    let settled = false;
    const done = (value) => {
      if (settled) return;
      settled = true;
      resolve(value ?? getPermission());
    };
    try {
      const maybePromise = window.Notification.requestPermission(done);
      if (maybePromise && typeof maybePromise.then === "function") {
        maybePromise.then(done, () => done(getPermission()));
      }
    } catch {
      done(getPermission());
    }
  });
}

export function canNotify() {
  return getPermission() === "granted" && isEnabled();
}

/** 使用者是否不在這個分頁／視窗（切到別的分頁、最小化、或其他應用程式在前景） */
export function isPageInBackground() {
  if (typeof document === "undefined") return false;
  if (document.hidden) return true;
  return typeof document.hasFocus === "function" ? !document.hasFocus() : false;
}

/**
 * 發一則系統通知；權限未授予或使用者關閉偏好時回 null。
 * 點擊通知會把視窗拉回前景並執行 onClick。同 tag 的通知會互相取代。
 */
export function showNotification(title, { body, tag, onClick } = {}) {
  if (!canNotify()) return null;
  let notification;
  try {
    notification = new window.Notification(title, { body, tag, icon: ICON_URL });
  } catch {
    return null;
  }
  notification.onclick = (event) => {
    event?.preventDefault?.();
    try {
      window.focus();
    } catch {
      // 部分瀏覽器不允許腳本聚焦視窗
    }
    onClick?.();
    notification.close();
  };
  return notification;
}
