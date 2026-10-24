// @vitest-environment happy-dom

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

/*
 * 回歸：課堂監看（StudentMachines）的資源用量從第一次就讀不到時，熱力圖的狀態徽章每 10 秒在
 * 「更新失敗」與「讀取即時資料」之間跳。新版 usageStatus = isError ? "error" : data ? "ready" : "loading"，
 * 沒有資料時 TanStack Query v5 每次重抓都把 status 改回 pending、清掉 error，重試期間就變回「讀取即時資料」。
 * 舊版（ce7675b9^）失敗後只在下一次結果回來時改狀態，重試期間維持「更新失敗」。
 */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { notifyManager } from "@tanstack/react-query";
import { StudentMachines } from "./ClassWorkspacePage";
import { withQueryClient } from "../../../queries/testing";
import { TeachingClassesService } from "../../../services/teachingClasses";
import { ClassroomService } from "../../../services/classroom";
import i18n from "../../../i18n";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), info: vi.fn(), success: vi.fn(), warning: vi.fn() } }));
vi.mock("../../../components/Classroom/ClassroomWatchDialog", () => ({ default: () => null }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => vi.fn() }));

const classItem = {
  id: "c1",
  nodes: [{ id: "n1", name: "Web", resource_type: "qemu" }],
  students: [{ id: "s1", full_name: "Amy", email: "amy@example.com", machines: [{ vmid: 101, status: "running", machine_node_id: "n1" }] }],
};

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

describe("StudentMachines：資源用量一直讀不到", () => {
  test("背景重試期間徽章維持「更新失敗」，不閃回「讀取即時資料」", async () => {
    const failedLabel = i18n.t("ClassWorkspacePage.updateFailedLabel", { ns: "teaching" });
    const loadingLabel = i18n.t("ClassWorkspacePage.loadingLiveDataLabel", { ns: "teaching" });
    /* 第一次立刻失敗；之後每次重試都要一段時間才失敗（PVE 逾時） */
    let calls = 0;
    vi.spyOn(TeachingClassesService, "resourceUsage").mockImplementation(() => {
      calls += 1;
      if (calls === 1) return Promise.reject(new Error("PVE 逾時"));
      return new Promise((_, reject) => { setTimeout(() => reject(new Error("PVE 逾時")), 3_000); });
    });
    vi.spyOn(ClassroomService, "listClassBroadcastSources").mockResolvedValue([]);
    vi.spyOn(ClassroomService, "listSessions").mockResolvedValue([]);

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    act(() => {
      root.render(withQueryClient(<StudentMachines item={classItem} />));
    });
    await advance(10);
    await advance(10);
    expect(container.textContent).toContain(failedLabel);

    /* 10 秒後背景重試，結果還沒回來（舊版：仍是「更新失敗」） */
    await advance(10_005);
    expect(calls).toBe(2);
    expect(container.textContent).not.toContain(loadingLabel);
    expect(container.textContent).toContain(failedLabel);

    act(() => root.unmount());
    container.remove();
  });
});
