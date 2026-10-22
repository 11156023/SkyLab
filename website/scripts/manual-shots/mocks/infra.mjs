// Shared data for the infrastructure admin pages (pve-connections, storage,
// gpu-management, monitoring, ldap): the school's PVE clusters, nodes and storages.
import { D, H, MIN, NOW, ago } from "../lib/mock.mjs";

export const GB = 1024 ** 3;

// common.mjs's fakeJwt expires in 2100: the app's refresh timer delay overflows
// setTimeout (> 24.8 days) and fires at once, so it refreshes in a tight loop and
// the page never goes network-idle. Hand out tokens that expire in a week instead.
const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
export function stableTokens(m) {
  m.use([
    ["POST", /^\/api\/v1\/login\/refresh-token$/, () => {
      const exp = Math.floor(NOW / 1000) + 7 * 86400;
      const jwt = `${b64({ alg: "HS256", typ: "JWT" })}.${b64({ sub: m.currentUser().id, exp, type: "access" })}.c2lnbmF0dXJl`;
      return { access_token: jwt, refresh_token: "rt." + (m.state.role || "student") };
    }],
  ]);
}

// ---------------------------------------------------------------- scene helpers

// The content column right of the sidebar, from `top`, `height` px tall.
export const main = (top = 0, height = 900) => ({ x: 336, y: top, width: 1104, height });
export const toastOf = (s) => s.page.locator("[data-sonner-toast]").first();
export async function waitToast(s) {
  await toastOf(s).waitFor({ timeout: 8000 });
  await s.page.waitForTimeout(400);
}
export async function dropToasts(s) {
  await s.page.evaluate(() => document.querySelectorAll("[data-sonner-toast]").forEach((t) => t.remove()));
}

// The framework starts a new frame only when > 4 % of the screen changed. When a
// state change is small (a list becoming empty), paint a block below `fromY` that
// the next shot's crop leaves out, so the change is detected; remove it afterwards.
export async function newFrame(s, fromY) {
  await s.page.evaluate((y) => {
    const d = document.createElement("div");
    d.id = "__infra_frame_break";
    d.style.cssText = `position:fixed;left:0;right:0;top:${y}px;bottom:0;background:#000;z-index:2147483646;pointer-events:none`;
    document.body.appendChild(d);
  }, fromY);
}
export async function endFrame(s) {
  await s.page.evaluate(() => document.getElementById("__infra_frame_break")?.remove());
}

// Three PVE entry points: the main teaching cluster (default), a GPU cluster
// and an old single host that is being retired.
export function connections(m) {
  const base = { api_timeout: 30, pool_name: "SkyLab", iso_storage: "local", task_check_interval: 2, enabled: true, is_default: false, has_ca_cert: false, verify_ssl: false, backup_storage: null, gateway_ip: null, local_subnet: null, default_node: null };
  return [
    { ...base, id: 1, name: m.L("Main Cluster", "主叢集"), host: "172.20.202.11", port: 8006, user: "skylab@pve", data_storage: "ceph-vm", backup_storage: "pbs-backup", gateway_ip: "10.20.0.1", local_subnet: "10.20.0.0/16", default_node: "pve-01", is_default: true, verify_ssl: true, has_ca_cert: true, node_count: 3, updated_at: ago(3 * D) },
    { ...base, id: 2, name: m.L("GPU Cluster", "GPU 叢集"), host: "172.20.202.31", port: 8006, user: "skylab@pve", data_storage: "nvme-lvm", gateway_ip: "10.40.0.1", local_subnet: "10.40.0.0/16", default_node: "gpu-01", node_count: 2, updated_at: ago(10 * D) },
    { ...base, id: 3, name: m.L("Old Lab PVE", "舊機房 PVE"), host: "192.168.50.10", port: 8006, user: "root@pam", data_storage: "local-lvm", enabled: false, node_count: 1, updated_at: ago(60 * D) },
  ];
}

