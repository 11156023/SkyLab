import { describe, expect, it } from "vitest";
import {
  assignmentsUntilToday,
  buildPracticeMachines,
  practiceMachineActionLabel,
  pickInProgress,
  toPercent,
} from "./studentDashboard";

describe("assignmentsUntilToday", () => {
  it("保留今天以前的所有任務、排除未來任務並依日期排列", () => {
    const assignments = assignmentsUntilToday([
      { id: "today", approved_at: "2026-08-25T15:00:00+08:00" },
      { id: "future", approved_at: "2026-08-26T09:00:00+08:00" },
      { id: "older", approved_at: "2026-08-18T09:00:00+08:00" },
      { id: "legacy", approved_at: null },
    ], new Date("2026-08-25T10:00:00+08:00"));

    expect(assignments.map((item) => item.id)).toEqual(["legacy", "older", "today"]);
  });
});

describe("buildPracticeMachines", () => {
  it("保留課程中的多台機器角色，並合併即時資源狀態", () => {
    const machines = buildPracticeMachines([
      { machine_node_id: "main", name: "操作主機", role: "主要練習機", resource_type: "qemu", vmid: 218, status: "completed" },
      { machine_node_id: "db", name: "資料庫主機", role: "資料庫驗證", resource_type: "lxc", vmid: null, status: "pending" },
    ], [
      { vmid: 218, name: "student-main", type: "qemu", status: "running" },
    ]);

    expect(machines).toHaveLength(2);
    expect(machines[0]).toMatchObject({
      classMachineName: "操作主機",
      classMachineRole: "主要練習機",
      name: "student-main",
      status: "running",
    });
    expect(machines[1]).toMatchObject({
      classMachineName: "資料庫主機",
      vmid: null,
      status: "pending",
    });
  });
});

describe("practiceMachineActionLabel", () => {
  it("課堂機器只顯示自動開機狀態，不提供學生啟動操作", () => {
    expect(practiceMachineActionLabel({ vmid: 218, status: "running" })).toBe("進入機器");
    expect(practiceMachineActionLabel({ vmid: 218, status: "stopped" })).toBe("啟動並進入");
    expect(practiceMachineActionLabel({ vmid: null, status: "pending" })).toBe("環境配置中");
  });
});

describe("toPercent", () => {
  it("把進度收斂到 0–100 的整數，非數值視為 0", () => {
    expect(toPercent(42.4)).toBe(42);
    expect(toPercent(-5)).toBe(0);
    expect(toPercent(180)).toBe(100);
    expect(toPercent(null)).toBe(0);
    expect(toPercent("abc")).toBe(0);
  });
});

describe("pickInProgress", () => {
  it("優先挑進行中的項目", () => {
    const picked = pickInProgress([
      { id: "done", progress_percent: 100 },
      { id: "doing", progress_percent: 40 },
      { id: "fresh", progress_percent: 0 },
    ]);

    expect(picked.id).toBe("doing");
  });

  it("沒有進行中的項目時退回第一個尚未完成的", () => {
    const picked = pickInProgress([
      { id: "done", progress_percent: 100 },
      { id: "fresh", progress_percent: 0 },
    ]);

    expect(picked.id).toBe("fresh");
  });

  it("全部完成時退回第一筆，空清單回 null", () => {
    expect(pickInProgress([{ id: "a", progress_percent: 100 }]).id).toBe("a");
    expect(pickInProgress([])).toBeNull();
    expect(pickInProgress(undefined)).toBeNull();
  });
});
