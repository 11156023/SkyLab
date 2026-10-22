// @vitest-environment happy-dom
/*
 * 回歸重現：WireGuard 分頁讀不到摘要（Gateway 連不上或 wg 檢查失敗）時，每次切回視窗都會再跳一次錯誤 toast。
 * 舊版（ce7675b9^ GatewayPage.jsx:478-494）摘要只在進分頁、按重試與服務操作後抓，失敗才提示；切回視窗不重抓。
 * 新版（GatewayPage.jsx:488-503）摘要 query 沿用全站 refetchOnWindowFocus，每次失敗的 errorUpdatedAt 都不同，
 * effect 每次切回視窗都再提示一次。
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { focusManager } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getConfig: vi.fn(),
  getServiceStatus: vi.fn(),
  getServiceLogs: vi.fn(),
  getWireGuardOverview: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/gateway", () => ({
  GatewayService: {
    getConfig: mocks.getConfig,
    getServiceStatus: mocks.getServiceStatus,
    readServiceConfig: vi.fn(),
    writeServiceConfig: vi.fn(),
    getServiceLogs: mocks.getServiceLogs,
    getWireGuardOverview: mocks.getWireGuardOverview,
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
vi.mock("./ConfigCodeEditor", () => ({ default: () => null }));

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
  mocks.getServiceStatus.mockResolvedValue({ active: true, status_text: "active (running)" });
  mocks.getServiceLogs.mockResolvedValue("log line");
  mocks.getWireGuardOverview.mockRejectedValue(new Error("wg overview failed"));
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

test("switching back to the window does not toast the same WireGuard overview failure again", async () => {
  await act(async () => { root.render(withQueryClient(<GatewayPage />, client)); });
  await flush();
  await act(async () => {
    [...host.querySelectorAll("button")].find((b) => b.textContent === "GatewayPage.tabWireGuard").click();
  });
  await flush();
  expect(mocks.toast.error).toHaveBeenCalledTimes(1);

  /* 切到別的視窗再切回來（兩次） */
  for (let i = 0; i < 2; i += 1) {
    act(() => { focusManager.setFocused(false); });
    act(() => { focusManager.setFocused(true); });
    await flush();
  }

  /* 修正後與舊版相同：切回視窗不重抓（這支只在進分頁、操作後與按重新載入時抓） */
  expect(mocks.getWireGuardOverview).toHaveBeenCalledTimes(1);
  /* 舊版：只有進分頁那次提示；新版：每次切回視窗都再跳 */
  expect(mocks.toast.error).toHaveBeenCalledTimes(1);
});
