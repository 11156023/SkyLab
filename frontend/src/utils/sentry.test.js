/**
 * sentry.test.js
 * 驗證前端 Sentry 只有在建置時帶 VITE_SENTRY_DSN 才載入，並把 React 錯誤帶 componentStack 回報。
 */

import { afterEach, describe, expect, test, vi } from "vitest";

const sentryMock = {
  init: vi.fn(),
  captureException: vi.fn(),
  captureReactException: vi.fn(),
};

vi.mock("@sentry/react", () => sentryMock);

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
  vi.clearAllMocks();
});

describe("utils/sentry", () => {
  test("沒有 DSN 時不載入 SDK、回報是 no-op", async () => {
    vi.stubEnv("VITE_SENTRY_DSN", "");
    const { initSentry, isSentryEnabled, reportError } = await import("./sentry");

    expect(isSentryEnabled()).toBe(false);
    expect(initSentry()).toBeNull();
    reportError(new Error("ignored"));
    await Promise.resolve();
    expect(sentryMock.init).not.toHaveBeenCalled();
    expect(sentryMock.captureException).not.toHaveBeenCalled();
  });

  test("有 DSN 時只初始化一次，並依是否有 componentStack 選擇回報方式", async () => {
    vi.stubEnv("VITE_SENTRY_DSN", "https://public@example.ingest.sentry.io/1");
    vi.stubEnv("VITE_SENTRY_ENVIRONMENT", "staging");
    const { initSentry, reportError } = await import("./sentry");

    await initSentry();
    await initSentry();
    expect(sentryMock.init).toHaveBeenCalledTimes(1);
    expect(sentryMock.init.mock.calls[0][0]).toMatchObject({
      dsn: "https://public@example.ingest.sentry.io/1",
      environment: "staging",
      sendDefaultPii: false,
    });

    const plain = new Error("plain");
    reportError(plain);
    const renderError = new Error("render");
    reportError(renderError, { componentStack: "\n    at Broken" });
    await initSentry();
    await Promise.resolve();

    expect(sentryMock.captureException).toHaveBeenCalledWith(plain);
    expect(sentryMock.captureReactException).toHaveBeenCalledWith(renderError, {
      componentStack: "\n    at Broken",
    });
  });
});
