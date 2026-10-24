// Shared data for the network pages (firewall, gateway, domain, ip-management):
// one lab subnet, one fleet of machines and the Cloudflare zones, so the same
// hostnames, IPs and URLs show up on every page.

import { D, H, MIN, ago } from "../lib/mock.mjs";

export const SUBNET = {
  cidr: "10.20.0.0/24",
  gateway: "10.20.0.1",
  gateway_vm_ip: "10.20.0.2",
  platform_ip: "10.20.0.3",
  bridge_name: "vmbr1",
  vlan_tag: 120,
  dns_servers: "140.131.1.1,8.8.8.8",
};
export const GATEWAY_PUBLIC_IP = "140.131.115.20";
export const GATEWAY_HOST = "gw.ntubcloud.tw";

// Cloudflare zones.
export const ZONES = [
  { id: "3f2a91c0d4e5b6a7c8d9e0f1a2b3c4d5", name: "ntubcloud.tw", status: "active" },
  { id: "8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e", name: "imd-lab.dev", status: "active" },
  { id: "c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6", name: "skylab-test.net", status: "pending" },
];

// Machines: [vmid, name, vm_type, ip, owner(en, zh) or null, extra]
export function fleet(m) {
  return [
    { vmid: 201, name: "web-portal", vm_type: "lxc", ip: "10.20.0.21", status: "running" },
    { vmid: 202, name: "mysql-db", vm_type: "lxc", ip: "10.20.0.22", status: "running" },
    { vmid: 203, name: "flask-api", vm_type: "qemu", ip: "10.20.0.23", status: "running" },
    { vmid: 204, name: "jupyter-ml", vm_type: "qemu", ip: "10.20.0.24", status: "running" },
    { vmid: 205, name: "redis-cache", vm_type: "lxc", ip: "10.20.0.25", status: "stopped" },
    {
      vmid: 241, name: "netsec-lab-07", vm_type: "lxc", ip: "10.20.0.41", status: "running",
      machine_kind: "teaching_class", class_relation: "student", can_manage: false,
      teaching_class_name: m.L("Network Security (Mon)", "網路安全實務（週一）"),
    },
  ];
}

// IP allocation rows for the whole subnet view.
export function allocations(m, { extraVms = 0 } = {}) {
  const rows = [
    ["10.20.0.1", "subnet_gateway", null, m.L("Lab subnet gateway", "實驗室網段閘道"), 200 * D],
    ["10.20.0.2", "gateway_vm", null, m.L("Gateway VM (nginx / WireGuard)", "Gateway VM（nginx／WireGuard）"), 200 * D],
    ["10.20.0.3", "platform_host", null, m.L("SkyLab main system", "SkyLab 主系統"), 200 * D],
    ["10.20.0.10", "reserved", null, m.L("Lab printer", "實驗室印表機"), 150 * D],
  ];
  for (const f of fleet(m)) rows.push([f.ip, f.vm_type === "qemu" ? "vm" : "lxc", f.vmid, f.name, 30 * D - f.vmid * H]);
  const courses = [
    ["ubuntu-web", "lxc"], ["kali-attack", "vm"], ["win-server", "vm"], ["docker-host", "lxc"], ["k8s-node", "vm"],
    ["nginx-proxy", "lxc"], ["pg-replica", "lxc"], ["gitlab-runner", "vm"], ["elk-stack", "vm"], ["mqtt-broker", "lxc"],
  ];
  for (let i = 0; i < extraVms; i++) {
    const [n, type] = courses[i % courses.length];
    const host = 50 + i;
    rows.push([`10.20.0.${host}`, type, 300 + i, `${n}-${String(Math.floor(i / courses.length) + 1).padStart(2, "0")}`, (20 - (i % 20)) * D + i * 37 * MIN]);
  }
  for (let i = 0; i < 6; i++) {
    rows.push([`10.20.0.${200 + i}`, "class_reserved", null, m.L(`Class "Cloud Computing (Wed)" seat ${i + 1}`, `班級「雲端運算（週三）」座位 ${i + 1}`), 2 * D + i * MIN]);
  }
  rows.push(["10.20.0.230", "quick_practice_reserved", null, m.L("Quick practice: Linux basics", "快速練習：Linux 基礎"), 3 * H]);
  rows.push(["10.20.0.231", "quick_practice_reserved", null, m.L("Quick practice: Linux basics", "快速練習：Linux 基礎"), 3 * H]);
  return rows.map(([ip_address, purpose, vmid, description, age]) => ({ ip_address, purpose, vmid, description, allocated_at: ago(age) }));
}

// ---------------------------------------------------------------- firewall topology

export const GW = { vmid: null, name: "Internet", node_type: "gateway", position_x: 760, position_y: 250 };

// Student view: own machines plus one read-only class machine.
const POS = { 201: [380, 0], 202: [0, 80], 203: [380, 170], 204: [380, 340], 205: [0, 300], 241: [380, 510] };

