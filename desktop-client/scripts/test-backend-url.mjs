import assert from "node:assert/strict";
import { test } from "node:test";
import { build } from "esbuild";

/* 後端網址的規則：設定頁即時提示與主程序存檔共用同一支，兩邊不會各說各話 */
const bundle = await build({
  entryPoints: ["electron/utils/BackendUrlUtils.ts"],
  bundle: true,
  write: false,
  platform: "node",
  format: "esm"
});
const { default: BackendUrlUtils } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`
);
const parse = value => BackendUrlUtils.parse(value);

test("https URLs are accepted and normalized without a trailing slash", () => {
  assert.deepEqual(parse("  https://skylab-tw.com/  "), {
    ok: true,
    url: "https://skylab-tw.com"
  });
  assert.deepEqual(parse("https://skylab-tw.com/base/"), {
    ok: true,
    url: "https://skylab-tw.com/base"
  });
});

test("plain http is only allowed for local testing", () => {
  assert.equal(parse("http://localhost:8000").ok, true);
  assert.equal(parse("http://127.0.0.1:8000").ok, true);
  assert.deepEqual(parse("http://skylab-tw.com"), { ok: false, problem: "insecure" });
});

test("empty, malformed and over-specified URLs report the reason", () => {
  assert.deepEqual(parse("   "), { ok: false, problem: "required" });
  assert.deepEqual(parse("skylab-tw.com"), { ok: false, problem: "invalid" });
  assert.deepEqual(parse("https://user:pw@skylab-tw.com"), { ok: false, problem: "extra" });
  assert.deepEqual(parse("https://skylab-tw.com/?next=1"), { ok: false, problem: "extra" });
  assert.deepEqual(parse("https://skylab-tw.com/#top"), { ok: false, problem: "extra" });
});
