// @vitest-environment happy-dom

/*
 * 回歸：AI 監控頁的總覽（overview）首載失敗後，新版每 30 秒的背景輪詢每重試一次就再跳一次「載入失敗」toast。
 *
 * 新版（pages/ai/ai-monitoring/AiMonitoringPage.jsx）用
 *   overviewFailedWithoutData = overviewQuery.data === undefined && overviewQuery.isError
 * 當 effect 的依賴：沒有資料時 TanStack Query v5 重抓會把 status 改回 pending（isError 變 false），
 * 失敗後又變回 true，effect 就再 toast 一次。
 * 舊版（ce7675b9^）只有非 silent 的 load（進頁、換區間）失敗才 toast，useAutoRefresh 的背景刷新是 load(true)，不提示。
 */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { notifyManager } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { withQueryClient } from "../../../queries/testing";
import AiMonitoringPage from "./AiMonitoringPage";

const mocks = vi.hoisted(() => ({
  overview: vi.fn(),
  runtime: vi.fn(),
  listProxyCalls: vi.fn(),
  listTemplateCalls: vi.fn(),
  listUsersUsage: vi.fn(),
  createGrafanaSession: vi.fn(),
  t: (key) => key,
  toast: { error: vi.fn(), success: vi.fn() },
}));

vi.mock("../../../services/aiMonitoring", () => ({
  AiMonitoringService: {
    overview: mocks.overview,
    runtime: mocks.runtime,
    listProxyCalls: mocks.listProxyCalls,
    listTemplateCalls: mocks.listTemplateCalls,
    listUsersUsage: mocks.listUsersUsage,
  },
}));
vi.mock("../../../services/monitoring", () => ({
  MonitoringService: { createGrafanaSession: mocks.createGrafanaSession },
}));
vi.mock("../../../i18n", () => ({ default: { language: "en" } }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: ({ children }) => <div>{children}</div> }));
vi.mock("../../../components/SegmentedControl/SegmentedControl", () => ({
  default: ({ options = [], value, onChange, ariaLabel }) => (
    <div data-group={ariaLabel}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          data-value={option.value}
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  ),
}));

let root;
let host;

async function advance(ms) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.useFakeTimers();
  notifyManager.setScheduler((cb) => cb());
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  mocks.runtime.mockResolvedValue({ gateway: { status: "available", readiness: true }, models: [], summary: {} });
  mocks.listProxyCalls.mockResolvedValue({ data: [], count: 0 });
  mocks.listTemplateCalls.mockResolvedValue({ data: [], count: 0 });
  mocks.listUsersUsage.mockResolvedValue({ data: [], count: 0 });
  mocks.createGrafanaSession.mockResolvedValue({ enabled: false });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  notifyManager.setScheduler((cb) => setTimeout(cb, 0));
  vi.useRealTimers();
});

describe("AiMonitoringPage：總覽一直載不到", () => {
  test("同一個區間的總覽持續失敗，只在首載提示一次，不會每 30 秒重跳", async () => {
    mocks.overview.mockRejectedValue(new Error("overview unavailable"));

    await act(async () => { root.render(withQueryClient(<AiMonitoringPage />)); });
    await advance(10);
    await advance(10);
    expect(mocks.overview).toHaveBeenCalledTimes(1);
    expect(mocks.toast.error).toHaveBeenCalledTimes(1);

    /* 頁面開著 90 秒（總覽 API 持續失敗，背景輪詢重試三次） */
    for (let i = 0; i < 3; i += 1) {
      await advance(30_005);
      await advance(10);
    }
    expect(mocks.overview.mock.calls.length).toBeGreaterThanOrEqual(4);

    /* 舊版：背景刷新是 load(true)，失敗不跳 toast，整段只有首載那 1 次 */
    expect(mocks.toast.error).toHaveBeenCalledTimes(1);
  });
});
