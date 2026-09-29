import { expect, test } from "vitest";
import { pickDefaultNode } from "./setupDefaults";

const hostB = [
  { name: "pve-b1", is_primary: false },
  { name: "pve-b2", is_primary: true },
];

test("換主機再測：舊主機的節點不在新清單中，改用新主節點", () => {
  expect(pickDefaultNode("pve-a1", hostB)).toBe("pve-b2");
});

test("原本選的節點仍存在就保留", () => {
  expect(pickDefaultNode("pve-b1", hostB)).toBe("pve-b1");
});

test("沒有主節點標記時用第一個；沒有節點時為空字串", () => {
  expect(pickDefaultNode("", [{ name: "only" }])).toBe("only");
  expect(pickDefaultNode("x", [])).toBe("");
  expect(pickDefaultNode("x", undefined)).toBe("");
});
