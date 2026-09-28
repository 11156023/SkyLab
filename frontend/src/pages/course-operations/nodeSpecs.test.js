import { describe, expect, it } from "vitest";
import { environmentSpecs, sumNodeSpecs } from "./nodeSpecs";

describe("sumNodeSpecs", () => {
  it("兩種節點形狀都能加總（memory GB 或 memory_mb）", () => {
    expect(sumNodeSpecs([
      { cpu: 2, memory: 4, disk: 20 },
      { cpu: 1, memory_mb: 2048, disk_gb: 10 },
    ])).toEqual({ cpu: 3, memoryGb: 6, disk: 30 });
    expect(sumNodeSpecs(undefined)).toEqual({ cpu: 0, memoryGb: 0, disk: 0 });
  });
});

describe("environmentSpecs", () => {
  it("記憶體先加總（再乘學生數）才取整，不逐台四捨五入", () => {
    const nodes = [
      { cpu: 1, memory_mb: 1536, disk_gb: 10 },
      { cpu: 1, memory_mb: 1536, disk_gb: 10 },
    ];
    expect(environmentSpecs(nodes)).toEqual({ cpu: 2, memory: 3, disk: 20 });
    expect(environmentSpecs([nodes[0]], 30)).toEqual({ cpu: 30, memory: 45, disk: 300 });
  });

  it("課程環境正規化後的整數 GB 維持原值", () => {
    expect(environmentSpecs([{ cpu: 2, memory: 4, disk: 20 }, { cpu: 4, memory: 8, disk: 40 }]))
      .toEqual({ cpu: 6, memory: 12, disk: 60 });
  });
});
