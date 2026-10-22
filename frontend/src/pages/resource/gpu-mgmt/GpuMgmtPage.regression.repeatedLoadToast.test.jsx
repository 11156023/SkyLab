// @vitest-environment happy-dom

/*
 * 回歸：GPU 對應清單首載失敗後（例如 PVE 連不上），新版每 30 秒的背景輪詢每重試一次：
 *   1. 就再跳一次錯誤 toast；
 *   2. 畫面從「沒有資料」的空清單閃回整頁載入畫面，直到這次重試失敗。
 *
 * 新版（pages/resource/gpu-mgmt/GpuMgmtPage.jsx）：
 *   loading = mappingsData === undefined && mappingsQuery.isPending
 *   loadFailedMessage = mappingsData === undefined && mappingsQuery.isError ? message : null
 * 沒有資料時 TanStack Query v5 每次重抓都把 status 改回 pending、error 清空：
 * isPending 變 true（閃回載入畫面）、loadFailedMessage 先變 null 再變回同一句（effect 又 toast 一次），
 * 註解說的「訊息相同就不重複提示」並不成立。
 * 舊版（ce7675b9^）首載失敗 toast 一次、loading 收掉顯示空清單；useAutoRefresh 的 load(true) 不動 loading、失敗不提示。
 */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { notifyManager } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { withQueryClient } from "../../../queries/testing";
import GpuMgmtPage from "./GpuMgmtPage";

const mocks = vi.hoisted(() => ({
  listMappings: vi.fn(),
  deleteMapping: vi.fn(),
  confirm: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/gpu", () => ({
  GpuService: { listMappings: mocks.listMappings, deleteMapping: mocks.deleteMapping },
}));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: () => null }));
vi.mock("../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));
vi.mock("../../../components/EmptyState/EmptyState", () => ({ default: () => <div data-testid="empty" /> }));

let root;
let host;

async function advance(ms) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.useFakeTimers();
  notifyManager.setScheduler((cb) => cb());
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
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

async function renderPage() {
  await act(async () => { root.render(withQueryClient(<GpuMgmtPage />)); });
  await advance(10);
  await advance(10);
}

describe("GpuMgmtPage：GPU 對應清單一直載不到", () => {
  test("同一個錯誤只在首載提示一次，不會每 30 秒重跳", async () => {
    mocks.listMappings.mockRejectedValue(new Error("PVE 連線失敗"));

    await renderPage();
    expect(mocks.toast.error).toHaveBeenCalledTimes(1);

    /* 頁面開著 90 秒（API 持續失敗，背景輪詢重試三次） */
    for (let i = 0; i < 3; i += 1) {
      await advance(30_005);
      await advance(10);
    }
    expect(mocks.listMappings.mock.calls.length).toBeGreaterThanOrEqual(4);

    /* 舊版：背景刷新 load(true) 失敗不提示，只有首載那 1 次 */
    expect(mocks.toast.error).toHaveBeenCalledTimes(1);
  });

  test("背景重試期間畫面不閃回整頁載入畫面", async () => {
    mocks.listMappings.mockRejectedValueOnce(new Error("PVE 連線失敗"));
    const retry = deferred();
    mocks.listMappings.mockReturnValueOnce(retry.promise);

    await renderPage();
    /* 首載失敗：顯示空清單 */
    expect(host.querySelector("[data-testid='loading']")).toBeNull();
    expect(host.querySelector("[data-testid='empty']")).not.toBeNull();

    /* 30 秒後背景輪詢重試，請求還在路上 */
    await advance(30_005);
    expect(mocks.listMappings).toHaveBeenCalledTimes(2);

    /* 舊版：背景刷新不動 loading，畫面維持空清單 */
    expect(host.querySelector("[data-testid='loading']")).toBeNull();

    await act(async () => { retry.reject(new Error("PVE 連線失敗")); });
    await advance(10);
  });
});
