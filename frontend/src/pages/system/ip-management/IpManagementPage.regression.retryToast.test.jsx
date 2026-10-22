// @vitest-environment happy-dom
/*
 * 回歸重現：IP 配發清單第一次就抓不到時，之後每次背景重試（30 秒輪詢／切回視窗）失敗都會再跳一次錯誤 toast，
 * 重試進行中整頁也閃回全頁載入中。
 * 舊版（ce7675b9^ IpManagementPage.jsx:91-112）背景刷新是 load(true)：不動 loading、不提示。
 * 新版（IpManagementPage.jsx:176-187）以 `data === undefined && isError` 觸發 toast、以 isPending／isFetching
 * 算 loading；沒有資料時重抓會把 status 改回 pending，兩者每輪都會再觸發一次。
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "../../../queries/testing";
import { ipManagementKeys } from "../../../queries/ipManagement";

const mocks = vi.hoisted(() => ({
  listAllocations: vi.fn(),
  getSubnet: vi.fn(),
  getStatus: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/ipManagement", () => ({
  IpManagementService: {
    listAllocations: mocks.listAllocations,
    getSubnet: mocks.getSubnet,
    getStatus: mocks.getStatus,
  },
}));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { id: "me", role: "admin", is_superuser: true } }) }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => vi.fn() }));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: () => null }));
vi.mock("../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));

const { default: IpManagementPage } = await import("./IpManagementPage");

let root;
let host;
let client;

/* TanStack Query 以 macrotask 批次通知：多等幾個 timer tick，確保重試中的 pending 狀態已經 render */
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
  mocks.getSubnet.mockResolvedValue(null);
  mocks.getStatus.mockResolvedValue({ configured: false });
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  client = createTestQueryClient();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  client.clear();
});

test("a slow background retry of a never-loaded allocation list stays quiet and does not flash the full-page loader", async () => {
  let rejectRetry;
  mocks.listAllocations
    .mockRejectedValueOnce(new Error("alloc failed"))
    .mockImplementationOnce(() => new Promise((_, reject) => { rejectRetry = reject; }));

  await act(async () => { root.render(withQueryClient(<IpManagementPage />, client)); });
  await flush();
  expect(mocks.toast.error).toHaveBeenCalledTimes(1);
  expect(host.querySelector('[data-testid="loading"]')).toBeNull();

  /* 一次背景刷新（原本 useAutoRefresh 的 tick，舊版是 load(true)：不轉圈、不提示） */
  let refresh;
  act(() => { refresh = client.refetchQueries({ queryKey: ipManagementKeys.allocations }); });
  await flush();
  const flashedLoader = host.querySelector('[data-testid="loading"]') !== null;

  await act(async () => { rejectRetry(new Error("alloc failed")); await refresh; });
  await flush();

  expect(mocks.listAllocations).toHaveBeenCalledTimes(2);
  expect({ flashedLoader, toasts: mocks.toast.error.mock.calls.length }).toEqual({ flashedLoader: false, toasts: 1 });
});
