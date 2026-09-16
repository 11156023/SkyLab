import { describe, it, expect } from "vitest";
import { buildFlow, isInternetEdge } from "./buildFlow";

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

describe("isInternetEdge", () => {
  it("任一端是網際網路就算上網線", () => {
    expect(isInternetEdge({ source_vmid: null, target_vmid: 101 })).toBe(true);
    expect(isInternetEdge({ source_vmid: 101, target_vmid: null })).toBe(true);
  });

  it("兩台機器之間的線不是上網線", () => {
    expect(isInternetEdge({ source_vmid: 101, target_vmid: 102 })).toBe(false);
  });
});

describe("buildFlow 上網線開關", () => {
  it("預設（未指定）全部顯示", () => {
    const { edges } = buildFlow(topology);
    expect(edges.map((e) => e.hidden)).toEqual([false, false, false]);
  });

  it("關掉時只藏上網線，內部互通照畫", () => {
    const { edges } = buildFlow(topology, { showInternet: false });
    expect(edges.map((e) => e.hidden)).toEqual([true, true, false]);
  });

  it("藏起來的邊仍保留原本的 id 與資料，開回來時選取狀態不會斷", () => {
    const shown = buildFlow(topology, { showInternet: true });
    const hidden = buildFlow(topology, { showInternet: false });
    expect(hidden.edges.map((e) => e.id)).toEqual(shown.edges.map((e) => e.id));
    expect(hidden.edges[0].data.edge).toEqual(topology.edges[0]);
  });

  it("對外暴露量不受開關影響：徽章講的是規則，不是畫不畫線", () => {
    const { nodes } = buildFlow(topology, { showInternet: false });
    const web = nodes.find((n) => n.id === "101");
    expect(web.data.exposed_count).toBe(1);
  });
});
