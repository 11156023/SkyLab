// @vitest-environment happy-dom
/*
 * 回歸重現：Gateway 連不上（服務狀態從沒抓成功）時，nginx 分頁每次切回視窗都會整個換成「載入中」，
 * 直到狀態那支 SSH 請求逾時才回來（編輯器、狀態卡、日誌整塊被卸載）。
 * 舊版（ce7675b9^ GatewayPage.jsx:340-366）狀態只在進分頁與服務操作後抓，切回視窗不重抓、也不動 loading。
 * 新版（GatewayPage.jsx:339-346）狀態 query 沿用全站 refetchOnWindowFocus，loading 直接看 statusQuery.isPending，
 * 沒有資料時重抓會把 status 改回 pending。
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { focusManager } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getConfig: vi.fn(),
  getServiceStatus: vi.fn(),
  readServiceConfig: vi.fn(),
  getServiceLogs: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/gateway", () => ({
  GatewayService: {
    getConfig: mocks.getConfig,
    getServiceStatus: mocks.getServiceStatus,
    readServiceConfig: mocks.readServiceConfig,
    writeServiceConfig: vi.fn(),
    getServiceLogs: mocks.getServiceLogs,
    getWireGuardOverview: vi.fn(),
    controlService: vi.fn(),
  },
}));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => vi.fn() }));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: ({ children }) => <div>{children}</div> }));
vi.mock("../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));
vi.mock("./ConfigCodeEditor", () => ({
  default: ({ loadFailed }) => <div data-testid="editor" data-failed={String(loadFailed)} />,
}));

const { default: GatewayPage } = await import("./GatewayPage");
const { withQueryClient } = await import("../../../queries/testing");
const { createQueryClient } = await import("../../../queries/queryClient");

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
  mocks.getConfig.mockResolvedValue({ is_configured: true, host: "gw.example.edu", ssh_port: 22, ssh_user: "root" });
  /* 與正式環境相同保留 refetchOnWindowFocus；staleTime 0 省掉等 5 秒 */
  client = createQueryClient({ queries: { retry: false, staleTime: 0, gcTime: 0, refetchInterval: false } });
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

test("returning to the window while the Gateway is unreachable keeps the nginx tab instead of swapping it for a loader", async () => {
  let rejectStatusRetry;
  mocks.getServiceStatus
    .mockRejectedValueOnce(new Error("ssh timeout"))
    .mockImplementationOnce(() => new Promise((_, reject) => { rejectStatusRetry = reject; }));
  mocks.readServiceConfig.mockRejectedValue(new Error("ssh timeout"));
  mocks.getServiceLogs.mockRejectedValue(new Error("ssh timeout"));

  await act(async () => { root.render(withQueryClient(<GatewayPage />, client)); });
  await flush();
  await act(async () => {
    [...host.querySelectorAll("button")].find((b) => b.textContent === "nginx").click();
  });
  await flush();
  expect(host.querySelector('[data-testid="editor"]')?.dataset.failed).toBe("true");

  /* 切到別的視窗再切回來 */
  act(() => { focusManager.setFocused(false); });
  act(() => { focusManager.setFocused(true); });
  await flush();

  /* 修正後與舊版相同：切回視窗不重抓（這支只在進分頁、操作後與按重新載入時抓） */
  expect(mocks.getServiceStatus).toHaveBeenCalledTimes(1);
  /* 舊版：分頁照舊顯示（狀態未知、編輯器讀取失敗）；新版：整個分頁換成載入中 */
  expect(host.querySelector('[data-testid="loading"]')).toBeNull();
  expect(host.querySelector('[data-testid="editor"]')).not.toBeNull();

  await act(async () => { rejectStatusRetry?.(new Error("ssh timeout")); });
  await flush();
});