export function nodes() {
  const n = (id, name, host, extra = {}) => ({ id, name, host, port: 8006, is_primary: false, is_online: true, last_checked: ago(MIN), priority: 5, enabled: true, ...extra });
  return [
    n(1, "pve-01", "172.20.202.11", { is_primary: true }),
    n(2, "pve-02", "172.20.202.12"),
    n(3, "pve-03", "172.20.202.13", { priority: 6 }),
    n(4, "gpu-01", "172.20.202.31", { is_primary: true }),
    n(5, "gpu-02", "172.20.202.32", { priority: 4 }),
    n(6, "lab-pve", "192.168.50.10", { is_primary: true, is_online: false, last_checked: ago(6 * H), enabled: false }),
  ];
}

export function storages(m) {
  const main = m.L("Main Cluster", "主叢集");
  const gpu = m.L("GPU Cluster", "GPU 叢集");
  const s = (id, storage, node_name, extra = {}) => ({
    id, storage, node_name, node_names: [node_name], connection_id: 1, connection_name: main, storage_type: "lvmthin",
    total_gb: 1800, used_gb: 620, avail_gb: 1180, can_vm: true, can_lxc: true, can_iso: false, can_backup: false,
    is_shared: false, active: true, enabled: true, speed_tier: "ssd", user_priority: 5, ...extra,
  });
  return [
    s(1, "ceph-vm", "pve-01", { node_names: ["pve-01", "pve-02", "pve-03"], storage_type: "rbd", is_shared: true, total_gb: 21500, used_gb: 9340, avail_gb: 12160, speed_tier: "nvme", user_priority: 1 }),
    s(2, "local-lvm", "pve-01", { total_gb: 870, used_gb: 312, avail_gb: 558, user_priority: 5 }),
    s(3, "local-lvm", "pve-02", { total_gb: 870, used_gb: 498, avail_gb: 372, user_priority: 5 }),
    s(4, "local-lvm", "pve-03", { total_gb: 870, used_gb: 205, avail_gb: 665, user_priority: 5 }),
    s(5, "local", "pve-01", { storage_type: "dir", total_gb: 94, used_gb: 38, avail_gb: 56, can_vm: false, can_lxc: false, can_iso: true, can_backup: true, speed_tier: "ssd", user_priority: 8 }),
    s(6, "pbs-backup", "pve-01", { node_names: ["pve-01", "pve-02", "pve-03"], storage_type: "pbs", is_shared: true, total_gb: 48000, used_gb: 17650, avail_gb: 30350, can_vm: false, can_lxc: false, can_backup: true, speed_tier: "hdd", user_priority: 10 }),
    s(7, "archive-hdd", "pve-03", { storage_type: "lvm", total_gb: 7450, used_gb: 1220, avail_gb: 6230, speed_tier: "hdd", user_priority: 9, enabled: false }),
    s(8, "nvme-lvm", "gpu-01", { connection_id: 2, connection_name: gpu, total_gb: 3570, used_gb: 1490, avail_gb: 2080, speed_tier: "nvme", user_priority: 2 }),
    s(9, "nvme-lvm", "gpu-02", { connection_id: 2, connection_name: gpu, total_gb: 3570, used_gb: 2210, avail_gb: 1360, speed_tier: "nvme", user_priority: 2 }),
    s(11, "old-nfs", "pve-02", { storage_type: "nfs", total_gb: 4096, used_gb: 3870, avail_gb: 226, can_vm: false, can_lxc: false, speed_tier: "hdd", user_priority: 10, enabled: false }),
    s(10, "infortrend-lvm", "gpu-01", { connection_id: 2, connection_name: gpu, node_names: ["gpu-01", "gpu-02"], storage_type: "lvm", is_shared: true, total_gb: 15360, used_gb: 6020, avail_gb: 9340, speed_tier: "unknown", user_priority: 5 }),
  ];
}
