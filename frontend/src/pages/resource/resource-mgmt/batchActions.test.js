import { describe, expect, test } from "vitest";
import { applicableVmids } from "./batchActions";

describe("applicableVmids", () => {
  const list = [
    { vmid: 1, status: "running" },
    { vmid: 2, status: "stopped" },
    { vmid: 3, status: "starting" },
    { vmid: 4, status: "stopped", start_blocked_reason: "window_ended" },
  ];

  test("每個動作只挑狀態對得上的機器（規則同單台電源選單）", () => {
    expect(applicableVmids(list, "start")).toEqual([2]);
    expect(applicableVmids(list, "shutdown")).toEqual([1]);
    expect(applicableVmids(list, "stop")).toEqual([1, 3]);
    expect(applicableVmids(list, "delete")).toEqual([1, 2, 3, 4]);
  });
});
