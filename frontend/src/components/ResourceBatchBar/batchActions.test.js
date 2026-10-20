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

  test("刪除只送 can_delete 沒被關掉的機器（課堂機、共享機、環境內機器單台不可刪）", () => {
    const mixed = [
      { vmid: 1, status: "running" },
      { vmid: 2, status: "stopped", can_delete: false },
      { vmid: 3, status: "stopped", can_delete: true },
      { vmid: 4, status: "failed", is_placeholder: true },
    ];
    expect(applicableVmids(mixed, "delete")).toEqual([1, 3]);
  });

  test("電源動作只送 can_control 沒被關掉的機器", () => {
    const mixed = [
      { vmid: 1, status: "running" },
      { vmid: 2, status: "running", can_control: false },
      { vmid: 3, status: "stopped", can_control: false },
    ];
    expect(applicableVmids(mixed, "shutdown")).toEqual([1]);
    expect(applicableVmids(mixed, "start")).toEqual([]);
  });
});
