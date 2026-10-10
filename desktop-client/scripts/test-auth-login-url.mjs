import assert from "node:assert/strict";
import { test } from "node:test";
import { build } from "esbuild";

globalThis.openedLoginUrl = "";
const bundle = await build({
  // BusinessError 跟 AuthService 打包在同一份，測試建的錯誤 instanceof 才會成立
  stdin: {
    contents: [
      'export { default } from "./electron/service/AuthService";',
      'export { BusinessError, ResponseCode } from "./electron/core/BusinessError";'
    ].join("\n"),
    resolveDir: ".",
    loader: "ts"
  },
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
const {
  default: AuthService,
  BusinessError,
  ResponseCode
} = await import(
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

test("an expired device login reports the login-timeout code", async () => {
  const service = new AuthService(
    {
      requestDeviceCode: async () => ({
        device_code: "test-code",
        expires_in: 0
      })
    },
    { getBackendUrl: async () => "https://skylab-tw.com" },
    {}
  );
  const result = await new Promise((resolve, reject) => {
    service
      .startLogin((success, error) => resolve({ success, error }))
      .catch(reject);
  });
  assert.equal(result.success, false);
  assert.equal(result.error.bizCode, "B1002");
});

const failedPollResult = async error => {
  const service = new AuthService(
    {
      requestDeviceCode: async () => ({
        device_code: "test-code",
        expires_in: 300
      }),
      pollDeviceCode: async () => {
        throw error;
      }
    },
    { getBackendUrl: async () => "https://skylab-tw.com" },
    {}
  );
  return new Promise((resolve, reject) => {
    service
      .startLogin((success, failure) => resolve({ success, failure }))
      .catch(reject);
  });
};

test("a failed poll keeps the backend error code for the UI", async () => {
  const unreachable = await failedPollResult(
    new BusinessError(ResponseCode.BACKEND_UNREACHABLE, "net::ERR_INTERNET_DISCONNECTED")
  );
  assert.equal(unreachable.success, false);
  assert.equal(unreachable.failure.bizCode, "B1017");

  const unexpected = await failedPollResult(new SyntaxError("Unexpected token <"));
  assert.equal(unexpected.failure.bizCode, "B1000");
});
