import { describe, expect, it } from "vitest";
import {
  machineRuntimeState,
  mergeResourceUsageByVmid,
  usageForMetric,
} from "./classHeatmapUsage";

describe("class heatmap usage", () => {
  const machine = { vmid: 7300, status: "completed" };

  it("indexes one batch response by VMID", () => {
    const indexed = mergeResourceUsageByVmid({}, [{ vmid: 7300, status: "running" }]);
    expect(indexed["7300"].status).toBe("running");
  });

  it("distinguishes running, stopped, and unavailable machines", () => {
    expect(machineRuntimeState(machine, { status: "running" })).toBe("on");
    expect(machineRuntimeState(machine, { status: "stopped" })).toBe("off");
    expect(machineRuntimeState(machine, undefined)).toBe("unavailable");
  });

  it("reads real percentages without inventing a fallback value", () => {
    const runtime = { cpu_usage_pct: 42.67, ram_usage_pct: 75 };
    expect(usageForMetric(runtime, "cpu")).toBe(43);
    expect(usageForMetric(runtime, "ram")).toBe(75);
    expect(usageForMetric({}, "cpu")).toBeNull();
  });

  it("reuses the previous snapshot until a displayed value changes", () => {
    const current = mergeResourceUsageByVmid({}, [
      { vmid: 7300, status: "running", cpu_usage_pct: 20, ram_usage_pct: 40 },
    ]);
    const unchanged = mergeResourceUsageByVmid(current, [
      { vmid: 7300, status: "running", cpu_usage_pct: 20, ram_usage_pct: 40 },
    ]);
    const changed = mergeResourceUsageByVmid(current, [
      { vmid: 7300, status: "running", cpu_usage_pct: 21, ram_usage_pct: 40 },
    ]);

    expect(unchanged).toBe(current);
    expect(changed).not.toBe(current);
    expect(changed["7300"].cpu_usage_pct).toBe(21);
  });
});
