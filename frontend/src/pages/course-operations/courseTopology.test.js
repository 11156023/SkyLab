import { describe, expect, test } from "vitest";
import { COURSE_MACHINE_NODE_SIZE, frozenTopologyLayout, LABEL_CLEARANCE_X, LABEL_CLEARANCE_Y, spreadForEdgeLabels } from "./courseTopology";

const box = (id, x, y) => ({ id, position: { x, y }, width: 180, height: 68 });

describe("spreadForEdgeLabels", () => {
  test("同一列靠太近的兩台推開到放得下標籤，最左邊那台不動", () => {
    const spread = spreadForEdgeLabels([box("debian", 61, 120), box("rocky", 316, 123)]);

    expect(spread.get("debian")).toEqual({ x: 61, y: 120 });
    expect(spread.get("rocky").x - (61 + 180)).toBe(LABEL_CLEARANCE_X);
    expect(spread.get("rocky").y).toBe(123);
  });

  test("間距已經夠的排法原封不動", () => {
    const boxes = [box("a", 0, 0), box("b", 180 + LABEL_CLEARANCE_X + 40, 10)];
    const spread = spreadForEdgeLabels(boxes);

    expect(spread.get("a")).toEqual({ x: 0, y: 0 });
    expect(spread.get("b")).toEqual({ x: 180 + LABEL_CLEARANCE_X + 40, y: 10 });
  });

  test("推開的量帶到右邊所有節點，上下對齊的欄不會被拆散", () => {
    const spread = spreadForEdgeLabels([box("a", 0, 0), box("c", 100, 0), box("d", 100, 300)]);

    expect(spread.get("c").x).toBe(180 + LABEL_CLEARANCE_X);
    expect(spread.get("d").x).toBe(spread.get("c").x);
  });

  test("不同列的節點左右靠近不用推開", () => {
    const spread = spreadForEdgeLabels([box("top", 0, 0), box("bottom", 60, 300)]);

    expect(spread.get("bottom")).toEqual({ x: 60, y: 300 });
  });

  test("同一欄上下太近時往下推開", () => {
    const spread = spreadForEdgeLabels([box("web", 0, 0), box("db", 20, 80)]);

    expect(spread.get("db").y - 68).toBe(LABEL_CLEARANCE_Y);
    expect(spread.get("db").x).toBe(20);
  });

  test("不改動傳進來的座標物件", () => {
    const boxes = [box("a", 0, 0), box("b", 50, 0)];
    spreadForEdgeLabels(boxes);

    expect(boxes[1].position).toEqual({ x: 50, y: 0 });
  });
});

describe("frozenTopologyLayout", () => {
  const machine = (id, x, y) => ({ id, position: { x, y } });

  test("機器照課程節點寬度推開，網際網路放在最右邊那台右側、跟第一列同高", () => {
    const { positions, internet } = frozenTopologyLayout([machine("debian", 60, 130), machine("rocky", 320, 120)]);
    const width = COURSE_MACHINE_NODE_SIZE.width;

    expect(positions.get("debian")).toEqual({ x: 60, y: 130 });
    expect(positions.get("rocky").x).toBe(60 + width + LABEL_CLEARANCE_X);
    expect(internet).toEqual({ x: positions.get("rocky").x + width + LABEL_CLEARANCE_X, y: 120 });
  });

  test("沒有機器時網際網路節點仍有預設位置", () => {
    expect(frozenTopologyLayout([]).internet).toEqual({ x: LABEL_CLEARANCE_X, y: 95 });
  });
});
