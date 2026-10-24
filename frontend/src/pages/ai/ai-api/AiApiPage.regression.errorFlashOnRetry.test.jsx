// @vitest-environment happy-dom

/*
 * 回歸：AI API 頁「我的金鑰」首載失敗、「我的申請」有待審時，錯誤畫面（含重試鈕）每 30 秒閃回載入畫面一次。
 *
 * 新版（pages/ai/ai-api/AiApiPage.jsx:1349-1352）：
 *   loading = noData && (credentialsQuery.isPending || requestsQuery.isPending || fetching)
 *   loadError = noData && (credentialsQuery.isError || requestsQuery.isError) && !fetching
 * 正是 CLAUDE.md 說不要寫的 `noData && isError && !isFetching`。申請那支有待審就 30 秒輪詢、金鑰那支也跟著
 * pendingCount 輪詢；任何一支在重抓（isFetching）時 loadError 變 false、loading 變 true，錯誤畫面就換成轉圈圈，
 * 重試失敗後再閃回錯誤。
 * 舊版（ce7675b9^ AiApiPage.jsx:1262-1308）兩份資料同一個 Promise.all，任一失敗就整批失敗、requests 維持 []，
 * pendingCount 為 0 不會背景刷新；錯誤畫面一直留著直到使用者按重試。
 */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { notifyManager } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { withQueryClient } from "../../../queries/testing";
import AiApiPage from "./AiApiPage";

const mocks = vi.hoisted(() => ({
  listMyCredentials: vi.fn(),
  listMyRequests: vi.fn(),
  getCredential: vi.fn(),
  getMyUsage: vi.fn(),
  getMyUsageRecords: vi.fn(),
  confirm: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/aiApi", () => ({
  AiApiService: {
    listMyCredentials: mocks.listMyCredentials,
    listMyRequests: mocks.listMyRequests,
    getCredential: mocks.getCredential,
    getMyUsage: mocks.getMyUsage,
    getMyUsageRecords: mocks.getMyUsageRecords,
  },
}));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { role: "student" } }) }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: () => null }));
vi.mock("../../../components/RrdChart/RrdChart", () => ({ default: () => null }));
vi.mock("../../../components/SegmentedControl/SegmentedControl", () => ({ default: () => null }));
vi.mock("../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));
vi.mock("../../../components/ErrorState/ErrorState", () => ({ default: () => <div data-testid="error" /> }));

const PENDING_REQUEST = Object.freeze({
  id: "req-1",
  status: "pending",
  api_key_name: "course-key",
  purpose: "課程作業需要呼叫模型",
  duration: "30d",
  created_at: "2026-10-20T00:00:00Z",
});

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

describe("AiApiPage：金鑰清單首載失敗、申請有待審", () => {
  test("背景輪詢重試期間錯誤畫面維持，不閃回載入畫面", async () => {
    const hanging = new Promise(() => {});
    mocks.listMyRequests.mockResolvedValue({ data: [PENDING_REQUEST], count: 1 });
    /* 金鑰清單首載失敗；之後的重試請求一直在路上 */
    mocks.listMyCredentials
      .mockRejectedValueOnce(new Error("金鑰清單載入失敗"))
      .mockReturnValue(hanging);

    await act(async () => { root.render(withQueryClient(<AiApiPage />)); });
    await advance(10);
    await advance(10);
    expect(host.querySelector("[data-testid='error']")).not.toBeNull();
    expect(host.querySelector("[data-testid='loading']")).toBeNull();

    /* 30 秒後（有待審申請）背景輪詢 */
    await advance(30_005);
    expect(mocks.listMyCredentials).toHaveBeenCalledTimes(2);

    /* 舊版：錯誤畫面一直留著，直到使用者按重試 */
    expect(host.querySelector("[data-testid='loading']")).toBeNull();
    expect(host.querySelector("[data-testid='error']")).not.toBeNull();
  });
});
