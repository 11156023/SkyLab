import assert from "node:assert/strict";
import { createServer } from "node:net";
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
const {
  default: Tunnel,
  elevatedCommand,
  elevatedExitError,
  windowsArgument
} = await import(
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
  const expired = await tunnel.getStatus();
  assert.equal(expired.running, false);
  assert.equal(expired.connectionErrorCode, "B1013");
});

test("a genuinely orphaned service still requires reconnecting", async () => {
  const tunnel = new Tunnel();
  tunnel.isRunning = async () => true;
  tunnel._readLatestHandshake = async () => null;
  const status = await tunnel.getStatus();
  assert.equal(status.running, false);
  assert.match(status.connectionError, /earlier app session/);
  assert.equal(status.connectionErrorCode, "B1014");
});

test("declining UAC is reported separately from a failed admin action", () => {
  assert.equal(elevatedExitError(0), null);
  assert.equal(elevatedExitError(1223).bizCode, "B1011");
  const failed = elevatedExitError(1603);
  assert.equal(failed.bizCode, "B1012");
  assert.match(failed.message, /exit code 1603/);
  assert.equal(elevatedExitError(null).bizCode, "B1012");
});

test("arguments with spaces or quotes follow Windows command-line quoting", () => {
  assert.equal(windowsArgument("/qn"), "/qn");
  assert.equal(
    windowsArgument(String.raw`C:\Program Files\SkyLab Connect\w.msi`),
    String.raw`"C:\Program Files\SkyLab Connect\w.msi"`
  );
  assert.equal(windowsArgument('say "hi"'), String.raw`"say \"hi\""`);
  assert.equal(windowsArgument("C:\\trailing dir\\"), String.raw`"C:\trailing dir\\"`);
  assert.equal(windowsArgument(""), '""');
});

test("elevated command runs as admin and maps the UAC cancel to exit 1223", () => {
  const command = elevatedCommand(String.raw`C:\Program Files\WireGuard\wireguard.exe`, [
    "/installtunnelservice",
    String.raw`C:\Users\O'Brien Lee\SkyLab.conf`
  ]);
  assert.ok(command.includes(String.raw`$psi.FileName = 'C:\Program Files\WireGuard\wireguard.exe';`));
  assert.ok(
    command.includes(String.raw`$psi.Arguments = '/installtunnelservice "C:\Users\O''Brien Lee\SkyLab.conf"';`)
  );
  assert.ok(command.includes("$psi.Verb = 'runas';"));
  assert.match(command, /NativeErrorCode -eq 1223\) \{ exit 1223 \}/);
  assert.match(command, /exit \$p\.ExitCode$/);
});

test("a changed network configuration asks for a reconnect with its own code", async () => {
  const tunnel = new Tunnel();
  tunnel._lastStartTime = Date.now();
  tunnel._expiresAt = Date.now() + 60_000;
  tunnel._activeConfigFingerprint = "before";
  tunnel.isRunning = async () => true;
  tunnel._readLatestHandshake = async () => ({ at: null, unavailable: true });
  tunnel._probeAuthorizedTargets = async () => false;
  tunnel._loadIdentity = () => ({ deviceId: "device-1" });
  tunnel._SkyLabService = { refreshWireGuard: async () => ({}) };
  tunnel._configFingerprint = () => "after";
  await assert.rejects(tunnel.refreshTunnel(), error => error.bizCode === "B1015");
  const status = await tunnel.getStatus();
  assert.equal(status.connectionErrorCode, "B1015");
});

test("reachable authorized SSH target confirms the tunnel when wg inspection is denied", async () => {
  const tunnel = new Tunnel();
  tunnel._lastStartTime = Date.now();
  tunnel._expiresAt = Date.now() + 60_000;
  tunnel._connections = [
    { vmid: 106, service: "ssh", host: "192.168.60.106", port: 22 }
  ];
  tunnel.isRunning = async () => true;
  tunnel._readLatestHandshake = async () => ({ at: null, unavailable: true });
  tunnel._probeAuthorizedTargets = async () => true;

  const status = await tunnel.getStatus();
  assert.equal(status.running, true);
  assert.equal(status.connected, true);
  assert.equal(status.handshakeUnavailable, true);
  assert.equal(status.latestHandshakeAt, null);
});

test("unreadable handshake without reachable targets does not claim a connection", async () => {
  const tunnel = new Tunnel();
  tunnel._lastStartTime = Date.now();
  tunnel._expiresAt = Date.now() + 60_000;
  tunnel._connections = [
    { vmid: 106, service: "ssh", host: "192.168.60.106", port: 22 }
  ];
  tunnel.isRunning = async () => true;
  tunnel._readLatestHandshake = async () => ({ at: null, unavailable: true });
  tunnel._probeAuthorizedTargets = async () => false;

  const status = await tunnel.getStatus();
  assert.equal(status.running, true);
  assert.equal(status.connected, false);
  assert.equal(status.handshakeUnavailable, true);
});

test("authorized target probe detects an open TCP port", async () => {
  const server = createServer(socket => socket.end());
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  try {
    const address = server.address();
    const tunnel = new Tunnel();
    assert.equal(
      await tunnel._probeTarget({ host: "127.0.0.1", port: address.port }),
      true
    );
  } finally {
    await new Promise(resolve => server.close(resolve));
  }
});
