// @vitest-environment happy-dom

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

/*
 * 回歸：學生機器清單（listClassStudents）首載失敗後，新版的 15 秒輪詢會一直重試；
 * 沒有資料時每次重抓 TanStack Query v5 都把 status 改回 pending、error 清空，於是
 *   - membersLoading（isPending && isFetching）在每次重試期間變 true → AiJudgePanel 被換成載入畫面再重掛，
 *     面板裡的草稿／對話狀態全部清掉
 *   - membersFailedMessage 在重試期間變 null、失敗後又變回同一句 → effect 再 toast 一次（每 15 秒一次）
 * 舊版（ce7675b9^）首載失敗只 toast 一次、面板維持掛載，不會重試。
 */

import { act, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { notifyManager } from "@tanstack/react-query";
import { toast } from "sonner";
import AiJudgePage from "./AiJudgePage";
import { withQueryClient } from "../../../queries/testing";
import { TeachingClassesService } from "../../../services/teachingClasses";
import { ClassroomService } from "../../../services/classroom";
import "../../../i18n";

const panelMounts = vi.hoisted(() => ({ count: 0 }));

vi.mock("sonner", () => ({ toast: { error: vi.fn(), info: vi.fn(), success: vi.fn(), warning: vi.fn() } }));
vi.mock("../../../contexts/UnsavedChangesContext", () => ({
  useUnsavedChanges: () => ({ confirmLeave: async () => true }),
}));
vi.mock("../class-workspace/AiJudgePanel", () => ({
  default: function FakeAiJudgePanel() {
    /* 代表面板裡老師打到一半的內容：重掛就會歸零 */
    const [draft] = useState("draft-in-progress");
    useEffect(() => {
      panelMounts.count += 1;
    }, []);
    return <div data-testid="ai-judge-panel">{draft}</div>;
  },
}));

const classDetail = {
  id: 42,
  name: "網路管理",
  status: "active",
  term: "115-1",
  weekday: 1,
  start_time: "09:00:00",
  end_time: "11:00:00",
  students: [{ id: 7, email: "s@example.edu" }],
  weeks: [{ id: 3, week_number: 1, title: "W1" }],
  machine_nodes: [],
};

async function advance(ms) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

function mount() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(withQueryClient(
      <MemoryRouter initialEntries={["/class-management/42/ai"]}>
        <Routes>
          <Route path="/class-management/:classId/ai" element={<AiJudgePage />} />
        </Routes>
      </MemoryRouter>,
    ));
  });
  return {
    container,
    cleanup: () => {
      act(() => root.unmount());
      container.remove();
    },
  };
}

beforeEach(() => {
  vi.useFakeTimers();
  notifyManager.setScheduler((cb) => cb());
  panelMounts.count = 0;
});

afterEach(() => {
  notifyManager.setScheduler((cb) => setTimeout(cb, 0));
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("AiJudgePage：學生機器首載失敗後", () => {
  test("面板不會每 15 秒被換成載入畫面重掛、錯誤 toast 只跳一次", async () => {
    vi.spyOn(TeachingClassesService, "get").mockResolvedValue(classDetail);
    vi.spyOn(ClassroomService, "listClassStudents").mockRejectedValue(new Error("PVE 無法連線"));

    const view = mount();
    await advance(10);
    await advance(10);

    /* 首載失敗：面板照樣掛著（members 為空）、提示一次 */
    expect(view.container.querySelector("[data-testid='ai-judge-panel']")).not.toBeNull();
    expect(panelMounts.count).toBe(1);
    expect(toast.error).toHaveBeenCalledTimes(1);

    /* 老師繼續在面板上操作 45 秒（期間學生機器 API 仍然失敗） */
    for (let i = 0; i < 3; i += 1) {
      await advance(15_005);
      await advance(10);
    }

    expect(view.container.querySelector("[data-testid='ai-judge-panel']")).not.toBeNull();
    /* 面板不可被卸載重掛（舊版：1 次） */
    expect.soft(panelMounts.count).toBe(1);
    /* 同一個錯誤不可每輪重跳（舊版：1 次） */
    expect.soft(toast.error).toHaveBeenCalledTimes(1);
    view.cleanup();
  });
});
