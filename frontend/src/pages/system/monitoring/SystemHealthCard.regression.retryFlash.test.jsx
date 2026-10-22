// @vitest-environment happy-dom
/*
 * 回歸重現：平台健康第一次就抓不到時，背景重試（30 秒輪詢／切回視窗）進行中卡片會從
 * 「讀取失敗」閃回「載入中」。
 * 舊版（ce7675b9^ SystemHealthCard.jsx:72-91）error 只在成功時才清掉，背景重抓期間錯誤畫面維持。
 * 新版 error = healthQuery.isError：沒有資料時 TanStack Query v5 每次重抓都把 status 改回 pending。
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "../../../queries/testing";
import { monitoringKeys } from "../../../queries/monitoring";

const mocks = vi.hoisted(() => ({
  getSystemHealth: vi.fn(),
  t: (key) => key,
}));

vi.mock("../../../services/monitoring", () => ({
  MonitoringService: { getSystemHealth: mocks.getSystemHealth },
}));
vi.mock("../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));

const { default: SystemHealthCard } = await import("./SystemHealthCard");

let host;
let root;
let client;

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  client = createTestQueryClient();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  client.clear();
});

/* TanStack Query 以 macrotask 批次通知：多等幾個 timer tick，確保重試中的 pending 狀態已經 render */
async function flush() {
  for (let round = 0; round < 3; round += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
      for (let i = 0; i < 5; i += 1) await Promise.resolve();
    });
  }
}

test("a background retry after the first load failed keeps the error message instead of flashing back to loading", async () => {
  let rejectRetry;
  mocks.getSystemHealth
    .mockRejectedValueOnce(new Error("503"))
    .mockImplementationOnce(() => new Promise((_, reject) => { rejectRetry = reject; }));

  await act(async () => root.render(withQueryClient(<SystemHealthCard />, client)));
  await flush();
  expect(host.textContent).toContain("SystemHealth.errorFetch");

  /* 一次背景輪詢（原本 useAutoRefresh 的 tick） */
  let retrying;
  act(() => { retrying = client.refetchQueries({ queryKey: monitoringKeys.systemHealth }); });
  await flush();

  expect(mocks.getSystemHealth).toHaveBeenCalledTimes(2);
  expect(host.querySelector('[data-testid="loading"]')).toBeNull();
  expect(host.textContent).toContain("SystemHealth.errorFetch");

  await act(async () => { rejectRetry(new Error("503")); await retrying; });
  await flush();
});
