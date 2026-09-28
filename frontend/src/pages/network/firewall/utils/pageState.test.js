import { describe, expect, it } from "vitest";
import { mergePendingLayout, topologyView } from "./pageState";

const topology = { nodes: [], edges: [] };

describe("topologyView", () => {
  it("已有拓撲時重新載入仍顯示圖，不卸載", () => {
    expect(topologyView({ loading: true, error: "", topology })).toBe("graph");
  });

  it("首次載入中且還沒有拓撲時顯示轉圈", () => {
    expect(topologyView({ loading: true, error: "", topology: null })).toBe("spinner");
  });

  it("有錯誤時顯示錯誤畫面", () => {
    expect(topologyView({ loading: false, error: "boom", topology: null })).toBe("error");
  });
});

describe("mergePendingLayout", () => {
  const node = (id, x, y) => ({ id, position: { x, y } });

  it("debounce 期間連續拖曳不同節點，兩個節點都會送出", () => {
    const pending = new Map();
    mergePendingLayout(pending, [node("101", 10.4, 20.6)], "gateway");
    mergePendingLayout(pending, [node("gateway", 300, 40)], "gateway");
    expect([...pending.values()]).toEqual([
      { vmid: 101, node_type: "vm", position_x: 10, position_y: 21 },
      { vmid: null, node_type: "gateway", position_x: 300, position_y: 40 },
    ]);
  });

  it("同一節點再次拖曳時以最新位置為準", () => {
    const pending = new Map();
    mergePendingLayout(pending, [node("101", 0, 0)], "gateway");
    mergePendingLayout(pending, [node("101", 50, 60)], "gateway");
    expect([...pending.values()]).toEqual([
      { vmid: 101, node_type: "vm", position_x: 50, position_y: 60 },
    ]);
  });
});
