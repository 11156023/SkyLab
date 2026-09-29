import { expect, test } from "vitest";
import { snapshotToKeepOnClose } from "./nginxSnapshot";

test("收合時丟掉失敗的快照，下次展開才會重試", () => {
  expect(snapshotToKeepOnClose({ runtime_error: "connect failed" })).toBeNull();
});

test("成功的快照照樣沿用", () => {
  const ok = { version: "1.27", active: true };
  expect(snapshotToKeepOnClose(ok)).toBe(ok);
  expect(snapshotToKeepOnClose(null)).toBeNull();
});
