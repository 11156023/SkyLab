import assert from "node:assert/strict";
import path from "node:path";
import { afterEach, beforeEach, test } from "node:test";
import { build } from "esbuild";

const handlers = new Map();
const sent = [];
const toasts = [];
const fakeWindow = new EventTarget();
fakeWindow.electronIpcRenderer = {
  send: (channel, args) => sent.push({ channel, args }),
  on(channel, handler) {
    if (!handlers.has(channel)) handlers.set(channel, new Set());
    handlers.get(channel).add(handler);
  },
  removeListener: (channel, handler) => handlers.get(channel)?.delete(handler)
};
globalThis.window = fakeWindow;
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.__authTest = {
  openExternal: async () => {},
  toasts
};

const bundle = await build({
  stdin: {
    contents: [
      'export { default as AuthService } from "./electron/service/AuthService";',
      'export { createPinia, setActivePinia } from "pinia";',
      'export { useAppStore } from "./src/store/app";',
      'export { ipcRouters } from "./electron/core/IpcRouter";'
    ].join("\n"),
    resolveDir: process.cwd(),
    loader: "ts"
  },
  bundle: true,
  write: false,
  platform: "node",
  format: "esm",
  plugins: [
    {
      name: "auth-runtime",
      setup(builder) {
        builder.onResolve({ filter: /^@\// }, args => ({
          path: path.join(process.cwd(), "src", `${args.path.slice(2)}.ts`)
        }));
        builder.onResolve(
          { filter: /^(electron|element-plus|\.\.\/router|.*\/core\/Logger)$/ },
          args => ({
            path: args.path,
            namespace: "fake"
          })
        );
        builder.onLoad(
          { filter: /.*/, namespace: "fake" },
          ({ path: name }) => ({
            contents:
              name === "electron"
                ? "export const shell = {openExternal: (...args) => globalThis.__authTest.openExternal(...args)};"
                : name === "element-plus"
                  ? "export const ElMessage = value => globalThis.__authTest.toasts.push(value);"
                  : name === "../router"
                    ? 'export default {currentRoute: {value: {name: "Home"}}, replace: async () => {}};'
                    : "export default {info(){},warn(){},error(){}};"
          })
        );
      }
    }
  ]
});
const { AuthService, createPinia, setActivePinia, useAppStore, ipcRouters } =
  await import(
    `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`
  );

const originalSetTimeout = globalThis.setTimeout;
const originalClearTimeout = globalThis.clearTimeout;
let timers;
let api;
let service;
let results;
let tokens;
let store;
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
};
const start = () => service.startLogin((...args) => results.push(args));
const poll = async () => {
  assert.equal(timers.size, 1);
  const [id, callback] = timers.entries().next().value;
  timers.delete(id);
  await callback();
};
const reply = (channel, data, bizCode = "A1000") => {
  for (const handler of handlers.get(channel) ?? []) {
    handler({}, { bizCode, data, message: "request failed" });
  }
};

beforeEach(() => {
  let nextTimer = 0;
  timers = new Map();
  globalThis.setTimeout = callback => {
    const id = ++nextTimer;
    timers.set(id, callback);
    return id;
  };
  globalThis.clearTimeout = id => timers.delete(id);
  handlers.clear();
  sent.length = 0;
  toasts.length = 0;
  results = [];
  tokens = [];
  globalThis.__authTest.openExternal = async () => {};
  api = {
    requestDeviceCode: async () => ({
      device_code: "code",
      login_url: "https://example.org/login",
      expires_in: 300
    }),
    pollDeviceCode: async () => ({ status: "pending" })
  };
  service = new AuthService(
    api,
    { setTokens: async (...pair) => tokens.push(pair) },
    {}
  );
  setActivePinia(createPinia());
  store = useAppStore();
  store.registerListeners();
});

afterEach(() => {
  globalThis.setTimeout = originalSetTimeout;
  globalThis.clearTimeout = originalClearTimeout;
});

test("repeated start requests create only one login flow", async () => {
  await start();
  await start();
  assert.equal(service.isLoginInProgress(), true);
  assert.equal(timers.size, 1);
});

