import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { after, test } from "node:test";
import { build } from "esbuild";

// 用真的 nedb 檔案（暫存目錄）；electron 只換掉 app 路徑與 safeStorage。
const dataDir = mkdtempSync(path.join(tmpdir(), "skylab-settings-"));
globalThis.__skylabTestDataDir = dataDir;
after(() => rmSync(dataDir, { recursive: true, force: true }));

const bundle = await build({
  stdin: {
    contents: [
      'export { default as SettingsRepository } from "./electron/repository/SettingsRepository";',
      'export { default as SettingsService } from "./electron/service/SettingsService";'
    ].join("\n"),
    resolveDir: process.cwd(),
    loader: "ts"
  },
  bundle: true,
  write: false,
  platform: "node",
  format: "esm",
  // nedb 是 CommonJS，會 require Node 內建模組；data: URL 模組沒有 require 可用
  banner: {
    js: [
      'import { createRequire as __createRequire } from "node:module";',
      `const require = __createRequire(${JSON.stringify(path.join(process.cwd(), "package.json"))});`
    ].join("\n")
  },
  plugins: [
    {
      name: "fake-desktop-runtime",
      setup(builder) {
        builder.onResolve(
          { filter: /^(electron|.*\/core\/Logger)$/ },
          args => ({ path: args.path, namespace: "fake" })
        );
        builder.onLoad({ filter: /.*/, namespace: "fake" }, args => ({
          contents:
            args.path === "electron"
              ? [
                  "export const app = {",
                  "  getPath: () => globalThis.__skylabTestDataDir,",
                  "  setLoginItemSettings() {}",
                  "};",
                  "export const safeStorage = {",
                  "  isEncryptionAvailable: () => true,",
                  '  encryptString: value => Buffer.from("enc:" + value),',
                  "  decryptString: buffer => buffer.toString().slice(4)",
                  "};"
                ].join("\n")
              : "export default {info(){},warn(){},error(){},debug(){}};"
        }));
      }
    }
  ]
});
const { SettingsRepository, SettingsService } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`
);

// 所有測試共用同一個 nedb 檔；每個測試先把紀錄重設成已知狀態
const repo = new SettingsRepository();
const service = new SettingsService(repo);
const reset = async () => {
  await repo.truncate();
  await service.save({
    language: "zh-TW",
    backendUrl: "https://campus.example.org",
    launchAtStartup: false
  });
  await service.setTokens("access-1", "refresh-1");
};

// 讓 nedb 每次讀取都多等一個 macrotask，放大「讀出 → 寫回」之間的空檔
const slowReads = () => {
  const original = repo.db.findOne.bind(repo.db);
  repo.db.findOne = (query, callback) =>
    original(query, (err, doc) => setTimeout(() => callback(err, doc), 5));
  return () => {
    repo.db.findOne = original;
  };
};

test("saving settings during a token refresh keeps the rotated tokens", async () => {
  await reset();
  const restore = slowReads();
  try {
    await Promise.all([
      service.save({ language: "ja" }),
      service.setTokens("access-2", "refresh-2"),
      service.save({ launchAtStartup: true })
    ]);
  } finally {
    restore();
  }
  const settings = await service.get();
  assert.equal(settings.token, "access-2");
  assert.equal(settings.refreshToken, "refresh-2");
  assert.equal(settings.language, "ja");
  assert.equal(settings.launchAtStartup, true);
});

test("a token refresh never rewrites ordinary settings", async () => {
  await reset();
  const restore = slowReads();
  try {
    await Promise.all([
      service.setTokens("access-3", "refresh-3"),
      service.save({ language: "en-US" })
    ]);
  } finally {
    restore();
  }
  const settings = await service.get();
  assert.equal(settings.language, "en-US");
  assert.equal(settings.refreshToken, "refresh-3");
});

test("tokens stay encrypted at rest and settings saves leave them untouched", async () => {
  await reset();
  await service.save({ language: "ja" });
  const stored = await repo.findById("1");
  assert.match(stored.token, /^safe:v1:/);
  assert.match(stored.refreshToken, /^safe:v1:/);
  assert.equal(await service.getRefreshToken(), "refresh-1");
});

test("changing the backend clears tokens; keeping it does not", async () => {
  await reset();
  await service.save({ backendUrl: "https://campus.example.org/" });
  assert.equal(await service.getToken(), "access-1");
  await service.save({ backendUrl: "https://other.example.org" });
  const settings = await service.get();
  assert.equal(settings.backendUrl, "https://other.example.org");
  assert.equal(settings.token, "");
  assert.equal(settings.refreshToken, "");
});

test("a failed write does not block later writes", async () => {
  await reset();
  await assert.rejects(
    service.save({ backendUrl: "http://insecure.example.org" })
  );
  const original = repo.db.update.bind(repo.db);
  repo.db.update = (_query, _update, _options, callback) =>
    callback(new Error("disk full"));
  try {
    await assert.rejects(service.setTokens("x", "y"), /disk full/);
  } finally {
    repo.db.update = original;
  }
  await service.setTokens("access-4", "refresh-4");
  assert.equal(await service.getRefreshToken(), "refresh-4");
});
