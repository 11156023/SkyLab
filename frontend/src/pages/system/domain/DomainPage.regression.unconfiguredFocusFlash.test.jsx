// @vitest-environment happy-dom
/*
 * 回歸重現：還沒設定 Cloudflare 時（zone 清單回 400），網域管理頁每次切回視窗都會從「尚未連線」空狀態
 * 跳成 zone 工作區＋轉圈，請求回來後再跳回空狀態（整頁版面閃一次）。
 * 舊版（ce7675b9^ DomainPage.jsx:312-325、349-352）zone 清單只在進頁與存設定後抓，切回視窗不重抓。
 * 新版 zones query 沿用全站 refetchOnWindowFocus，沒有資料時重抓把 status 改回 pending，
 * loadingZones = data === undefined && (isPending || isFetching) → `!isConfigured && !loadingZones` 不成立。
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { focusManager } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getConfig: vi.fn(),
  listZones: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/cloudflare", () => ({
  CloudflareService: {
    getConfig: mocks.getConfig,
    listZones: mocks.listZones,
    listDnsRecords: vi.fn(),
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

const { default: DomainPage } = await import("./DomainPage");
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

function notConfigured() {
  return Object.assign(new Error("Cloudflare not configured"), { status: 400 });
}

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  mocks.getConfig.mockResolvedValue({ is_configured: false, has_api_token: false });
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

test("returning to the window keeps the not-connected empty state instead of flashing the zone workbench", async () => {
  let rejectRetry;
  mocks.listZones
    .mockRejectedValueOnce(notConfigured())
    .mockImplementationOnce(() => new Promise((_, reject) => { rejectRetry = reject; }));

  await act(async () => { root.render(withQueryClient(<DomainPage />, client)); });
  await flush();
  expect(host.textContent).toContain("DomainPage.emptyNotConnected");

  act(() => { focusManager.setFocused(false); });
  act(() => { focusManager.setFocused(true); });
  await flush();

  /* 修正後與舊版相同：切回視窗不重抓（只在進頁與存連線設定後抓） */
  expect(mocks.listZones).toHaveBeenCalledTimes(1);
  /* 舊版：空狀態不動；新版：換成 zone 工作區＋轉圈 */
  expect(host.querySelector('[data-testid="loading"]')).toBeNull();
  expect(host.textContent).toContain("DomainPage.emptyNotConnected");

  await act(async () => { rejectRetry?.(notConfigured()); });
  await flush();
});
