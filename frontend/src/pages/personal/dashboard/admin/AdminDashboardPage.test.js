import { describe, expect, it } from "vitest";

/* 待辦的分類與排序改由 adminAttention.js 負責（首頁改成按急迫度分層），
   對應的測試在 adminAttention.test.js。 */
import { countRows, normalizeAssistantPrompt } from "./AdminDashboardPage";

describe("countRows", () => {
  it("supports list and paginated API responses", () => {
    expect(countRows([{}, {}])).toBe(2);
    expect(countRows({ count: 4, data: [] })).toBe(4);
    expect(countRows({ total: 9, items: [] })).toBe(9);
    expect(countRows({ items: [{}, {}, {}] })).toBe(3);
    expect(countRows(null)).toBe(0);
  });
});

describe("admin assistant prompt", () => {
  it("trims the prompt before opening the inline conversation", () => {
    expect(normalizeAssistantPrompt("  幫我檢查節點  ")).toBe("幫我檢查節點");
    expect(normalizeAssistantPrompt(null)).toBe("");
  });
});
