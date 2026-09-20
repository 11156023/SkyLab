/**
 * topologyNodes.test.js
 * 拓撲節點進連線對話框前的過濾與命名：唯讀的課堂機不能當連線端點，
 * 老師開放的機器只能當目標並帶允許的埠，別人的機器要帶擁有者才分得出來。
 */

import { describe, expect, test } from "vitest";
import { canConnectNode, canManageNode, isPeerNode, nodeLabel, toDialogNodes } from "./topologyNodes";

const gateway = { vmid: null, name: "Internet", node_type: "gateway" };
const mine = { vmid: 100, name: "demo", node_type: "vm" };
const studentVm = {
  vmid: 201,
  name: "web-01",
  node_type: "vm",
  can_manage: true,
  owner_name: "王小明",
  machine_kind: "teaching_class",
  class_relation: "teacher",
  teaching_class_name: "網路概論",
};
const readOnlyClassVm = {
  vmid: 202,
  name: "web-01",
  node_type: "vm",
  can_manage: false,
  can_connect: false,
  teaching_class_name: "網路概論",
};
const teacherPeer = {
  vmid: 100,
  name: "demo-server",
  node_type: "vm",
  can_manage: false,
  can_connect: true,
  allowed_ports: [{ port: 80, protocol: "tcp" }],
  owner_name: "陳老師",
  teaching_class_name: "網路概論",
};

describe("節點身分", () => {
  test("後端沒帶欄位的舊格式視為可管理、可連線", () => {
    expect(canManageNode(mine)).toBe(true);
    expect(canConnectNode(mine)).toBe(true);
    expect(isPeerNode(mine)).toBe(false);
  });

  test("唯讀課堂機不能管也不能連", () => {
    expect(canManageNode(readOnlyClassVm)).toBe(false);
    expect(canConnectNode(readOnlyClassVm)).toBe(false);
    expect(isPeerNode(readOnlyClassVm)).toBe(false);
  });

  test("老師開放的機器是可連不可管", () => {
    expect(isPeerNode(teacherPeer)).toBe(true);
    expect(isPeerNode(null)).toBe(false);
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
  test("排除網關與唯讀節點，保留可管理的學生機器與老師開放的機器", () => {
    const list = toDialogNodes([gateway, mine, studentVm, readOnlyClassVm, teacherPeer]);
    expect(list).toEqual([
      {
        key: "100",
        vmid: 100,
        name: "demo",
        kindLabelKey: "MachineKind.personal",
        peerOnly: false,
        allowedPorts: null,
      },
      {
        key: "201",
        vmid: 201,
        name: "web-01 · 王小明",
        kindLabelKey: "MachineKind.classStudent",
        peerOnly: false,
        allowedPorts: null,
      },
      {
        key: "100",
        vmid: 100,
        name: "demo-server · 陳老師",
        kindLabelKey: "MachineKind.teacherOpen",
        peerOnly: true,
        allowedPorts: [{ port: 80, protocol: "tcp" }],
      },
    ]);
  });

  test("老師開放的機器沒帶埠清單時給空陣列，不會是 null", () => {
    const [node] = toDialogNodes([{ ...teacherPeer, allowed_ports: undefined }]);
    expect(node.allowedPorts).toEqual([]);
  });

  test("空拓撲回空陣列", () => {
    expect(toDialogNodes(undefined)).toEqual([]);
  });
});
