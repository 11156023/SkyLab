import assert from "node:assert/strict";
import { test } from "node:test";
import { build } from "esbuild";

const bundle = await build({
  entryPoints: ["src/utils/resourceAccess.ts"],
  bundle: true,
  write: false,
  platform: "node",
  format: "esm"
});
const { canConnectResource, resourceTargets, resourceWindowBlock } =
  await import(
    `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`
  );
const resource = { vmid: 205, status: "running", can_control: true };
const ssh = { vmid: 205, host: "192.168.60.105", port: 22, service: "ssh" };
const now = Date.parse("2026-10-04T10:00:00Z");

test("authorized SSH and RDP targets retain their assigned port", () => {
  const rdp = { ...ssh, service: "rdp", port: 3389 };
  assert.deepEqual(resourceTargets(resource, [ssh, rdp]), [ssh, rdp]);
  assert.equal(canConnectResource(resource, ssh, true, now), true);
  assert.equal(canConnectResource(resource, rdp, true, now), true);
});
test("disconnected, stopped, read-only and placeholders cannot launch", () => {
  assert.equal(canConnectResource(resource, ssh, false, now), false);
  for (const patch of [
    { status: "stopped" },
    { can_control: false },
    { vmid: null },
    { is_placeholder: true }
  ]) {
    assert.equal(
      canConnectResource({ ...resource, ...patch }, ssh, true, now),
      false
    );
  }
});
test("access closes exactly at the deadline without waiting for resource polling", () => {
  const timed = { ...resource, window_end_at: new Date(now).toISOString() };
  assert.equal(canConnectResource(timed, ssh, true, now - 1), true);
  assert.equal(canConnectResource(timed, ssh, true, now), false);
  assert.equal(resourceWindowBlock(timed, now), "window_ended");
});
test("future windows and backend denials cannot be bypassed", () => {
  assert.equal(
    canConnectResource(
      { ...resource, window_start_at: new Date(now + 1000).toISOString() },
      ssh,
      true,
      now
    ),
    false
  );
  assert.equal(
    canConnectResource(
      { ...resource, start_blocked_reason: "window_ended" },
      ssh,
      true,
      now
    ),
    false
  );
});
test("date-only resource expiry follows the backend UTC cutoff", () => {
  const expiring = { ...resource, expiry_date: "2026-10-04" };
  assert.equal(
    canConnectResource(expiring, ssh, true, Date.parse("2026-10-03T23:59:59Z")),
    true
  );
  assert.equal(
    canConnectResource(expiring, ssh, true, Date.parse("2026-10-04T00:00:00Z")),
    false
  );
});
test("unrelated machines, unsupported services and invalid targets are excluded", () => {
  const invalid = [
    { ...ssh, vmid: 206 },
    { ...ssh, port: 0 },
    { ...ssh, port: 65536 },
    { ...ssh, port: 22.5 },
    { ...ssh, host: "" },
    { ...ssh, host: "evil.example" },
    { ...ssh, host: "999.168.60.105" },
    { ...ssh, service: "http" }
  ];
  assert.deepEqual(resourceTargets(resource, invalid), []);
  for (const target of invalid)
    assert.equal(canConnectResource(resource, target, true, now), false);
});