export function studentTopology(m) {
  const nodes = [GW, ...fleet(m).map((f) => ({
    vmid: f.vmid, name: f.name, node_type: "vm", vm_type: f.vm_type, status: f.status, ip_address: f.ip,
    firewall_enabled: true, position_x: POS[f.vmid][0], position_y: POS[f.vmid][1],
    can_manage: f.can_manage ?? true, owner_name: null, teaching_class_name: f.teaching_class_name ?? null,
    machine_kind: f.machine_kind ?? "personal", class_relation: f.class_relation ?? null,
  }))];
  const e = (source_vmid, target_vmid, ports, extra = {}) => ({ source_vmid, target_vmid, ports, direction: "one_way", topology_managed: false, ...extra });
  const edges = [
    e(null, 201, [
      { port: 80, protocol: "tcp", domain: "portal.ntubcloud.tw", enable_https: true, external_port: null },
      { port: 22, protocol: "tcp", external_port: 30122, domain: null, enable_https: true },
    ]),
    e(null, 203, [{ port: 5000, protocol: "tcp", external_port: 30500, domain: null, enable_https: true }]),
    e(null, 204, [{ port: 8888, protocol: "tcp", domain: "jupyter-alex.ntubcloud.tw", enable_https: true, external_port: null }]),
    e(null, 241, [{ port: 22, protocol: "tcp", external_port: 31007, domain: null, enable_https: true }], { topology_managed: true }),
    e(201, 202, [{ port: 3306, protocol: "tcp" }]),
    e(203, 202, [{ port: 3306, protocol: "tcp" }]),
    e(203, 205, [{ port: 6379, protocol: "tcp" }], { direction: "bidirectional" }),
    ...[201, 203, 204, 205, 241].map((v) => e(v, null, [])),
  ];
  return { nodes, edges };
}

function rulesFor(vmid) {
  const base = [
    { type: "in", action: "ACCEPT", proto: "tcp", dport: "80", comment: "SkyLab:gateway->201:80/tcp", is_managed: true, dest: "10.20.0.21" },
    { type: "in", action: "ACCEPT", proto: "tcp", dport: "22", comment: "SkyLab:gateway->201:22/tcp", is_managed: true, dest: "10.20.0.21" },
    { type: "in", action: "ACCEPT", proto: "tcp", dport: "22", source: "10.20.0.3", comment: "SkyLab:platform:ssh", is_managed: true },
    { type: "out", action: "DROP", dest: "192.168.100.0/24", comment: "SkyLab:block-extra:3fa9c1", is_managed: true },
    { type: "out", action: "DROP", dest: "140.131.0.0/16", comment: "SkyLab:block-extra:3fa9c1", is_managed: true },
    { type: "out", action: "ACCEPT", proto: "tcp", dport: "3306", dest: "10.20.0.22", comment: "SkyLab:201->202:3306/tcp", is_managed: true },
    { type: "out", action: "ACCEPT", comment: "SkyLab:201->gateway:all", is_managed: true },
    { type: "in", action: "ACCEPT", proto: "icmp", source: "10.20.0.0/24", comment: "Ping from lab subnet", is_managed: false },
    { type: "in", action: "ACCEPT", proto: "tcp", dport: "8080", source: "140.131.78.0/24", comment: "Old staging port", is_managed: false, enable: 0 },
  ];
  return base.map((r, pos) => ({ pos, enable: 1, source: null, dest: null, proto: null, dport: null, sport: null, ...r }));
}

export function firewallMocks(m, topo) {
  m.state.topo = topo;
  m.use([
    ["GET", /^\/api\/v1\/firewall\/topology$/, ({ state }) => state.topo],
    ["PUT", /^\/api\/v1\/firewall\/layout$/, () => ({ message: "ok" })],
    ["GET", /^\/api\/v1\/firewall\/(\d+)\/rules$/, ({ mt }) => rulesFor(Number(mt[1]))],
    ["GET", /^\/api\/v1\/firewall\/(\d+)\/options$/, () => ({ enable: true, policy_in: "DROP", policy_out: "ACCEPT" })],
    ["POST", /^\/api\/v1\/firewall\/connections$/, ({ body }) => ({ ...body, topology_managed: false })],
    ["POST", /^\/api\/v1\/firewall\/(\d+)\/services$/, ({ mt, body, state }) => {
      state.published = { ...body, vmid: Number(mt[1]) };
      return { ...body, url: body.domain ? `${body.enable_https ? "https" : "http"}://${body.domain}` : null, firewall_rule_present: true };
    }],
    ["DELETE", /^\/api\/v1\/firewall\/connections$/, ({ body, state }) => {
      state.topo = { ...state.topo, edges: state.topo.edges.filter((x) => !(x.source_vmid === body.source_vmid && x.target_vmid === body.target_vmid)) };
      return { message: "ok" };
    }],
    ["GET", /^\/api\/v1\/reverse-proxy\/setup-context$/, () => ({
      enabled: true, gateway_ready: true, cloudflare_ready: true, reasons: [],
      zones: ZONES.filter((z) => z.status === "active").map(({ id, name }) => ({ id, name })),
      default_dns_target_type: "A", default_dns_target_value: "140.131.115.20",
    })],
    ["GET", /^\/api\/v1\/reverse-proxy\/domain-availability$/, ({ query }) => ({ domain: query.domain, available: true, reason: null, message: null })],
  ]);
}

