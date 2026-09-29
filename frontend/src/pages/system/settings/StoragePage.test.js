import { describe, expect, test } from "vitest";
import { PRIORITY_MAX, PRIORITY_MIN, parsePriorityDraft } from "./StoragePage";

describe("StoragePage 使用者優先度草稿解析", () => {
  test("多位數整數在範圍內照送（不會只存到第一個數字）", () => {
    expect(parsePriorityDraft("10")).toBe(10);
    expect(parsePriorityDraft(" 7 ")).toBe(7);
    expect(parsePriorityDraft(PRIORITY_MIN)).toBe(PRIORITY_MIN);
  });

  test("清空、負號、小數、非數字都不送出（不會被當成 0 存下）", () => {
    expect(parsePriorityDraft("")).toBeNull();
    expect(parsePriorityDraft("-")).toBeNull();
    expect(parsePriorityDraft("-3")).toBeNull();
    expect(parsePriorityDraft("2.5")).toBeNull();
    expect(parsePriorityDraft("abc")).toBeNull();
    expect(parsePriorityDraft(null)).toBeNull();
  });

  test("超出後端允許範圍（1–10）不送出", () => {
    expect(parsePriorityDraft("0")).toBeNull();
    expect(parsePriorityDraft(String(PRIORITY_MAX + 1))).toBeNull();
    expect(parsePriorityDraft("15")).toBeNull();
  });
});
