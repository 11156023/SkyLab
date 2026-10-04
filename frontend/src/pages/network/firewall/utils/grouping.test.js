/**
 * grouping.test.js
 * 拓撲分組、收合、篩選：
 *   - 班級機依班級、其他依機器類型分組，摘要（台數／開機／對外開放）正確
 *   - 收合後群組內往外的線併成一條、記條數；群組內部的線不畫
 *   - 搜尋時命中所在的群組強制展開；篩選回報看得到幾台
 *   - 不分組時只拿掉被篩掉的機器與它們的線
 */

import { describe, expect, test, vi } from "vitest";
import { GATEWAY_KEY, buildFlow } from "./buildFlow";
import {
  applyView,
  buildGroups,
  defaultCollapsed,
  groupKeyOf,
  groupKeyOfId,
  groupNodeId,
  listGroupKeys,
  vmidsWithRules,
  COLLAPSE_THRESHOLD,
} from "./grouping";

const vm = (vmid, extra = {}) => ({
  vmid,
  name: `vm-${vmid}`,
  node_type: "vm",
  status: "running",
  ip_address: `10.0.0.${vmid}`,
  machine_kind: "personal",
  ...extra,
});

const TOPOLOGY = {
  nodes: [
    { vmid: null, name: "gateway", node_type: "gateway" },
    vm(101, { machine_kind: "teaching_class", class_relation: "teacher", teaching_class_name: "資二甲" }),
    vm(102, { machine_kind: "teaching_class", class_relation: "teacher", teaching_class_name: "資二甲", status: "stopped" }),
    vm(103, { machine_kind: "teaching_class", class_relation: "teacher", teaching_class_name: "資二甲" }),
    vm(201),
    vm(202),
  ],
  edges: [
    { source_vmid: null, target_vmid: 101, ports: [{ port: 80, protocol: "tcp" }] }, // 對外開放
    { source_vmid: 102, target_vmid: null, ports: [] }, // 上網線
    { source_vmid: 103, target_vmid: null, ports: [] }, // 上網線
    { source_vmid: 101, target_vmid: 102, ports: [{ port: 22, protocol: "tcp" }] }, // 同班內部
    { source_vmid: 201, target_vmid: 101, ports: [{ port: 5432, protocol: "tcp" }] }, // 跨群組
  ],
};

const flow = () => buildFlow(TOPOLOGY, { showLabel: true, showInternet: true });
const CLASS_KEY = "class:資二甲";
const PERSONAL_KEY = "kind:personal";

describe("分組", () => {
  test("班級機依班級，其他依機器類型", () => {
    expect(groupKeyOf(TOPOLOGY.nodes[1])).toBe(CLASS_KEY);
    expect(groupKeyOf(TOPOLOGY.nodes[4])).toBe(PERSONAL_KEY);
    expect(listGroupKeys(TOPOLOGY.nodes)).toEqual([CLASS_KEY, PERSONAL_KEY]);
  });

  test("摘要：台數、開機數、對外開放數", () => {
    const groups = buildGroups(flow().nodes.filter((n) => n.type === "vm"));
    const cls = groups.find((g) => g.key === CLASS_KEY);
    expect(cls).toMatchObject({ total: 3, running: 2, exposed: 1 });
  });

  test("有規則＝扣掉上網線後還有連線", () => {
    expect([...vmidsWithRules(TOPOLOGY.edges)].sort()).toEqual(["101", "102", "201"]);
  });

  test("預設收合：機器多且沒有對外開放才收", () => {
    expect(defaultCollapsed({ exposed: 0 }, COLLAPSE_THRESHOLD + 1)).toBe(true);
    expect(defaultCollapsed({ exposed: 2 }, COLLAPSE_THRESHOLD + 1)).toBe(false);
    expect(defaultCollapsed({ exposed: 0 }, COLLAPSE_THRESHOLD)).toBe(false);
  });
});

