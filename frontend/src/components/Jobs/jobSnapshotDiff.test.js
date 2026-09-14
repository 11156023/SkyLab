import { describe, expect, it } from "vitest";
import { diffJobSnapshot, jobTouchedAt } from "./jobSnapshotDiff";

const job = (id, status, updated_at, extra = {}) => ({ id, status, updated_at, ...extra });

describe("jobTouchedAt", () => {
  it("取 updated_at 與 completed_at 較晚者，缺欄位回 0", () => {
    expect(jobTouchedAt({ updated_at: "2026-09-09T00:00:00Z", completed_at: "2026-09-09T00:00:05Z" }))
      .toBe(Date.parse("2026-09-09T00:00:05Z"));
    expect(jobTouchedAt({ updated_at: "2026-09-09T00:00:09Z", completed_at: null }))
      .toBe(Date.parse("2026-09-09T00:00:09Z"));
    expect(jobTouchedAt({})).toBe(0);
    expect(jobTouchedAt({ updated_at: "not a date" })).toBe(0);
  });
});

describe("diffJobSnapshot", () => {
  it("第一次快照只建立基準，不通知任何歷史任務", () => {
    const items = [
      job("a", "completed", "2026-09-09T00:00:10Z"),
      job("b", "running", "2026-09-09T00:00:12Z"),
    ];
    const { transitions, baseline } = diffJobSnapshot(items, null);
    expect(transitions).toEqual([]);
    expect(baseline.statusMap.get("a")).toBe("completed");
    expect(baseline.statusMap.get("b")).toBe("running");
    expect(baseline.highWater).toBe(Date.parse("2026-09-09T00:00:12Z"));
  });

  it("見過的任務從執行中變成終態 → 通知", () => {
    const first = diffJobSnapshot([job("b", "running", "2026-09-09T00:00:12Z")], null);
    const second = diffJobSnapshot([job("b", "completed", "2026-09-09T00:00:20Z")], first.baseline);
    expect(second.transitions.map((j) => j.id)).toEqual(["b"]);
  });

  it("狀態沒變（終態保持終態、updated_at 被更新）→ 不重複通知", () => {
    const first = diffJobSnapshot([job("a", "blocked", "2026-09-09T00:00:10Z")], null);
    const second = diffJobSnapshot([job("a", "blocked", "2026-09-09T01:00:00Z")], first.baseline);
    expect(second.transitions).toEqual([]);
  });

  it("兩次推送之間建立並完成的任務（首次看到就是終態、時間晚於高水位）→ 通知", () => {
    const first = diffJobSnapshot([job("old", "completed", "2026-09-09T00:00:10Z")], null);
    const quick = job("quick", "completed", "2026-09-09T00:00:13Z", {
      created_at: "2026-09-09T00:00:11Z",
      completed_at: "2026-09-09T00:00:13Z",
    });
    const second = diffJobSnapshot([quick, job("old", "completed", "2026-09-09T00:00:10Z")], first.baseline);
    expect(second.transitions.map((j) => j.id)).toEqual(["quick"]);
    // 下一輪同一筆已在基準內，不會再通知
    const third = diffJobSnapshot([quick], second.baseline);
    expect(third.transitions).toEqual([]);
  });

  it("首次看到但早於高水位的終態任務（被擠回清單的歷史）→ 不通知", () => {
    const first = diffJobSnapshot([job("a", "completed", "2026-09-09T00:00:10Z")], null);
    const stale = job("stale", "failed", "2026-09-08T23:00:00Z");
    const second = diffJobSnapshot([stale], first.baseline);
    expect(second.transitions).toEqual([]);
  });

  it("首次看到但仍在執行中 → 不通知，並記入基準", () => {
    const first = diffJobSnapshot([], null);
    const second = diffJobSnapshot([job("r", "running", "2026-09-09T00:00:30Z")], first.baseline);
    expect(second.transitions).toEqual([]);
    expect(second.baseline.statusMap.get("r")).toBe("running");
  });

  it("高水位單調遞增：舊快照的最大值不會因為新快照沒帶那筆而倒退", () => {
    const first = diffJobSnapshot([job("a", "completed", "2026-09-09T00:00:10Z")], null);
    const second = diffJobSnapshot([job("b", "running", "2026-09-09T00:00:05Z")], first.baseline);
    expect(second.baseline.highWater).toBe(Date.parse("2026-09-09T00:00:10Z"));
  });

  it("items 不是陣列時視為空快照", () => {
    const { transitions, baseline } = diffJobSnapshot(undefined, null);
    expect(transitions).toEqual([]);
    expect(baseline.statusMap.size).toBe(0);
  });
});
