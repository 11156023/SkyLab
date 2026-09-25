/**
 * 瀏覽器端錯誤追蹤（Sentry）。
 *
 * 只有建置時帶了 VITE_SENTRY_DSN 才會載入 SDK；SDK 以動態 import 切成獨立
 * chunk，沒設定 DSN 的部署完全不下載。init 之後 SDK 會自己掛 window.onerror
 * 與 unhandledrejection；React render 錯誤由 ErrorBoundary 呼叫 reportError。
 */

const DSN = import.meta.env.VITE_SENTRY_DSN;

let sentryPromise = null;

export function isSentryEnabled() {
  return Boolean(DSN);
}

/** 初始化（重複呼叫只會載入一次）；未設定 DSN 時回 null */
export function initSentry() {
  if (!DSN) return null;
  if (!sentryPromise) {
    sentryPromise = import("@sentry/react")
      .then((Sentry) => {
        Sentry.init({
          dsn: DSN,
          environment: import.meta.env.VITE_SENTRY_ENVIRONMENT || import.meta.env.MODE,
          release: import.meta.env.VITE_SENTRY_RELEASE || undefined,
          // 不送 IP、cookie 等個資；使用者身分也不附上
          sendDefaultPii: false,
        });
        return Sentry;
      })
      .catch((err) => {
        // SDK 載入失敗（例如被廣告阻擋外掛擋掉）不影響頁面本身
        console.warn("[sentry] SDK failed to load", err);
        return null;
      });
  }
  return sentryPromise;
}

/** 回報一個已攔截的錯誤；未啟用時不做任何事 */
export function reportError(error, { componentStack } = {}) {
  const pending = initSentry();
  if (!pending) return;
  pending.then((Sentry) => {
    if (!Sentry) return;
    if (componentStack) {
      Sentry.captureReactException(error, { componentStack });
    } else {
      Sentry.captureException(error);
    }
  });
}
