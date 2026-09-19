// @vitest-environment happy-dom
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { readRecentMachines, recordMachineUse, selectRecentMachines } from "./recentMachines";

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

it("keeps a deduplicated, most-recent-first history scoped to the user", () => {
  vi.spyOn(Date, "now").mockReturnValueOnce(100).mockReturnValueOnce(200).mockReturnValueOnce(300);
  recordMachineUse("alice", 101);
  recordMachineUse("alice", 102);
  recordMachineUse("alice", "101");
  expect(readRecentMachines("alice")).toEqual([{ vmid: 101, usedAt: 300 }, { vmid: 102, usedAt: 200 }]);
  expect(readRecentMachines("bob")).toEqual([]);
  expect(readRecentMachines(null)).toEqual([]);
});

it("only shows machines still returned by the current user's resource API", () => {
  const resources = [{ vmid: 102, name: "Current name", status: "stopped" }];
  const history = [{ vmid: 101, usedAt: 300 }, { vmid: 102, usedAt: 200 }];
  expect(selectRecentMachines(resources, history, 1)).toEqual([
    { vmid: 102, name: "Current name", status: "stopped", usedAt: 200 },
  ]);
  expect(selectRecentMachines([], history)).toEqual([]);
});

it("ignores malformed storage and cannot interrupt connection when storage is blocked", () => {
  localStorage.setItem("skylab:recent-machines:alice", "broken");
  expect(readRecentMachines("alice")).toEqual([]);
  localStorage.setItem("skylab:recent-machines:alice", JSON.stringify([null, { vmid: 10, usedAt: "bad" }]));
  expect(readRecentMachines("alice")).toEqual([]);
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
  expect(() => recordMachineUse("alice", 100)).not.toThrow();
});

it("limits retained history and ignores unallocated machines", () => {
  for (let vmid = 1; vmid <= 25; vmid += 1) recordMachineUse("alice", vmid);
  recordMachineUse("alice", null);
  recordMachineUse("alice", "invalid");
  expect(readRecentMachines("alice")).toHaveLength(20);
  expect(readRecentMachines("alice").some((entry) => entry.vmid === 0)).toBe(false);
});
