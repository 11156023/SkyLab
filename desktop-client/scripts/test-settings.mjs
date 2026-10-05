import assert from "node:assert/strict";
import { test } from "node:test";
import { build } from "esbuild";

const bundle = await build({
  entryPoints: ["electron/repository/SettingsRepository.ts"],
  bundle: true,
  write: false,
  platform: "node",
  format: "esm",
  plugins: [
    {
      name: "fake-settings-storage",
      setup(builder) {
        builder.onResolve({ filter: /^electron$/ }, () => ({
          path: "electron",
          namespace: "fake"
        }));
        builder.onResolve({ filter: /\/BaseRepository$/ }, () => ({
          path: "BaseRepository",
          namespace: "fake"
        }));
        builder.onLoad({ filter: /.*/, namespace: "fake" }, args => ({
          contents:
            args.path === "electron"
              ? `export const safeStorage = {
              isEncryptionAvailable: () => true,
              encryptString: value => Buffer.from(value),
              decryptString: value => value.toString()
            };`
              : `export default class BaseRepository {
              async findById() { return this.record; }
              async updateById(_id, value) { this.record = value; return value; }
            }`
        }));
      }
    }
  ]
});
const { default: SettingsRepository } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`
);

for (const oldUrl of [
  "https://skylab.ntubimdbirc.tw",
  "https://skylab.ntubimdbirc.tw/",
  "http://localhost:8000"
]) {
  test(`former default ${oldUrl} migrates and clears old credentials`, async () => {
    const repository = new SettingsRepository();
    repository.record = {
      _id: "1",
      backendUrl: oldUrl,
      token: "old-token",
      refreshToken: "old-refresh",
      language: "zh-TW"
    };
    const settings = await repository.get();
    assert.equal(settings.backendUrl, "https://skylab-tw.com");
    assert.equal(settings.token, "");
    assert.equal(settings.refreshToken, "");
    assert.equal(repository.record.backendUrl, "https://skylab-tw.com");
  });
}

test("a user configured server is preserved", async () => {
  const repository = new SettingsRepository();
  repository.record = {
    _id: "1",
    backendUrl: "https://custom.example",
    token: "my-token",
    refreshToken: "my-refresh",
    language: "zh-TW"
  };
  const settings = await repository.get();
  assert.equal(settings.backendUrl, "https://custom.example");
  assert.equal(settings.token, "my-token");
  assert.equal(settings.refreshToken, "my-refresh");
});
