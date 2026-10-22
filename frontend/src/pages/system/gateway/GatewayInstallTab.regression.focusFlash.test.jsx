// @vitest-environment happy-dom
/*
 * 回歸重現：一鍵安裝分頁讀不到安裝狀態（Gateway SSH 連不上）時顯示錯誤卡；每次切回視窗都會把錯誤卡換成
 * 「載入中」直到那次 SSH 請求逾時。
 * 舊版（ce7675b9^ GatewayInstallTab.jsx:131-134）useAutoRefresh 的回呼只在 state === "running" 時才重抓，
 * 切回視窗不會重抓、錯誤卡維持；新版安裝狀態 query 沿用全站 refetchOnWindowFocus，
 * 沒有資料時重抓把 status 改回 pending，loading = status === null && isPending。
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { focusManager } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import GatewayInstallTab from "./GatewayInstallTab";
import { createQueryClient } from "../../../queries/queryClient";
import { withQueryClient } from "../../../queries/testing";

const mocks = vi.hoisted(() => ({
  getInstallStatus: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/gateway", () => ({
  GatewayService: { getInstallStatus: mocks.getInstallStatus, startInstall: vi.fn() },
}));
vi.mock("../../../services/reverseProxy", () => ({ ReverseProxyService: { syncRules: vi.fn() } }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => vi.fn() }));
vi.mock("../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));

let root;
let host;
let client;

async function flush() {
  for (let round = 0; round < 3; round += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
      for (let i = 0; i < 5; i += 1) await Promise.resolve();
    });
  }
}

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  /* 與正式環境相同保留 refetchOnWindowFocus；staleTime 0 省掉等 5 秒 */
  client = createQueryClient({ queries: { retry: false, staleTime: 0, gcTime: 0 } });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  client.clear();
  focusManager.setFocused(undefined);
});

test("returning to the window while the install status is unreadable keeps the error card", async () => {
  let rejectRetry;
  mocks.getInstallStatus
    .mockRejectedValueOnce(new Error("ssh timeout"))
    .mockImplementationOnce(() => new Promise((_, reject) => { rejectRetry = reject; }));

  await act(async () => { root.render(withQueryClient(<GatewayInstallTab gatewayReady onGoToConnection={() => {}} />, client)); });
  await flush();
  expect(host.textContent).toContain("GatewayPage.installConnectErrorTitle");

  act(() => { focusManager.setFocused(false); });
  act(() => { focusManager.setFocused(true); });
  await flush();

  /* 修正後與舊版相同：切回視窗不重抓（這支只在進分頁、操作後與按重新載入時抓） */
  expect(mocks.getInstallStatus).toHaveBeenCalledTimes(1);
  /* 舊版：錯誤卡維持（不是安裝中就不重抓）；新版：換成載入中 */
  expect(host.querySelector('[data-testid="loading"]')).toBeNull();
  expect(host.textContent).toContain("GatewayPage.installConnectErrorTitle");

  await act(async () => { rejectRetry?.(new Error("ssh timeout")); });
  await flush();
});
