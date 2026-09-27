import { expect, test } from "vitest";
import { pruneSelection } from "./courseCmsSelection";

const rows = [{ id: "a" }, { id: "b" }];

test("選取的項目已被刪除時清掉選取", () => {
  expect(pruneSelection([{ id: "b" }], "a")).toBeNull();
});

test("選取的項目仍在清單中則保留", () => {
  expect(pruneSelection(rows, "b")).toBe("b");
});

test("沒有選取或清單為空時回傳 null", () => {
  expect(pruneSelection(rows, null)).toBeNull();
  expect(pruneSelection([], "a")).toBeNull();
  expect(pruneSelection(undefined, "a")).toBeNull();
});
