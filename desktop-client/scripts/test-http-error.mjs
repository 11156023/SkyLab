import assert from "node:assert/strict";
import { test } from "node:test";
import { build } from "esbuild";

const bundle = await build({
  entryPoints: ["electron/utils/HttpErrorUtils.ts"],
  bundle: true,
  write: false,
  platform: "node",
  format: "esm"
});
const { default: HttpErrorUtils } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`
);

const nginx504 = [
  "<html>",
  "<head><title>504 Gateway Time-out</title></head>",
  "<body>",
  "<center><h1>504 Gateway Time-out</h1></center>",
  "<hr><center>nginx</center>",
  "</body>",
  "</html>",
  ...Array.from(
    { length: 6 },
    () => "<!-- a padding to disable MSIE and Chrome friendly error page -->"
  )
].join("\r\n");

test("FastAPI detail string is shown as the message", () => {
  assert.equal(
    HttpErrorUtils.describe(409, JSON.stringify({ detail: "Session already extended" })),
    "Session already extended"
  );
});

test("an nginx 504 page becomes a short status message", () => {
  const message = HttpErrorUtils.describe(504, nginx504);
  assert.equal(message, "Server returned 504.");
  assert.doesNotMatch(message, /<|html/i);
});

test("an empty body reports the status code", () => {
  assert.equal(HttpErrorUtils.describe(502, ""), "Server returned 502.");
  assert.equal(HttpErrorUtils.describe(500, "  \r\n "), "Server returned 500.");
});

test("JSON without a detail string falls back to the status code", () => {
  const validation = JSON.stringify({
    detail: [{ loc: ["body", "device_id"], msg: "Field required" }]
  });
  assert.equal(HttpErrorUtils.describe(422, validation), "Server returned 422.");
  assert.equal(HttpErrorUtils.describe(500, "null"), "Server returned 500.");
});

test("plain text is collapsed to one line and capped at 200 characters", () => {
  assert.equal(
    HttpErrorUtils.describe(500, "Internal\r\n  Server Error\n"),
    "Internal Server Error"
  );
  const message = HttpErrorUtils.describe(500, "x".repeat(5000));
  assert.equal(message.length, 200);
  assert.ok(message.endsWith("…"));
});
