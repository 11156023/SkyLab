import { describe, it, expect } from "vitest";
import { buildFlow, focusEdges, isOutboundEdge } from "./buildFlow";

/* 三台機器：一條對外開放、一條對外連線、一條內部互通 */
const topology = {
  nodes: [
    { node_type: "gateway", vmid: null, position_x: 0, position_y: 0 },
    { node_type: "vm", vmid: 101, name: "web", position_x: 200, position_y: 0 },
    { node_type: "vm", vmid: 102, name: "db", position_x: 400, position_y: 0 },
  ],
  edges: [
    { source_vmid: null, target_vmid: 101, ports: [{ port: 80, protocol: "tcp" }] },
    { source_vmid: 101, target_vmid: null, ports: [{ port: 443, protocol: "tcp" }] },
    { source_vmid: 101, target_vmid: 102, ports: [{ port: 5432, protocol: "tcp" }] },
  ],
};

describe("isOutboundEdge", () => {
  it("機器連出去到網際網路才是上網線", () => {
    expect(isOutboundEdge({ source_vmid: 101, target_vmid: null })).toBe(true);
  });

  it("網際網路連進機器是對外開放，不是上網線", () => {
    expect(isOutboundEdge({ source_vmid: null, target_vmid: 101 })).toBe(false);
  });

  it("兩台機器之間的線不是上網線", () => {
    expect(isOutboundEdge({ source_vmid: 101, target_vmid: 102 })).toBe(false);
  });

  it("欄位缺漏時不會誤判成上網線", () => {
    expect(isOutboundEdge({})).toBe(false);
    expect(isOutboundEdge(null)).toBe(false);
  });
});

describe("buildFlow 上網線開關", () => {
  it("預設（未指定）全部顯示", () => {
    const { edges } = buildFlow(topology);
    expect(edges.map((e) => e.hidden)).toEqual([false, false, false]);
  });

  it("關掉時只藏出站線，對外開放與內部互通照畫", () => {
    const { edges } = buildFlow(topology, { showInternet: false });
    expect(edges.map((e) => e.hidden)).toEqual([false, true, false]);
  });

  it("藏起來的邊仍保留原本的 id 與資料，開回來時選取狀態不會斷", () => {
    const shown = buildFlow(topology, { showInternet: true });
    const hidden = buildFlow(topology, { showInternet: false });
    expect(hidden.edges.map((e) => e.id)).toEqual(shown.edges.map((e) => e.id));
    expect(hidden.edges[1].data.edge).toEqual(topology.edges[1]);
  });

  it("對外暴露量不受開關影響：徽章講的是規則，不是畫不畫線", () => {
    const { nodes } = buildFlow(topology, { showInternet: false });
    const web = nodes.find((n) => n.id === "101");
    expect(web.data.exposed_count).toBe(1);
  });
});

describe("buildFlow 邊 id", () => {
  it("前面的連線被移除時，未變動連線的 id 不變（連線面板不會被關掉）", () => {
    const before = buildFlow(topology);
    const after = buildFlow({ ...topology, edges: [topology.edges[2]] });
    const idOf = (flow) =>
      flow.edges.find((e) => e.source === "101" && e.target === "102").id;
    expect(idOf(after)).toBe(idOf(before));
  });

  it("同一組來源→目標重複出現時 id 仍唯一", () => {
    const dup = topology.edges[2];
    const { edges } = buildFlow({ ...topology, edges: [dup, { ...dup }] });
    expect(new Set(edges.map((e) => e.id)).size).toBe(2);
  });
});

describe("focusEdges 選中機器只看相關連線", () => {
  it("沒選機器時原樣回傳", () => {
    const { edges } = buildFlow(topology);
    expect(focusEdges(edges, null)).toBe(edges);
  });

  it("只留任一端是選中機器的線，其餘藏起來", () => {
    const { edges } = buildFlow(topology);
    const hiddenOf = (id) => focusEdges(edges, id).map((e) => e.hidden);
    /* 101 三條都沾到；102 只有內部互通那條 */
    expect(hiddenOf("101")).toEqual([false, false, false]);
    expect(hiddenOf("102")).toEqual([true, true, false]);
  });

  it("上網線開關藏起來的線，選中它的機器也維持藏著", () => {
    const { edges } = buildFlow(topology, { showInternet: false });
    expect(focusEdges(edges, "101").map((e) => e.hidden)).toEqual([false, true, false]);
  });

  it("沒變的線沿用原物件，ReactFlow 不必重繪", () => {
    const { edges } = buildFlow(topology);
    const focused = focusEdges(edges, "102");
    expect(focused[2]).toBe(edges[2]);
  });

  it("收合群組的合併線，另一端是選中機器時照畫", () => {
    const agg = { id: "agg-group:x-102", source: "group:x", target: "102", hidden: false };
    const other = { id: "agg-group:x-103", source: "group:x", target: "103", hidden: false };
    expect(focusEdges([agg, other], "102").map((e) => e.hidden)).toEqual([false, true]);
  });
});
