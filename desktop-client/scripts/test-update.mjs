import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { promises as fs } from "node:fs";
import { dirname } from "node:path";
import { test } from "node:test";
import { build } from "esbuild";

globalThis.updateTest = {
  download: null,
  redirectUrl: "",
  followed: 0,
  opened: []
};
const bundle = await build({
  entryPoints: ["electron/service/UpdateService.ts"],
  bundle: true,
  write: false,
  platform: "node",
  format: "esm",
  plugins: [
    {
      name: "fake-electron",
      setup(builder) {
        builder.onResolve({ filter: /^electron$/ }, () => ({
          path: "electron",
          namespace: "fake"
        }));
        builder.onLoad({ filter: /.*/, namespace: "fake" }, () => ({
          contents: `import { EventEmitter } from "node:events";
          import { PassThrough } from "node:stream";
          export const app = { getVersion: () => "0.2.0-beta.9" };
          export const net = { request: () => {
            const request = new EventEmitter();
            request.setHeader = () => {};
            request.followRedirect = () => { request.followed = true; globalThis.updateTest.followed += 1; };
            request.abort = () => { request.aborted = true; };
            request.end = () => queueMicrotask(() => {
              if (globalThis.updateTest.redirectUrl) {
                request.emit("redirect", 302, "GET", globalThis.updateTest.redirectUrl, {});
                if (!request.followed && !request.aborted) {
                  request.emit("error", new Error("Redirect was cancelled"));
                  return;
                }
              }
              if (request.aborted) return;
              const response = new PassThrough();
              response.statusCode = 200;
              response.headers = {"content-length": String(globalThis.updateTest.download.length)};
              request.emit("response", response);
              response.end(globalThis.updateTest.download);
            });
            return request;
          }};
          export const shell = { openPath: async path => { globalThis.updateTest.opened.push(path); return ""; } };`
        }));
      }
    }
  ]
});
const {
  default: UpdateService,
  isNewerVersion,
  selectNewestInstallableRelease
} = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`
);

const payload = Buffer.from("verified installer test payload");
const digest = `sha256:${createHash("sha256").update(payload).digest("hex")}`;
const release = (version, overrides = {}) => ({
  tag_name: `v${version}`,
  draft: false,
  assets: [
    {
      name: "SkyLab-Connect-Setup.exe",
      browser_download_url:
        "https://github.com/1Ray0/SkyLab-Connect-Releases/releases/download/test/SkyLab-Connect-Setup.exe",
      digest,
      size: payload.length,
      state: "uploaded"
    }
  ],
  ...overrides
});

test("published prereleases are ordered numerically and incomplete releases are ignored", () => {
  assert.equal(isNewerVersion("v0.2.0-beta.10", "0.2.0-beta.7"), true);
  const result = selectNewestInstallableRelease([
    release("0.2.0-beta.8"),
    release("0.2.0-beta.10", { draft: true }),
    release("0.2.0-beta.9", { assets: [] }),
    release("0.2.0-beta.7")
  ]);
  assert.equal(result.tag_name, "v0.2.0-beta.8");
});

test("GitHub redirect is followed, verified download is opened and progress is reported", async () => {
  const service = new UpdateService();
  service.fetchReleases = async () => [release("0.2.0-beta.10")];
  globalThis.updateTest.download = payload;
  globalThis.updateTest.redirectUrl =
    "https://release-assets.githubusercontent.com/installer";
  globalThis.updateTest.followed = 0;
  const stages = [];
  await service.install(progress => stages.push(progress.stage));
  const path = globalThis.updateTest.opened.pop();
  try {
    assert.deepEqual(await fs.readFile(path), payload);
    assert.equal(globalThis.updateTest.followed, 1);
    assert.ok(stages.includes("downloading"));
    assert.ok(stages.includes("verifying"));
    assert.ok(stages.includes("launching"));
  } finally {
    await fs.rm(dirname(path), { recursive: true, force: true });
  }
});

test("tampered download is rejected before opening", async () => {
  const service = new UpdateService();
  service.fetchReleases = async () => [release("0.2.0-beta.10")];
  globalThis.updateTest.download = Buffer.alloc(payload.length);
  globalThis.updateTest.redirectUrl =
    "https://release-assets.githubusercontent.com/installer";
  const openedBefore = globalThis.updateTest.opened.length;
  await assert.rejects(
    service.install(() => {}),
    /integrity check failed/
  );
  assert.equal(globalThis.updateTest.opened.length, openedBefore);
});

test("redirects to untrusted hosts are rejected before downloading", async () => {
  const service = new UpdateService();
  service.fetchReleases = async () => [release("0.2.0-beta.10")];
  globalThis.updateTest.download = payload;
  globalThis.updateTest.redirectUrl = "https://example.invalid/installer.exe";
  globalThis.updateTest.followed = 0;
  const openedBefore = globalThis.updateTest.opened.length;
  await assert.rejects(
    service.install(() => {}),
    /untrusted host/
  );
  assert.equal(globalThis.updateTest.followed, 0);
  assert.equal(globalThis.updateTest.opened.length, openedBefore);
});
