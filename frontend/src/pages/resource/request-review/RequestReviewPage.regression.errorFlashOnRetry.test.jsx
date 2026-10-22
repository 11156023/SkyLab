// @vitest-environment happy-dom

/*
 * 回歸：申請審核清單首載失敗後，新版每 30 秒背景輪詢重試時，錯誤訊息與「重試」鈕會整個換成載入畫面，
 * 直到這次重試結束（失敗就再閃回錯誤）。
 *
 * 新版（pages/resource/request-review/RequestReviewPage.jsx:327、368）：
 *   error = noData && failure && !fetching ? failure : null；loading = list.noData && !list.error
 * 正是 CLAUDE.md 說不要寫的 `noData && isError && !isFetching`：沒有資料時每次重抓都把 status 改回 pending、
 * 清掉 error，錯誤畫面就在背景重試時閃。
 * 舊版（ce7675b9^ RequestReviewPage.jsx:342-391）背景刷新是 fetchRequests(tab, true)：不動 loading／error，
 * 錯誤畫面一直留著。
 */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { notifyManager } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import RequestReviewPage from "./RequestReviewPage";
import { withQueryClient } from "../../../queries/testing";

const mocks = vi.hoisted(() => ({
  vmListAll: vi.fn(),
  getReviewContext: vi.fn(),
  vmReview: vi.fn(),
  specListAll: vi.fn(),
  specReview: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/vmRequests", () => ({
  VmRequestsService: {
    listAll: mocks.vmListAll,
    getReviewContext: mocks.getReviewContext,
    review: mocks.vmReview,
  },
}));
vi.mock("../../../services/specChangeRequests", () => ({
  SpecChangeRequestsService: { listAll: mocks.specListAll, review: mocks.specReview },
}));
vi.mock("../../../services/pendingResources", () => ({ CONSUMED_REQUEST_MARKERS: [] }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: () => null }));
vi.mock("../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));
vi.mock("../../../components/SegmentedControl/SegmentedControl", () => ({ default: () => null }));

let root;
let host;

async function advance(ms) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
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

describe("RequestReviewPage：清單首載失敗後的背景重試", () => {
  test("重試進行中錯誤畫面維持，不閃回載入畫面", async () => {
    const hanging = new Promise(() => {});
    let vmListCalls = 0;
    /* 待審分頁的完整清單（limit 1000）首載失敗、之後的重試一直在路上；角標查詢（limit 1）都正常 */
    mocks.vmListAll.mockImplementation((_status, { limit } = {}) => {
      if (limit === 1) return Promise.resolve({ data: [], count: 0 });
      vmListCalls += 1;
      return vmListCalls === 1 ? Promise.reject(new Error("申請清單載入失敗")) : hanging;
    });
    mocks.specListAll.mockResolvedValue({ data: [], count: 0 });

    await act(async () => { root.render(withQueryClient(<RequestReviewPage />)); });
    await advance(10);
    await advance(10);
    expect(host.textContent).toContain("申請清單載入失敗");
    expect(host.querySelector("[data-testid='loading']")).toBeNull();

    /* 30 秒後背景輪詢重試 */
    await advance(30_005);
    expect(vmListCalls).toBe(2);

    /* 舊版：背景刷新不動畫面，錯誤訊息與重試鈕還在 */
    expect(host.querySelector("[data-testid='loading']")).toBeNull();
    expect(host.textContent).toContain("申請清單載入失敗");
  });
});
