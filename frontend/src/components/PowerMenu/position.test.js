import { describe, expect, test } from "vitest";
import {
  ANCHOR_GAP,
  EDGE_MARGIN,
  MENU_WIDTH,
  computePosition,
  isAnchorOffscreen,
} from "./position";

const VIEWPORT = { width: 1280, height: 800 };
const MENU_HEIGHT = 180;

/** 產生一個 30x30 錨點按鈕的 rect（模擬 ⋮ 按鈕） */
function anchorAt(top, right = 900) {
  return { top, bottom: top + 30, right };
}

describe("computePosition", () => {
  test("下方空間足夠時往下展開，貼在按鈕下緣", () => {
    const rect = anchorAt(200);
    const pos = computePosition(rect, MENU_HEIGHT, VIEWPORT);

    expect(pos.openUp).toBe(false);
    expect(pos.top).toBe(rect.bottom + ANCHOR_GAP);
  });

  test("下方放不下且上方較寬裕時往上翻，貼在按鈕上緣", () => {
    const rect = anchorAt(700); // bottom 730，下方只剩 70px
    const pos = computePosition(rect, MENU_HEIGHT, VIEWPORT);

    expect(pos.openUp).toBe(true);
    expect(pos.top).toBe(rect.top - ANCHOR_GAP - MENU_HEIGHT);
  });

  test("align 為 left 時左緣對齊錨點（輸入框的建議選單）", () => {
    const rect = { top: 200, bottom: 236, left: 300, right: 430 };
    const pos = computePosition(rect, MENU_HEIGHT, VIEWPORT, 280, "left");

    expect(pos.left).toBe(300);
  });

  test("align 為 left 且右側放不下時夾回視窗內", () => {
    const rect = { top: 200, bottom: 236, left: 1100, right: 1230 };
    const pos = computePosition(rect, MENU_HEIGHT, VIEWPORT, 280, "left");

    expect(pos.left).toBe(VIEWPORT.width - 280 - EDGE_MARGIN);
  });

  test("右緣對齊錨點按鈕", () => {
    const pos = computePosition(anchorAt(200, 900), MENU_HEIGHT, VIEWPORT);

    expect(pos.left).toBe(900 - MENU_WIDTH);
  });

  test("錨點靠左時不會被推出視窗左緣", () => {
    const pos = computePosition(anchorAt(200, 40), MENU_HEIGHT, VIEWPORT);

    expect(pos.left).toBe(EDGE_MARGIN);
  });

  test("錨點貼右時不會超出視窗右緣", () => {
    const pos = computePosition(anchorAt(200, VIEWPORT.width), MENU_HEIGHT, VIEWPORT);

    expect(pos.left + MENU_WIDTH).toBeLessThanOrEqual(VIEWPORT.width - EDGE_MARGIN);
  });

  test("上下都塞不下時夾在視窗內，不會被截斷", () => {
    const tallMenu = 700;
    const pos = computePosition(anchorAt(400), tallMenu, VIEWPORT);

    expect(pos.top).toBeGreaterThanOrEqual(EDGE_MARGIN);
    expect(pos.top + tallMenu).toBeLessThanOrEqual(VIEWPORT.height - EDGE_MARGIN);
  });

  test("錨點在最上方時即使下方不夠也不往上翻（上方更擠）", () => {
    const pos = computePosition(anchorAt(10), 760, VIEWPORT);

    expect(pos.openUp).toBe(false);
  });
});

describe("isAnchorOffscreen", () => {
  test("錨點在視窗內回傳 false", () => {
    expect(isAnchorOffscreen(anchorAt(200), VIEWPORT)).toBe(false);
  });

  test("錨點被往上捲出視窗回傳 true", () => {
    expect(isAnchorOffscreen({ top: -60, bottom: -30, right: 900 }, VIEWPORT)).toBe(true);
  });

  test("錨點還在視窗下方外回傳 true", () => {
    expect(isAnchorOffscreen(anchorAt(VIEWPORT.height + 20), VIEWPORT)).toBe(true);
  });
});
