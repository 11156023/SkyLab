import { describe, expect, it } from "vitest";
import {
  BOOTING_POLL_INTERVAL,
  LIVE_STATUSES,
  machineSpecLabel,
  statusAfterAction,
} from "./resourceRows";

describe("statusAfterAction", () => {
  it("marks stop and shutdown as stopped", () => {
    expect(statusAfterAction("stop")).toBe("stopped");
    expect(statusAfterAction("shutdown")).toBe("stopped");
  });

  it("marks start and reboot as starting so the console stays disabled", () => {
    expect(statusAfterAction("start")).toBe("starting");
    expect(statusAfterAction("reboot")).toBe("starting");
  });

  it("keeps a reset machine running", () => {
    expect(statusAfterAction("reset")).toBe("running");
  });
});

describe("machineSpecLabel", () => {
  it("joins CPU and rounded memory", () => {
    expect(machineSpecLabel({ cpu: 2, memoryBytes: 4 * 1024 ** 3 })).toBe("2 CPU · 4 GB");
  });

  it("omits missing parts", () => {
    expect(machineSpecLabel({ cpu: 4 })).toBe("4 CPU");
    expect(machineSpecLabel({})).toBe("");
  });
});

describe("live status constants", () => {
  it("treats starting machines as live and polls faster while booting", () => {
    expect(LIVE_STATUSES.has("starting")).toBe(true);
    expect(LIVE_STATUSES.has("error")).toBe(false);
    expect(BOOTING_POLL_INTERVAL).toBe(5_000);
  });
});
