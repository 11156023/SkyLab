/**
 * topologyNodes.test.js
 * 拓撲節點進連線對話框前的過濾與命名：唯讀的課堂機不能當連線端點，
 * 別人的機器要帶擁有者才分得出來。
 */

import { describe, expect, test } from "vitest";
import { canManageNode, nodeLabel, toDialogNodes } from "./topologyNodes";

const gateway = { vmid: null, name: "Internet", node_type: "gateway" };
const mine = { vmid: 100, name: "demo", node_type: "vm" };
const studentVm = {
  vmid: 201,
  name: "web-01",
  node_type: "vm",
  can_manage: true,
  owner_name: "王小明",
  teaching_class_name: "網路概論",
};
const readOnlyClassVm = {
  vmid: 202,
  name: "web-01",
  node_type: "vm",
  can_manage: false,
  teaching_class_name: "網路概論",
};

describe("canManageNode", () => {
  test("後端沒帶 can_manage 的舊格式視為可管理", () => {
    expect(canManageNode(mine)).toBe(true);
    expect(canManageNode(studentVm)).toBe(true);
    expect(canManageNode(readOnlyClassVm)).toBe(false);
  });
});

describe("nodeLabel", () => {
  test("自己的機器只有名稱，別人的機器帶擁有者", () => {
    expect(nodeLabel(mine)).toBe("demo");
    expect(nodeLabel(studentVm)).toBe("web-01 · 王小明");
    expect(nodeLabel(null)).toBe("");
  });
});

describe("toDialogNodes", () => {
  test("排除網關與唯讀節點，保留可管理的學生機器", () => {
    const list = toDialogNodes([gateway, mine, studentVm, readOnlyClassVm]);
    expect(list).toEqual([
      { key: "100", vmid: 100, name: "demo" },
      { key: "201", vmid: 201, name: "web-01 · 王小明" },
    ]);
  });

  test("空拓撲回空陣列", () => {
    expect(toDialogNodes(undefined)).toEqual([]);
  });
});