describe("applyView 分組模式", () => {
  test("展開：群組框在子節點前面，子節點掛在群組底下", () => {
    const view = applyView(flow(), TOPOLOGY.edges, { grouped: true });
    const groupIndex = view.nodes.findIndex((n) => n.id === groupNodeId(CLASS_KEY));
    const childIndex = view.nodes.findIndex((n) => n.id === "101");
    expect(groupIndex).toBeGreaterThanOrEqual(0);
    expect(childIndex).toBeGreaterThan(groupIndex);
    expect(view.nodes[childIndex]).toMatchObject({ parentId: groupNodeId(CLASS_KEY), draggable: false });
    expect(view.nodes.some((n) => n.id === GATEWAY_KEY)).toBe(true);
  });

  test("收合：往外的線改接群組並記條數，群組內部的線不畫", () => {
    const onExpandGroup = vi.fn();
    const view = applyView(flow(), TOPOLOGY.edges, {
      grouped: true,
      isCollapsed: (g) => g.key === CLASS_KEY,
      onExpandGroup,
    });
    const groupId = groupNodeId(CLASS_KEY);
    expect(view.nodes.some((n) => n.id === "101")).toBe(false);
    /* 102、103 → 網際網路的兩條上網線併成一條 */
    const outbound = view.edges.find((e) => e.source === groupId && e.target === GATEWAY_KEY);
    expect(outbound.data.aggregateCount).toBe(2);
    /* 101 → 102 是同一個群組內部，不畫 */
    expect(view.edges.some((e) => e.source === groupId && e.target === groupId)).toBe(false);
    /* 跨群組的 201 → 101 改接到群組 */
    expect(view.edges.some((e) => e.source === "201" && e.target === groupId)).toBe(true);
    /* 摘要卡的規則數：對外開放→101、101→102、201→101 共三條（上網線不算） */
    expect(view.nodes.find((n) => n.id === groupId).data.rules).toBe(3);
    /* 點合併線＝展開群組 */
    outbound.data.onSelect();
    expect(onExpandGroup).toHaveBeenCalledWith(CLASS_KEY);
  });

  test("搜尋時命中所在的群組強制展開", () => {
    const view = applyView(flow(), TOPOLOGY.edges, {
      grouped: true,
      isCollapsed: () => true,
      filter: { query: "vm-102" },
    });
    expect(view.visibleVms).toBe(1);
    expect(view.nodes.some((n) => n.id === "102")).toBe(true);
    expect(view.nodes.find((n) => n.id === groupNodeId(CLASS_KEY)).data.collapsed).toBe(false);
  });

  test("只有一台的群組不畫框，那台直接排在堆疊裡；篩選後只剩一台的仍保留框", () => {
    const topology = {
      ...TOPOLOGY,
      nodes: [...TOPOLOGY.nodes, vm(301, { machine_kind: "teaching_class", class_relation: "teacher", teaching_class_name: "獨班" })],
    };
    const view = applyView(buildFlow(topology, { showLabel: true, showInternet: true }), topology.edges, {
      grouped: true,
      isCollapsed: () => true,
    });
    expect(view.nodes.some((n) => n.id === groupNodeId("class:獨班"))).toBe(false);
    expect(view.nodes.find((n) => n.id === "301")).toMatchObject({ draggable: false });
    expect(view.nodes.find((n) => n.id === "301").parentId).toBeUndefined();
    /* 資二甲有三台，搜尋只命中一台：框還在，才看得出它是哪一班的 */
    const searched = applyView(flow(), TOPOLOGY.edges, { grouped: true, filter: { query: "vm-102" } });
    expect(searched.nodes.some((n) => n.id === groupNodeId(CLASS_KEY))).toBe(true);
  });

  test("拖曳過的群組位置優先於自動排列", () => {
    const view = applyView(flow(), TOPOLOGY.edges, {
      grouped: true,
      positions: { groups: { [PERSONAL_KEY]: { x: 999, y: 888 } } },
    });
    expect(view.nodes.find((n) => n.id === groupNodeId(PERSONAL_KEY)).position).toEqual({ x: 999, y: 888 });
  });
});

describe("applyView 不分組", () => {
  test("只看對外開放：只留有暴露面的機器與它們的線，並回報台數", () => {
    const view = applyView(flow(), TOPOLOGY.edges, { filter: { onlyExposed: true } });
    expect(view.totalVms).toBe(5);
    /* 只有 101 被網際網路開放 80 port */
    expect(view.visibleVms).toBe(1);
    expect(view.nodes.map((n) => n.id).sort()).toEqual(["101", GATEWAY_KEY].sort());
    expect(view.edges.some((e) => e.source === "103" || e.source === "201")).toBe(false);
  });

  test("篩光了：網際網路節點也不畫，畫布留給空狀態", () => {
    const view = applyView(flow(), TOPOLOGY.edges, { filter: { query: "沒有這台" } });
    expect(view.visibleVms).toBe(0);
    expect(view.nodes).toEqual([]);
    expect(view.edges).toEqual([]);
  });

  test("群組篩選只留該群組", () => {
    const view = applyView(flow(), TOPOLOGY.edges, { filter: { groupKey: PERSONAL_KEY } });
    expect(view.nodes.filter((n) => n.type === "vm").map((n) => n.id).sort()).toEqual(["201", "202"]);
  });
});

describe("群組 id 與搜尋中的收合", () => {
  test("群組 id 不含空白、引號、括號（會進 marker 的 url(#…)），且能還原成 key", () => {
    const key = "class:Network Security (A) 'B'";
    const id = groupNodeId(key);
    expect(id).not.toMatch(/[\s"'()]/);
    expect(groupKeyOfId(id)).toBe(key);
  });

  test("班級名稱含空白：合併邊點下去展開的仍是原本的群組 key", () => {
    const name = "網路 安全";
    const topology = {
      nodes: [
        { vmid: null, name: "gateway", node_type: "gateway" },
        vm(401, { machine_kind: "teaching_class", class_relation: "teacher", teaching_class_name: name }),
        vm(402, { machine_kind: "teaching_class", class_relation: "teacher", teaching_class_name: name }),
      ],
      edges: [{ source_vmid: null, target_vmid: 401, ports: [{ port: 80, protocol: "tcp" }] }],
    };
    const onExpandGroup = vi.fn();
    const view = applyView(buildFlow(topology, { showLabel: true, showInternet: true }), topology.edges, {
      grouped: true,
      isCollapsed: () => true,
      onExpandGroup,
    });
    const agg = view.edges.find((e) => e.data.aggregateCount >= 1);
    expect(agg.id).not.toMatch(/[\s"'()]/);
    agg.data.onSelect();
    expect(onExpandGroup).toHaveBeenCalledWith(`class:${name}`);
  });

  test("搜尋中群組被強制展開，標題不給切收合；沒搜尋時照常", () => {
    const onToggleGroup = vi.fn();
    const searched = applyView(flow(), TOPOLOGY.edges, { grouped: true, filter: { query: "vm-102" }, onToggleGroup });
    expect(searched.nodes.find((n) => n.id === groupNodeId(CLASS_KEY)).data.onToggle).toBeUndefined();
    const plain = applyView(flow(), TOPOLOGY.edges, { grouped: true, onToggleGroup });
    expect(plain.nodes.find((n) => n.id === groupNodeId(CLASS_KEY)).data.onToggle).toBe(onToggleGroup);
  });
});
