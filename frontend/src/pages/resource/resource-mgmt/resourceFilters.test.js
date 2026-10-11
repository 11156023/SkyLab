import { describe, expect, test } from "vitest";
import { filterGroups, filterResources, statusCounts } from "./resourceFilters";

const list = [
  { vmid: 101, name: "web-01", status: "running", ip_address: "10.0.0.5", owner_name: "王小明", owner_email: "ming@example.com" },
  { vmid: 102, name: "db-01", status: "stopped", ip_address: "10.0.0.6", owner_name: "李小華", owner_email: "hua@example.com" },
  { vmid: 203, name: "lab", status: "paused", ip_address: null, owner_name: "王小明" },
];

describe("filterResources", () => {
  test("沒條件時全部保留", () => {
    expect(filterResources(list)).toHaveLength(3);
    expect(filterResources(list, { query: "   ", status: "all" })).toHaveLength(3);
  });

  test("搜尋比對名稱、VMID、IP、擁有者（不分大小寫）", () => {
    const vmids = (query) => filterResources(list, { query }).map((r) => r.vmid);
    expect(vmids("WEB")).toEqual([101]);
    expect(vmids("203")).toEqual([203]);
    expect(vmids("10.0.0.6")).toEqual([102]);
    expect(vmids("王小明")).toEqual([101, 203]);
    expect(vmids("hua@")).toEqual([102]);
  });

  test("多個詞都要命中", () => {
    expect(filterResources(list, { query: "王小明 10.0" }).map((r) => r.vmid)).toEqual([101]);
    expect(filterResources(list, { query: "王小明 db" })).toEqual([]);
  });

  test("狀態切換只看對應的狀態，跟搜尋一起套用", () => {
    expect(filterResources(list, { status: "running" }).map((r) => r.vmid)).toEqual([101]);
    expect(filterResources(list, { status: "stopped" }).map((r) => r.vmid)).toEqual([102]);
    expect(filterResources(list, { query: "王", status: "stopped" })).toEqual([]);
  });
});

describe("filterGroups", () => {
  const groups = [{
    id: "course-1",
    title: "資料庫系統",
    machines: [
      { vmid: 301, name: "db-a", status: "running", ip: "10.1.0.1", ownerName: "王小明" },
      { vmid: 302, name: "db-b", status: "stopped", ip: "10.1.0.2", ownerName: "李小華" },
    ],
  }];

  test("群組名稱命中時整組都留", () => {
    expect(filterGroups(groups, { query: "資料庫" })[0].machines).toHaveLength(2);
  });

  test("只有部分機器符合時，群組留著、只列符合的那幾台", () => {
    const [match] = filterGroups(groups, { query: "李小華" });
    expect(match.group.id).toBe("course-1");
    expect(match.machines.map((m) => m.vmid)).toEqual([302]);
    expect(filterGroups(groups, { status: "running" })[0].machines.map((m) => m.vmid)).toEqual([301]);
  });

  test("沒有機器符合的群組不列", () => {
    expect(filterGroups(groups, { query: "web" })).toEqual([]);
  });
});

test("statusCounts 依狀態切換數台數", () => {
  expect(statusCounts(list)).toEqual({ all: 3, running: 1, stopped: 1 });
});
