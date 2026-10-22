// @vitest-environment happy-dom
/*
 * 回歸重現：PVE 從一開始就連不上（沒有任何 overview）時，背景重試（30 秒輪詢／切回視窗）進行中
 * usePveOverview 會回到 loading=true、error=false。
 * 舊版（ce7675b9^ hooks/usePveOverview.js）首次結果之後 loading 永遠是 false，silent 重抓期間 error 維持 true，
 * 監控頁的概況區與管理首頁的「檢查中」不會跟著每 30 秒閃一次。
 * 既有測試 "retrying while PVE is still unreachable does not flash back to loading" 用的是立即 reject 的 mock，
 * 中間的 pending 狀態來不及 render，所以測不出來；這裡讓第二次請求停在路上。
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { withQueryClient } from "../queries/testing";
import usePveOverview from "./usePveOverview";

const mocks = vi.hoisted(() => ({ getOverview: vi.fn() }));
vi.mock("../services/monitoring", () => ({ MonitoringService: { getOverview: mocks.getOverview } }));

let host;
let root;
let seen;

function Probe() {
  seen = usePveOverview();
  /* 結果物件是存取追蹤的：要斷言的欄位在 render 時讀過 */
  void seen.loading;
  void seen.error;
  return null;
}

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
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

test("a slow background retry after PVE was never reachable keeps error and does not go back to loading", async () => {
  let rejectRetry;
  mocks.getOverview
    .mockRejectedValueOnce(new Error("PVE down"))
    .mockImplementationOnce(() => new Promise((_, reject) => { rejectRetry = reject; }));

  await act(async () => { root.render(withQueryClient(<Probe />)); });
  await flush();
  expect(seen.loading).toBe(false);
  expect(seen.error).toBe(true);

  /* 背景重試（等同 30 秒輪詢的一次 tick）送出、還沒回來 */
  let retrying;
  act(() => { retrying = seen.reload(); });
  await flush();

  expect(mocks.getOverview).toHaveBeenCalledTimes(2);
  /* 舊版：loading=false、error=true；新版：loading=true、error=false（畫面閃回載入中） */
  expect({ loading: seen.loading, error: seen.error }).toEqual({ loading: false, error: true });

  await act(async () => { rejectRetry(new Error("PVE down")); await retrying; });
  await flush();
});
