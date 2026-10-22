// @vitest-environment happy-dom
/*
 * 回歸重現：挖礦事件第一次就抓不到時（面板顯示空狀態），背景重試（30 秒輪詢／切回視窗）進行中
 * 面板閃回載入中。
 * 舊版（ce7675b9^ MiningIncidentsPanel.jsx:67-78）失敗時 setIncidents((prev) => prev ?? [])，之後的背景重抓
 * 不動畫面；新版 incidents = isError ? EMPTY : null，重抓期間 isError 變回 false → null → LoadingState。
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "../../../queries/testing";
import { miningIncidentKeys } from "../../../queries/miningIncidents";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

vi.mock("../../../services/miningIncidents", () => ({
  MiningIncidentsService: { list: mocks.list, ban: vi.fn(), dismiss: vi.fn() },
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => vi.fn() }));
vi.mock("../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));

const { default: MiningIncidentsPanel } = await import("./MiningIncidentsPanel");

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

test("a background retry after the first load failed keeps the empty state instead of flashing back to loading", async () => {
  let rejectRetry;
  mocks.list
    .mockRejectedValueOnce(new Error("503"))
    .mockImplementationOnce(() => new Promise((_, reject) => { rejectRetry = reject; }));

  await act(async () => root.render(withQueryClient(
    <MemoryRouter><MiningIncidentsPanel /></MemoryRouter>,
    client,
  )));
  await flush();
  expect(host.textContent).toContain("MiningIncidentsPanel.emptyNone");

  let retrying;
  act(() => { retrying = client.refetchQueries({ queryKey: miningIncidentKeys.all }); });
  await flush();

  expect(mocks.list).toHaveBeenCalledTimes(2);
  expect(host.querySelector('[data-testid="loading"]')).toBeNull();
  expect(host.textContent).toContain("MiningIncidentsPanel.emptyNone");

  await act(async () => { rejectRetry(new Error("503")); await retrying; });
  await flush();
});
