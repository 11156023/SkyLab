import assert from "node:assert/strict";
import path from "node:path";
import { beforeEach, test } from "node:test";
import { build } from "esbuild";

// 真的 store 與 ipcUtils；IPC、提示訊息與 router 換成假的
const toasts = [];
const listeners = new Map();
const sent = [];
const fakeWindow = new EventTarget();
fakeWindow.electronIpcRenderer = {
  send: (channel, args) => sent.push({ channel, args }),
  on: (channel, handler) => listeners.set(channel, handler),
  removeListener: () => {},
  removeAllListeners: () => {}
};
globalThis.window = fakeWindow;
globalThis.localStorage = {
  getItem: () => null,
  setItem: () => {}
};
globalThis.__skylabToasts = toasts;

const src = path.join(process.cwd(), "src");
const bundle = await build({
  stdin: {
    contents: [
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
      name: "fake-renderer-runtime",
      setup(builder) {
        builder.onResolve({ filter: /^@\// }, args => ({
          path: path.join(src, `${args.path.slice(2)}.ts`)
        }));
        builder.onResolve(
          { filter: /^(element-plus|\.\.\/router)$/ },
          args => ({
            path: args.path,
            namespace: "fake"
          })
        );
        builder.onLoad({ filter: /.*/, namespace: "fake" }, args => ({
          contents:
            args.path === "element-plus"
              ? "export const ElMessage = options => globalThis.__skylabToasts.push(options);"
              : 'export default { currentRoute: { value: { name: "Home" } }, replace: async () => {} };'
        }));
      }
    }
  ]
});
const { createPinia, setActivePinia, useAppStore, ipcRouters } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`
);

const reply = (router, response) =>
  listeners.get(`${router.path}:hook`)({}, response);
const statuses = ipcRouters.SESSION.getSessionStatuses;
const backendDown = {
  bizCode: "B1005",
  data: null,
  message: "Backend request failed. net::ERR_CONNECTION_REFUSED"
};

let store;
beforeEach(() => {
  toasts.length = 0;
  listeners.clear();
  setActivePinia(createPinia());
  store = useAppStore();
  store.registerListeners();
  store.loggedIn = true;
});

test("a backend outage during polling shows one quiet indicator, not a toast per poll", () => {
  for (let poll = 0; poll < 3; poll++) reply(statuses, backendDown);

  assert.deepEqual(toasts, []);
  assert.equal(store.serverUnreachable, true);
});

test("the indicator clears on the next successful poll", () => {
  reply(statuses, backendDown);
  reply(statuses, { bizCode: "A1000", data: [], message: "successful." });

  assert.equal(store.serverUnreachable, false);
  assert.deepEqual(toasts, []);
});

test("an expired login is not reported as an unreachable server", () => {
  let expired = 0;
  fakeWindow.addEventListener("skylab:auth-expired", () => expired++);

  reply(statuses, { bizCode: "B1001", data: null, message: "Not logged in." });

  assert.equal(store.serverUnreachable, false);
  assert.ok(expired >= 1);
});

test("stopping the poller (logout) drops a stale indicator", () => {
  reply(statuses, backendDown);
  store.stopSessionPolling();

  assert.equal(store.serverUnreachable, false);
});
