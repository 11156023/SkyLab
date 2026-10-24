// @vitest-environment happy-dom

/*
 * 回歸：批次審核清單首載失敗後，新版每 30 秒背景輪詢重試時，錯誤訊息與「重試」鈕會整個換成載入畫面，
 * 直到這次重試結束（失敗就再閃回錯誤）。
 *
 * 新版（pages/resource/batch-review/BatchReviewPage.jsx:255-258）：
 *   failed = noData && listFailure && !listFetching；loading = noData && !failed
 * 正是 CLAUDE.md 說不要寫的 `noData && isError && !isFetching`：沒有資料時每次重抓都把 status 改回 pending、
 * 清掉 error，錯誤畫面就在背景重試時閃。
 * 舊版（ce7675b9^ BatchReviewPage.jsx:239-277）背景刷新是 load(true)：不動 loading／error，錯誤畫面一直留著。
 */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { notifyManager } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import BatchReviewPage from "./BatchReviewPage";
import { withQueryClient } from "../../../queries/testing";

const mocks = vi.hoisted(() => ({
  listAll: vi.fn(),
  review: vi.fn(),
  getRecurrencePreview: vi.fn(),
  confirm: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/batchProvision", () => ({
  BatchProvisionService: {
    listAll: mocks.listAll,
    review: mocks.review,
    getRecurrencePreview: mocks.getRecurrencePreview,
  },
}));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));
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

describe("BatchReviewPage：清單首載失敗後的背景重試", () => {
  test("重試進行中錯誤畫面維持，不閃回載入畫面", async () => {
    let calls = 0;
    const hanging = new Promise(() => {});
    /* 首載（最新 N 筆＋待審兩支）失敗；之後的背景重試請求一直在路上 */
    mocks.listAll.mockImplementation(() => {
      calls += 1;
      return calls <= 2 ? Promise.reject(new Error("清單載入失敗")) : hanging;
    });

    await act(async () => { root.render(withQueryClient(<BatchReviewPage />)); });
    await advance(10);
    await advance(10);
    expect(host.textContent).toContain("清單載入失敗");
    expect(host.querySelector("[data-testid='loading']")).toBeNull();

    /* 30 秒後背景輪詢重試 */
    await advance(30_005);
    expect(mocks.listAll.mock.calls.length).toBeGreaterThan(2);

    /* 舊版：背景刷新不動畫面，錯誤訊息與重試鈕還在 */
    expect(host.querySelector("[data-testid='loading']")).toBeNull();
    expect(host.textContent).toContain("清單載入失敗");
  });
});
