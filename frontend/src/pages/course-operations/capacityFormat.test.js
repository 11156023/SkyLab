import { describe, expect, it } from "vitest";
import { bytesToGb, capacityNodeRows, mbToGb } from "./capacityFormat";

const GIB = 1024 ** 3;

describe("capacityFormat", () => {
  it("keeps one decimal instead of rounding to whole GB", () => {
    expect(mbToGb(1536)).toBe(1.5);
    expect(mbToGb(4096)).toBe(4);
    expect(bytesToGb(7.5 * GIB)).toBe(7.5);
    expect(bytesToGb(null)).toBe("—");
  });

  it("lists every node with need and availability, flagging shortages", () => {
    const rows = capacityNodeRows({
      pve2: { machines: 2, cpu_cores: 4, memory_bytes: 8 * GIB, available_cpu_cores: 10, available_memory_bytes: 7.5 * GIB },
      pve1: { machines: 1, cpu_cores: 2, memory_bytes: 2 * GIB },
    });
    expect(rows.map((row) => row.node)).toEqual(["pve1", "pve2"]);
    expect(rows[0]).toMatchObject({ cpuAvailable: "—", memoryAvailable: "—", shortCpu: false, shortMemory: false });
    expect(rows[1]).toMatchObject({ memoryNeed: 8, memoryAvailable: 7.5, shortCpu: false, shortMemory: true });
  });

  it("returns no rows when the cluster was not checked", () => {
    expect(capacityNodeRows(undefined)).toEqual([]);
  });
});
