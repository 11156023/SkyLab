import assert from "node:assert/strict";
import { test } from "node:test";
import { build } from "esbuild";

const bundle = await build({
  entryPoints: ["electron/service/WireGuardTunnelService.ts"],
  bundle: true,
  write: false,
  platform: "node",
  format: "esm",
  plugins: [
    {
      name: "fake-desktop-runtime",
      setup(builder) {
        builder.onResolve(
          { filter: /^(electron|.*\/core\/(BeanFactory|Logger))$/ },
          args => ({ path: args.path, namespace: "fake" })
        );
        builder.onLoad({ filter: /.*/, namespace: "fake" }, args => ({
          contents:
            args.path === "electron"
              ? "export const app = {}, BrowserWindow = {}, Notification = class {}, safeStorage = {};"
              : args.path.endsWith("BeanFactory")
                ? "export default {getBean: () => ({})};"
                : "export default {info(){},warn(){},error(){},debug(){}};"
        }));
      }
    }
  ]
});
const { default: Tunnel } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`
);
const deferred = () => {
  let resolve;
  const promise = new Promise(done => {
    resolve = done;
  });
  return { promise, resolve };
};

test("repeated starts coalesce and stop waits for start", async () => {
  const tunnel = new Tunnel();
  const entered = deferred(),
    finish = deferred();
  const calls = [];
  tunnel._startTunnel = async () => {
    calls.push("start");
    entered.resolve();
    await finish.promise;
  };
  tunnel._stopTunnel = async () => {
    calls.push("stop");
  };
  const first = tunnel.startTunnel();
  const second = tunnel.startTunnel();
  await entered.promise;
  const stop = tunnel.stopTunnel();
  assert.deepEqual(calls, ["start"]);
  finish.resolve();
  await Promise.all([first, second, stop]);
  assert.deepEqual(calls, ["start", "stop"]);
});

test("status waits for startup instead of reporting an orphan", async () => {
  const tunnel = new Tunnel();
  const entered = deferred(),
    finish = deferred();
  tunnel.isRunning = async () => true;
  tunnel._readLatestHandshake = async () => null;
  tunnel._startTunnel = async () => {
    entered.resolve();
    await finish.promise;
    tunnel._lastStartTime = Date.now();
    tunnel._expiresAt = Date.now() + 60000;
  };
  const start = tunnel.startTunnel();
  await entered.promise;
  const status = tunnel.getStatus();
  finish.resolve();
  await start;
  assert.equal((await status).running, true);
  assert.equal((await status).connectionError, null);
});

test("a stop during handshake inspection discards the stale running snapshot", async () => {
  const tunnel = new Tunnel();
  const inspected = deferred(),
    finish = deferred();
  let running = true;
  tunnel._lastStartTime = Date.now();
  tunnel.isRunning = async () => running;
  tunnel._readLatestHandshake = async () => {
    inspected.resolve();
    await finish.promise;
    return 123;
  };
  tunnel._stopTunnel = async () => {
    running = false;
    tunnel._lastStartTime = -1;
  };
  const status = tunnel.getStatus();
  await inspected.promise;
  await tunnel.stopTunnel();
  finish.resolve();
  assert.equal((await status).running, false);
});

test("renewal failure warns while lease is valid; expiry still blocks", async () => {
  const tunnel = new Tunnel();
  tunnel._lastStartTime = Date.now();
  tunnel._expiresAt = Date.now() + 60000;
  tunnel.isRunning = async () => true;
  tunnel._readLatestHandshake = async () => null;
  tunnel._refreshTunnelLease = async () => {
    throw new Error("HTTP 502");
  };
  await tunnel.refreshIfRunning("test");
  const status = await tunnel.getStatus();
  assert.equal(status.running, true);
  assert.equal(status.connectionError, null);
  assert.equal(status.leaseRefreshError, "HTTP 502");
  tunnel._expiresAt = Date.now() - 1;
  assert.equal((await tunnel.getStatus()).running, false);
});

test("a genuinely orphaned service still requires reconnecting", async () => {
  const tunnel = new Tunnel();
  tunnel.isRunning = async () => true;
  tunnel._readLatestHandshake = async () => null;
  const status = await tunnel.getStatus();
  assert.equal(status.running, false);
  assert.match(status.connectionError, /earlier app session/);
});
