// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "./testing";
import {
  aiMonitoringKeys,
  resolveMonitoringWindow,
  useAiMonitoringOverviewQuery,
  useAiMonitoringRuntimeQuery,
  useAiProxyCallsQuery,
  useAiTemplateCallsQuery,
  useAiUsersUsageQuery,
} from "./aiMonitoring";

const mocks = vi.hoisted(() => ({
  overview: vi.fn(),
  runtime: vi.fn(),
  listProxyCalls: vi.fn(),
  listTemplateCalls: vi.fn(),
  listUsersUsage: vi.fn(),
}));
vi.mock("../services/aiMonitoring", () => ({ AiMonitoringService: mocks }));

function deferred() {
  let resolve;
  const promise = new Promise((res) => { resolve = res; });
  return { promise, resolve };
}

let host;
let root;
let client;
let seen;

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

function render(ui) {
  return act(async () => { root.render(withQueryClient(ui, client)); });
}

/* useQuery 的結果是存取追蹤的：欄位要在 render 期間讀過，資料變了才會重繪 */
function Probe({ preset }) {
  const overview = useAiMonitoringOverviewQuery({ preset, bucket: "day", compare: true, source: "all" }, { refetchInterval: false });
  const runtime = useAiMonitoringRuntimeQuery({ refetchInterval: false });
  const proxy = useAiProxyCallsQuery({ preset, limit: 100 }, { refetchInterval: false });
  const template = useAiTemplateCallsQuery({ preset, limit: 100 }, { refetchInterval: false });
  const users = useAiUsersUsageQuery({ preset, limit: 100, source: "all" }, { refetchInterval: false });
  seen = {
    overview: overview.data,
    overviewPlaceholder: overview.isPlaceholderData,
    runtime: runtime.data,
    runtimeError: runtime.isError,
    proxy: proxy.data,
    template: template.data,
    users: users.data,
  };
  return null;
}

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  client = createTestQueryClient();
  mocks.runtime.mockResolvedValue({ gateway: { status: "available" } });
  mocks.listProxyCalls.mockResolvedValue({ data: [], count: 0 });
  mocks.listTemplateCalls.mockResolvedValue({ data: [], count: 0 });
  mocks.listUsersUsage.mockResolvedValue({ data: [], count: 0 });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.useRealTimers();
});

describe("resolveMonitoringWindow", () => {
  test("turns a preset into a window ending now and keeps the other params", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-22T12:00:00Z"));
    expect(resolveMonitoringWindow({ preset: "7d", limit: 100 })).toEqual({
      startDate: "2026-10-15T12:00:00.000Z",
      endDate: "2026-10-22T12:00:00.000Z",
      limit: 100,
    });
    expect(resolveMonitoringWindow({ limit: 5 })).toEqual({ limit: 5 });
  });
});

describe("AI monitoring queries", () => {
  test("keys carry the preset while every fetch computes a fresh window", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-22T12:00:00Z"));
    mocks.overview.mockResolvedValue({ summary: { total_calls: 1 } });
    await render(<Probe preset="7d" />);
    await flush();

    expect(mocks.overview).toHaveBeenLastCalledWith({
      startDate: "2026-10-15T12:00:00.000Z",
      endDate: "2026-10-22T12:00:00.000Z",
      bucket: "day",
      compare: true,
      source: "all",
    });
    expect(mocks.listProxyCalls).toHaveBeenLastCalledWith(expect.objectContaining({ limit: 100, endDate: "2026-10-22T12:00:00.000Z" }));
    expect(mocks.listUsersUsage).toHaveBeenLastCalledWith(expect.objectContaining({ source: "all" }));
    expect(client.getQueryData(aiMonitoringKeys.overview({ preset: "7d", bucket: "day", compare: true, source: "all" })))
      .toEqual({ summary: { total_calls: 1 } });

    // 背景刷新跟著現在往前滑：同一個 key、新的起訖
    vi.setSystemTime(new Date("2026-10-22T12:00:30Z"));
    await act(async () => { await client.refetchQueries({ queryKey: aiMonitoringKeys.all }); });
    await flush();
    expect(mocks.overview).toHaveBeenLastCalledWith(expect.objectContaining({ endDate: "2026-10-22T12:00:30.000Z" }));
  });

  test("switching ranges keeps the previous overview until the new one arrives, and runtime is not refetched", async () => {
    const ninety = deferred();
    mocks.overview
      .mockResolvedValueOnce({ summary: { total_calls: 7 } })
      .mockReturnValueOnce(ninety.promise);
    await render(<Probe preset="7d" />);
    await flush();

    await render(<Probe preset="90d" />);
    await flush();
    expect(seen.overviewPlaceholder).toBe(true);
    expect(seen.overview).toEqual({ summary: { total_calls: 7 } });
    expect(mocks.runtime).toHaveBeenCalledTimes(1);
    expect(mocks.listProxyCalls).toHaveBeenCalledTimes(2);

    await act(async () => { ninety.resolve({ summary: { total_calls: 90 } }); });
    await flush();
    expect(seen.overviewPlaceholder).toBe(false);
    expect(seen.overview).toEqual({ summary: { total_calls: 90 } });
  });

  test("a runtime failure is reported without affecting the usage data", async () => {
    mocks.overview.mockResolvedValue({ summary: { total_calls: 3 } });
    mocks.runtime.mockRejectedValue(new Error("gateway down"));
    await render(<Probe preset="30d" />);
    await flush();

    expect(seen.runtimeError).toBe(true);
    expect(seen.runtime).toBeUndefined();
    expect(seen.overview).toEqual({ summary: { total_calls: 3 } });
    expect(seen.proxy).toEqual({ data: [], count: 0 });
  });
});
