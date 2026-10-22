// @vitest-environment happy-dom

/*
 * 回歸：範本清單首載失敗後（畫面顯示「沒有範本」），新版每 30 秒背景輪詢重試時整頁閃回載入畫面，
 * 直到這次重試結束。
 *
 * 新版（pages/resource/templates/TemplatesPage.jsx:396-398）：
 *   templatesFailure = templatesQuery.isError ? error : null
 *   templates = templatesData ?? (templatesFailure ? NO_TEMPLATES : null)   // null → 整頁 LoadingState
 * 沒有資料時 TanStack Query v5 重抓會把 status 改回 pending、清掉 error，templates 變回 null。
 * 舊版（ce7675b9^ TemplatesPage.jsx:297-330）失敗時 setTemplates((prev) => prev ?? [])，之後的輪詢不會把它改回 null，
 * 畫面一直停在空清單（每次失敗照樣 toast，這點新舊相同）。
 */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { notifyManager } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { withQueryClient } from "../../../queries/testing";
import TemplatesPage from "./TemplatesPage";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  confirm: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/templates", () => ({
  TemplatesService: {
    list: mocks.list,
    listAttachments: vi.fn(),
    downloadAttachment: vi.fn(),
    startUpdateCycle: vi.fn(),
    finishUpdateCycle: vi.fn(),
    cancelUpdateCycle: vi.fn(),
    retry: vi.fn(),
    remove: vi.fn(),
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
vi.mock("../../../components/EmptyState/EmptyState", () => ({ default: () => <div data-testid="empty" /> }));
vi.mock("./TemplateFormDialog", () => ({ default: () => null }));
vi.mock("./TemplateCloneDialog", () => ({ default: () => null }));

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

describe("TemplatesPage：範本清單首載失敗後的背景重試", () => {
  test("重試進行中畫面維持空清單，不閃回整頁載入畫面", async () => {
    const hanging = new Promise(() => {});
    mocks.list
      .mockRejectedValueOnce(new Error("範本清單載入失敗"))
      .mockReturnValue(hanging);

    await act(async () => { root.render(withQueryClient(<TemplatesPage />)); });
    await advance(10);
    await advance(10);
    expect(host.querySelector("[data-testid='empty']")).not.toBeNull();
    expect(host.querySelector("[data-testid='loading']")).toBeNull();

    /* 30 秒後背景輪詢重試，請求還在路上 */
    await advance(30_005);
    expect(mocks.list).toHaveBeenCalledTimes(2);

    /* 舊版：templates 已是 []，輪詢不會改回 null，畫面維持空清單 */
    expect(host.querySelector("[data-testid='loading']")).toBeNull();
    expect(host.querySelector("[data-testid='empty']")).not.toBeNull();
  });
});
