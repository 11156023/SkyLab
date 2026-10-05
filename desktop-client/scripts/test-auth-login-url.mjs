import assert from "node:assert/strict";
import { test } from "node:test";
import { build } from "esbuild";

globalThis.openedLoginUrl = "";
const bundle = await build({
  entryPoints: ["electron/service/AuthService.ts"],
  bundle: true,
  write: false,
  platform: "node",
  format: "esm",
  plugins: [
    {
      name: "fake-auth-runtime",
      setup(builder) {
        builder.onResolve({ filter: /^electron$/ }, () => ({
          path: "electron",
          namespace: "fake"
        }));
        builder.onResolve({ filter: /\/core\/Logger$/ }, () => ({
          path: "Logger",
          namespace: "fake"
        }));
        builder.onLoad({ filter: /.*/, namespace: "fake" }, args => ({
          contents:
            args.path === "electron"
              ? "export const shell = {openExternal: async url => {globalThis.openedLoginUrl = url;}};"
              : "export default {warn(){},error(){}};"
        }));
      }
    }
  ]
});
const { default: AuthService } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`
);

test("device login uses the configured site when backend returns an old login URL", async () => {
  const service = new AuthService(
    {
      requestDeviceCode: async () => ({
        device_code: "test-code",
        login_url: "https://skylab.ntubimdbirc.tw/login?device_code=test-code",
        expires_in: 300
      })
    },
    { getBackendUrl: async () => "https://skylab-tw.com" },
    {}
  );
  try {
    await service.startLogin(() => {});
    assert.equal(
      globalThis.openedLoginUrl,
      "https://skylab-tw.com/login?device_code=test-code"
    );
  } finally {
    service.cancelLogin();
  }
});
