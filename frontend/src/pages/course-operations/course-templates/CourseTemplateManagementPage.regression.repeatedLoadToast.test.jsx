// @vitest-environment happy-dom

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

/*
 * 回歸：教學環境清單首載失敗後，新版的 30 秒背景輪詢每次重試都會再跳一次錯誤 toast。
 * 沒有資料時 TanStack Query v5 重抓會把 status 改回 pending、error 清空，loadFailedMessage 先變 null、
 * 失敗後又變回同一句，effect 依字串觸發就再提示一次（註解說「訊息相同就不重複提示」並不成立）。
 * 舊版（ce7675b9^）只在進頁時載一次，失敗 toast 一次。
 */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { notifyManager } from "@tanstack/react-query";
import { toast } from "sonner";
import CourseTemplateManagementPage from "./CourseTemplateManagementPage";
import { withQueryClient } from "../../../queries/testing";
import { CourseEnvironmentsService } from "../../../services/courseEnvironments";
import "../../../i18n";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), info: vi.fn(), success: vi.fn(), warning: vi.fn() } }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => vi.fn() }));

async function advance(ms) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  notifyManager.setScheduler((cb) => cb());
});

afterEach(() => {
  notifyManager.setScheduler((cb) => setTimeout(cb, 0));
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("CourseTemplateManagementPage：教學環境清單一直載不到", () => {
  test("同一個錯誤只提示一次，不會每 30 秒重跳", async () => {
    vi.spyOn(CourseEnvironmentsService, "list").mockRejectedValue(new Error("伺服器暫時無法使用"));

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    act(() => {
      root.render(withQueryClient(
        <MemoryRouter initialEntries={["/course-template-management"]}>
          <CourseTemplateManagementPage />
        </MemoryRouter>,
      ));
    });
    await advance(10);
    await advance(10);
    expect(toast.error).toHaveBeenCalledTimes(1);

    /* 頁面開著 90 秒（API 持續失敗） */
    for (let i = 0; i < 3; i += 1) {
      await advance(30_005);
      await advance(10);
    }

    /* 舊版：1 次 */
    expect(toast.error).toHaveBeenCalledTimes(1);

    act(() => root.unmount());
    container.remove();
  });
});