test("browser denial ends login and permits a fresh retry", async () => {
  api.pollDeviceCode = async () => ({ status: "denied" });
  await start();
  await poll();
  assert.equal(service.isLoginInProgress(), false);
  assert.deepEqual(results, [[false, undefined, "B1003"]]);
  await start();
  assert.equal(timers.size, 1);
});

test("cancelling while the device code is loading never opens the browser", async () => {
  const request = deferred();
  let opened = 0;
  api.requestDeviceCode = () => request.promise;
  globalThis.__authTest.openExternal = async () => opened++;
  const pending = start();
  service.cancelLogin();
  request.resolve({
    device_code: "old",
    login_url: "https://example.org/login",
    expires_in: 300
  });
  await pending;
  assert.equal(opened, 0);
  assert.equal(timers.size, 0);
  assert.deepEqual(results, []);
});

test("a cancelled in-flight approval cannot log in or replace a new attempt", async () => {
  const request = deferred();
  api.pollDeviceCode = () => request.promise;
  await start();
  const pending = poll();
  service.cancelLogin();
  await start();
  request.resolve({ status: "approved", accessToken: "old-token" });
  await pending;
  assert.equal(service.isLoginInProgress(), true);
  assert.equal(timers.size, 1);
  assert.deepEqual(tokens, []);
  assert.deepEqual(results, []);
});

test("a stale poll failure cannot terminate the next login", async () => {
  const request = deferred();
  api.pollDeviceCode = () => request.promise;
  await start();
  const pending = poll();
  service.cancelLogin();
  await start();
  request.reject(new Error("old request failed"));
  await pending;
  assert.equal(service.isLoginInProgress(), true);
  assert.deepEqual(results, []);
});

test("approval saves one token pair and emits one result", async () => {
  api.pollDeviceCode = async () => ({
    status: "approved",
    accessToken: "access",
    refreshToken: "refresh"
  });
  await start();
  await poll();
  assert.deepEqual(tokens, [["access", "refresh"]]);
  assert.deepEqual(results, [[true, undefined, undefined]]);
  assert.equal(timers.size, 0);
});

test("browser-open failure and timeout both release the login lock", async () => {
  globalThis.__authTest.openExternal = async () => {
    throw new Error("cancelled");
  };
  await assert.rejects(start(), /cancelled/);
  assert.equal(service.isLoginInProgress(), false);
  globalThis.__authTest.openExternal = async () => {};
  api.requestDeviceCode = async () => ({
    device_code: "expired",
    login_url: "https://example.org/login",
    expires_in: -1
  });
  await start();
  await poll();
  assert.equal(results[0][2], "B1002");
  assert.equal(service.isLoginInProgress(), false);
});

test("global auth listeners register once and repeated success connects once without toasts", () => {
  store.registerListeners();
  store.startLogin();
  store.startLogin();
  reply("auth:event", { type: "login-success" });
  reply("auth:event", { type: "login-success" });
  assert.equal(handlers.get("auth:event").size, 1);
  assert.equal(
    sent.filter(item => item.channel === ipcRouters.AUTH.startLogin.path)
      .length,
    1
  );
  assert.equal(
    sent.filter(item => item.channel === ipcRouters.TUNNEL.start.path).length,
    1
  );
  assert.equal(store.loggedIn, true);
  assert.deepEqual(toasts, []);
});

test("cancel and failure leave the renderer ready to retry without duplicate error toasts", () => {
  store.startLogin();
  reply("auth:event", { type: "login-cancelled" });
  assert.equal(store.loginInProgress, false);
  assert.equal(store.loginError, "");
  store.startLogin();
  reply(`${ipcRouters.AUTH.startLogin.path}:hook`, null, "B1000");
  assert.equal(store.loginInProgress, false);
  assert.equal(store.loginError, "request failed");
  assert.deepEqual(toasts, []);
  store.startLogin();
  assert.equal(store.loginInProgress, true);
  assert.equal(store.loginError, "");
  store.cancelLogin();
  reply("auth:event", { type: "login-success" });
  assert.equal(store.loggedIn, false);
  assert.equal(sent.at(-1).channel, ipcRouters.AUTH.cancelLogin.path);
});
