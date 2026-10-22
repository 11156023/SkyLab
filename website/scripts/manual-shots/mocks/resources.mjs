// Shared mock data for the "resources" group: My Resources and Resource Detail.
// registerResources(m) adds every endpoint both pages call; scenes tweak m.state.
//
// m.state knobs (all optional):
//   list           array of resources returned by /resources/my (default: student machines)
//   requests       vm-requests returned by /vm-requests/my (default: none)
//   sessions       quick practice sessions (raw API shape)
//   quota          /quotas/my-usage body
//   status         { [vmid]: status } overrides set by power actions
//   patch          { [vmid]: {...} } merged into a resource
//   specRequests   spec change requests returned by /spec-change-requests/my
//   snapshots, backups, rules, shares, boot, credentials ... (see the handlers)
import { D, H, MIN, NOW, ago, iso, later } from "../lib/mock.mjs";

const GB = 1024 ** 3;
const MB = 1024 ** 2;
const day = (ms) => iso(ms).slice(0, 10);

export const VM = 10231; // ubuntu-desktop: the main machine of the detail page
export const LXC = 10342; // flask-blog-lab
export const BACKUP_VM = 10318; // jupyter-ml-lab: storage without snapshots, uses backups

export const PLATFORM_PUB = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK3vQ8pXz7d1mN0fR6tYwB2cE4hJ9kL5sU8aV1xW3yZq skylab-vm10231";
export const OWN_PUB = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGm4Wq2Hk8sTzX1vN7bP3rL6yJ0dC5eF9aQ2uM8nR4oK alex@laptop";
export const NEW_PUB = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPz2Lr8Xc5Vn1Qe7Tg3Hb9Kd4Wf6Ys0Ma2Nj8Ru5Io3 alex@lab-pc";
export const PRIVATE_KEY = [
  "-----BEGIN OPENSSH PRIVATE KEY-----",
  "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW",
  "QyNTUxOQAAACCt70PKV8+3dZjdEX0erWMAdnBOIScpC+bFPGlV1sd2WQAAAJjK3m1Hyt5t",
  "RwAAAAtzc2gtZWQyNTUxOQAAACCt70PKV8+3dZjdEX0erWMAdnBOIScpC+bFPGlV1sd2WQ",
  "AAAEC7Qm2yX4p9sLk1vZ8nT3wE6rU0aH5cJ2dF7gB4iK9mOq3vQ8pXz7d1mN0fR6tYwB2c",
  "E4hJ9kL5sU8aV1xW3yZqAAAADnNreWxhYi12bTEwMjMxAQIDBAUGBw==",
  "-----END OPENSSH PRIVATE KEY-----",
].join("\n");

function resource(m, o) {
  return {
    vmid: null, request_id: null, teaching_class_id: null, allocation_scope: "personal", control_policy: "owner",
    name: "", status: "running", node: "pve-01", type: "lxc", is_placeholder: false,
    can_control: true, can_delete: true, can_request_spec_change: true, can_extend: true,
    environment_type: null, os_info: null, guest_os: null, expiry_date: null, ip_address: null,
    ssh_public_key: null, has_login_password: true, cpu: 0.08, maxcpu: 2, mem: 1.2 * GB, maxmem: 4 * GB,
    uptime: 2 * 86400 + 7200, auto_stop_at: null, auto_stop_reason: null, idle_since: null,
    scheduled_deletion_at: null, mining_exempt: false, access_role: "owner", can_manage: true, owner_email: null,
    machine_kind: "personal", start_blocked_reason: null, window_start_at: null, window_end_at: null,
    class_relation: null, owner_name: null, teaching_class_name: null, public_urls: [],
    ...o,
  };
}

// The student's own and shared machines (My Resources list, default).
export function studentMachines(m) {
  return [
    resource(m, {
      vmid: VM, request_id: "req-0001", name: "ubuntu-desktop", type: "qemu", node: "pve-02",
      environment_type: m.L("OS course desktop", "作業系統課程桌面"), os_info: "Ubuntu 24.04 LTS",
      ip_address: "10.20.3.41", maxcpu: 4, mem: 3.1 * GB, maxmem: 8 * GB, cpu: 0.18,
      uptime: 3 * 86400 + 5 * 3600 + 12 * 60, expiry_date: day(NOW + 40 * D),
      window_start_at: ago(44 * D), window_end_at: later(100 * D),
      ssh_public_key: PLATFORM_PUB, public_urls: ["https://os-lab-wang.skylab.ntub.edu.tw"],
      idle_since: ago(5 * H),
    }),
    resource(m, {
      vmid: LXC, request_id: "req-0003", name: "flask-blog-lab", type: "lxc", node: "pve-03",
      environment_type: m.L("Web development", "網頁開發"), os_info: "Ubuntu 24.04 (LXC)",
      ip_address: "10.20.3.112", maxcpu: 2, mem: 0.9 * GB, maxmem: 4 * GB,
      window_start_at: ago(12 * D), window_end_at: later(104 * D),
      public_urls: ["https://blog-wang.skylab.ntub.edu.tw"],
    }),
    resource(m, {
      vmid: BACKUP_VM, request_id: "req-0002", name: "jupyter-ml-lab", type: "lxc", node: "pve-01", status: "stopped",
      environment_type: m.L("Python data science", "Python 資料科學"), os_info: "Ubuntu 22.04 (LXC)",
      ip_address: "10.20.3.77", maxcpu: 4, mem: 0, maxmem: 4 * GB, cpu: 0, uptime: 0,
      window_start_at: ago(60 * D), window_end_at: ago(2 * D), start_blocked_reason: "window_ended",
    }),
    resource(m, {
      vmid: 10466, name: "mysql-team-db", type: "lxc", node: "pve-03",
      environment_type: m.L("Database", "資料庫"), os_info: "Debian 12 (LXC)", ip_address: "10.20.3.135",
      expiry_date: "2027-01-31", machine_kind: "shared", access_role: "shared", can_manage: false, can_delete: false,
      owner_name: m.L("Emily Chang", "張雅婷"), owner_email: "11256003@ntub.edu.tw",
    }),
    resource(m, {
      vmid: 10488, request_id: "req-0005", name: "win11-office", type: "qemu", node: "pve-02", status: "starting",
      environment_type: m.L("Windows desktop", "Windows 桌面"), os_info: "Windows 11 Education",
      ip_address: "10.20.3.150", maxcpu: 4, maxmem: 8 * GB, uptime: 40,
    }),
  ];
}

// Machines of the teacher account (convert-to-template section).
export function teacherMachines(m) {
  return [
    resource(m, {
      vmid: 10512, name: "k8s-demo-master", type: "qemu", node: "pve-02",
      environment_type: m.L("Kubernetes demo", "Kubernetes 示範"), os_info: "Ubuntu 24.04 LTS",
      ip_address: "10.20.5.12", maxcpu: 4, maxmem: 8 * GB, expiry_date: "2027-01-31",
    }),
    resource(m, {
      vmid: 10515, name: "nginx-teaching-demo", type: "lxc", node: "pve-01",
      environment_type: m.L("Web server", "網頁伺服器"), os_info: "Debian 12 (LXC)", ip_address: "10.20.5.15",
      expiry_date: "2027-01-31",
    }),
  ];
}

// A course group (two class machines) and the resources behind a quick practice session.
export function groupResources(m) {
  const cls = {
    teaching_class_id: "c1a55000-0000-4000-8000-0000000000b2", allocation_scope: "teaching_class",
    machine_kind: "teaching_class", class_relation: "student", control_policy: "class_member",
    teaching_class_name: m.L("Linux System Administration (IM 2B)", "Linux 系統管理（資管二乙）"),
    environment_type: m.L("Linux lab", "Linux 實驗環境"), can_delete: false, can_request_spec_change: false, can_extend: false,
  };
  return [
    resource(m, { ...cls, vmid: 30121, name: "lsa-web-11156001", os_info: "web-server", ip_address: "10.30.8.21", node: "pve-04", maxcpu: 2, maxmem: 2 * GB }),
    resource(m, { ...cls, vmid: 30122, name: "lsa-db-11156001", os_info: "db-server", ip_address: "10.30.8.22", node: "pve-04", maxcpu: 1, maxmem: 2 * GB }),
    resource(m, { vmid: 30410, request_id: "qpm-1", name: "docker-lab-a1", machine_kind: "quick_practice", environment_type: m.L("Docker practice", "Docker 練習"), os_info: "Ubuntu 24.04 (LXC)", ip_address: "10.30.9.41", node: "pve-05", can_delete: false, maxcpu: 2, maxmem: 2 * GB }),
    resource(m, { vmid: 30411, request_id: "qpm-2", name: "docker-lab-a2", machine_kind: "quick_practice", environment_type: m.L("Docker practice", "Docker 練習"), os_info: "Ubuntu 24.04 (LXC)", ip_address: "10.30.9.42", node: "pve-05", can_delete: false, maxcpu: 2, maxmem: 2 * GB }),
    resource(m, { vmid: 30412, request_id: "qpm-3", name: "docker-lab-a3", machine_kind: "quick_practice", status: "stopped", environment_type: m.L("Docker practice", "Docker 練習"), os_info: "Ubuntu 24.04 (LXC)", ip_address: "10.30.9.43", node: "pve-05", can_delete: false, maxcpu: 2, maxmem: 2 * GB, cpu: 0, mem: 0, uptime: 0 }),
  ];
}

export function quickSession(m) {
  const mach = (i, name, role, status) => ({
    id: `qpm-node-${i}`, request_id: `qpm-${i}`, vmid: 30409 + i, name, role, resource_type: "lxc",
    os_info: "Ubuntu 24.04 (LXC)", status, ip_address: `10.30.9.${40 + i}`, node: "pve-05",
    public_url: i === 1 ? "https://docker-a1.skylab.ntub.edu.tw" : null,
    forward_endpoints: i === 2 ? [{ protocol: "tcp", internal_port: 22, external_port: 22042, host: null }] : [],
  });
  return {
    id: "qps-7f2c", title: m.L("Docker Compose practice", "Docker Compose 練習"), status: "running", kind_label: "quick_practice",
    environment_id: "qpe-docker", environment_version_id: "qpv-docker-2", expires_at: later(3 * H), created_at: ago(H),
    publication_pending: false,
    machines: [
      mach(1, "docker-lab-a1", m.L("Web front end", "網頁前端"), "running"),
      mach(2, "docker-lab-a2", m.L("API server", "API 伺服器"), "running"),
      mach(3, "docker-lab-a3", m.L("Database", "資料庫"), "stopped"),
    ],
  };
}

function allMachines(m) {
  return [...studentMachines(m), ...teacherMachines(m), ...groupResources(m)];
}

function findResource(m, vmid) {
  const st = m.state;
  const base = (st.list ?? []).find((r) => r.vmid === vmid) ?? allMachines(m).find((r) => r.vmid === vmid);
  if (!base) return null;
  return { ...base, ...(st.patch?.[vmid] ?? {}), ...(st.status?.[vmid] ? { status: st.status[vmid] } : {}) };
}

function snapshots(m) {
  const t = (ms) => Math.floor((NOW - ms) / 1000);
  return [
    { name: "skylab-init", description: m.L("Initial state after provisioning", "建立完成時的初始狀態"), snaptime: t(44 * D) },
    { name: "before-gcc-upgrade", description: m.L("Before upgrading gcc to 14", "升級 gcc 14 前"), snaptime: t(9 * D + 3 * H) },
    { name: "week5-done", description: m.L("Week 5 assignment finished", "第 5 週作業完成"), snaptime: t(2 * D + 6 * H) },
    { name: "mining-20261010-0312", description: m.L("Evidence: suspected mining process", "存證：疑似挖礦程序"), snaptime: t(2 * D + 7 * H) },
  ];
}

function backups(m) {
  const t = (ms) => Math.floor((NOW - ms) / 1000);
  return [
    { volid: "pbs-backup:backup/ct/10318/2026-10-09T08:12:44Z", created_at: t(3 * D + 2 * H), size: 2.4 * GB, description: m.L("Before installing PyTorch", "安裝 PyTorch 前") },
    { volid: "pbs-backup:backup/ct/10318/2026-09-28T13:40:02Z", created_at: t(14 * D), size: 1.9 * GB, description: m.L("Clean environment", "乾淨環境") },
  ];
}

function auditLogs(m) {
  const me = { user_full_name: m.L("Alex Wang", "王小明"), user_email: "11156001@ntub.edu.tw" };
  const sys = { user_full_name: null, user_email: null };
  const rows = [
    // Matches the rest of the page: snapshot times, uptime since 10/09 04:48, two shares.
    [2 * D, me, "firewall_rule_create", m.L("Inbound TCP 22 from 10.20.0.0/16 (SSH from campus)", "進站 TCP 22，來源 10.20.0.0/16（校內 SSH）")],
    [2 * D + 6 * H, me, "snapshot_create", m.L("Created snapshot week5-done", "建立快照 week5-done")],
    [3 * D + 5 * H + 12 * MIN, me, "resource_start", m.L("Started ubuntu-desktop", "啟動 ubuntu-desktop")],
    [3 * D + 11 * H, sys, "resource_shutdown", m.L("Auto shutdown: idle for 6 hours", "自動關機：閒置 6 小時")],
    [4 * D, me, "credential_update", m.L("Login password reset (system generated)", "重設登入密碼（系統產生）")],
    [5 * D, me, "resource_share_update", m.L("Shared with 11256003@ntub.edu.tw", "共享給 11256003@ntub.edu.tw")],
    [7 * D, me, "resource_share_update", m.L("Shared with 11156012@ntub.edu.tw", "共享給 11156012@ntub.edu.tw")],
    [9 * D + 3 * H, me, "snapshot_create", m.L("Created snapshot before-gcc-upgrade", "建立快照 before-gcc-upgrade")],
    [12 * D, me, "spec_change_apply", m.L("CPU 2 → 4 cores, memory 4 GB → 8 GB", "CPU 2 → 4 核，記憶體 4 GB → 8 GB")],
    [12 * D + H, me, "spec_change_request", m.L("Requested CPU 2 → 4 cores, memory 4 GB → 8 GB", "申請 CPU 2 → 4 核，記憶體 4 GB → 8 GB")],
    [44 * D, sys, "vm_create", m.L("Created from template ubuntu-24.04-desktop on pve-02", "由範本 ubuntu-24.04-desktop 在 pve-02 建立")],
  ];
  return rows.map(([ago_, who, action, details], i) => ({
    id: `al-${i}`, created_at: ago(ago_), action, details, ...who, resource_vmid: VM,
  }));
}

function rrd(timeframe) {
  const span = { hour: H, day: D, week: 7 * D, month: 30 * D, year: 365 * D }[timeframe] ?? H;
  const n = 70;
  return Array.from({ length: n }, (_, i) => {
    const t = NOW - span + (span * i) / (n - 1);
    const w = Math.sin(i / 5) + 0.6 * Math.sin(i / 2.3 + 1);
    return {
      time: Math.floor(t / 1000),
      cpu: Math.max(0.02, 0.16 + 0.09 * w + (i > 48 && i < 56 ? 0.35 : 0)),
      mem: (3.0 + 0.25 * w) * GB, maxmem: 8 * GB,
      netin: (90 + 60 * w + (i > 48 && i < 56 ? 420 : 0)) * 1024,
      netout: (25 + 15 * Math.cos(i / 4)) * 1024,
    };
  });
}

export function firewallTopology(m) {
  return {
    nodes: [
      { vmid: null, name: "Internet", node_type: "gateway", position_x: 620, position_y: 160 },
      { vmid: VM, name: "ubuntu-desktop", node_type: "vm", vm_type: "qemu", status: "running", ip_address: "10.20.3.41", firewall_enabled: true, position_x: 300, position_y: 160, can_manage: true, machine_kind: "personal", class_relation: null },
      { vmid: LXC, name: "flask-blog-lab", node_type: "vm", vm_type: "lxc", status: "running", ip_address: "10.20.3.112", firewall_enabled: true, position_x: 20, position_y: 160, can_manage: true, machine_kind: "personal", class_relation: null },
    ],
    edges: [
      { source_vmid: null, target_vmid: VM, ports: [{ port: 80, protocol: "tcp", domain: "os-lab-wang.skylab.ntub.edu.tw" }], direction: "one_way" },
      { source_vmid: VM, target_vmid: null, ports: [], direction: "one_way" },
      { source_vmid: LXC, target_vmid: VM, ports: [{ port: 5432, protocol: "tcp" }], direction: "one_way" },
    ],
  };
}

function firewallRules(m) {
  return [
    { pos: 0, type: "in", action: "ACCEPT", source: null, dest: null, proto: "tcp", dport: "80", enable: 1, comment: "SkyLab: gateway → ubuntu-desktop (os-lab-wang.skylab.ntub.edu.tw)", is_managed: true },
    { pos: 1, type: "in", action: "ACCEPT", source: "10.20.3.112", dest: null, proto: "tcp", dport: "5432", enable: 1, comment: "SkyLab: flask-blog-lab → ubuntu-desktop", is_managed: true },
    { pos: 2, type: "out", action: "ACCEPT", source: null, dest: null, proto: null, dport: null, enable: 1, comment: "SkyLab: internet", is_managed: true },
    { pos: 3, type: "in", action: "ACCEPT", source: "10.20.0.0/16", dest: null, proto: "tcp", dport: "22", enable: 1, comment: m.L("SSH from campus", "校內 SSH"), is_managed: false },
    { pos: 4, type: "in", action: "ACCEPT", source: null, dest: null, proto: "tcp", dport: "8080", enable: 0, comment: m.L("Test web server", "測試用網頁伺服器"), is_managed: false },
  ];
}

function bootOptions(st) {
  return {
    supports_boot_order: true,
    supports_cdrom: true,
    boot_order: st.bootOrder ?? ["scsi0", "ide2", "net0"],
    boot_devices: [
      { key: "scsi0", kind: "disk", description: "local-lvm:vm-10231-disk-0, 32G" },
      { key: "ide2", kind: "cdrom", description: st.iso ? st.iso.split("/").pop() : "none" },
      { key: "net0", kind: "network", description: "virtio, vmbr0" },
      { key: "efidisk0", kind: "other", description: "EFI disk" },
    ],
    cdrom_iso: st.iso ?? null,
    cdrom_slot: "ide2",
    iso_storage: "iso-nfs",
  };
}

const ISO_IMAGES = [
  { volid: "iso-nfs:iso/ubuntu-24.04.1-desktop-amd64.iso", name: "ubuntu-24.04.1-desktop-amd64.iso", size: 5.8 * GB },
  { volid: "iso-nfs:iso/systemrescue-11.02-amd64.iso", name: "systemrescue-11.02-amd64.iso", size: 0.9 * GB },
  { volid: "iso-nfs:iso/debian-12.7.0-amd64-netinst.iso", name: "debian-12.7.0-amd64-netinst.iso", size: 0.63 * GB },
];

export function registerResources(m) {
  const st = m.state;
  const json = (status, body) => ({ __status: status, __body: body });
  const vmidOf = (mt) => Number(mt[1]);
  m.use([
    // ---- list page
    ["GET", /^\/api\/v1\/resources\/my$/, () => (st.list ?? studentMachines(m)).map((r) => findResource(m, r.vmid) && r.vmid ? findResource(m, r.vmid) : r).filter((r) => !(st.deleted ?? []).includes(r.vmid))],
    ["GET", /^\/api\/v1\/quick-practice\/sessions\/my$/, () => st.sessions ?? []],
    ["POST", /^\/api\/v1\/quick-practice\/sessions\/[^/]+\/end$/, () => ({ ...quickSession(m), status: "reclaiming" })],
    ["GET", /^\/api\/v1\/vm-requests\/my$/, () => { const d = st.requests ?? []; return { data: d, count: d.length }; }],
    ["POST", /^\/api\/v1\/vm-requests\/[^/]+\/(cancel|retry)$/, () => ({ message: "ok" })],
    ["GET", /^\/api\/v1\/quotas\/my-usage$/, () => st.quota ?? {
      used_cpu_cores: 10, used_memory_mb: 16384, used_disk_gb: 92, used_instances: 3,
      quota: { max_cpu_cores: 16, max_memory_mb: 32768, max_disk_gb: 200, max_instances: 5 },
    }],
    ["POST", /^\/api\/v1\/resources\/batch$/, ({ body }) => ({ results: (body?.vmids ?? []).map((vmid) => ({ vmid, success: true })) })],
    ["POST", /^\/api\/v1\/resources\/(\d+)\/(start|stop|shutdown|reboot|reset)$/, ({ mt }) => {
      const next = { start: "starting", reboot: "starting", stop: "stopped", shutdown: "stopped", reset: "running" }[mt[2]];
      st.status = { ...(st.status ?? {}), [vmidOf(mt)]: next };
      return { message: "ok" };
    }],
    ["DELETE", /^\/api\/v1\/resources\/(\d+)$/, ({ mt }) => {
      st.deleted = [...(st.deleted ?? []), vmidOf(mt)];
      return json(202, { message: "queued", task_id: "task-del-1" });
    }],
    ["GET", /^\/api\/v1\/vm\/(\d+)\/console$/, ({ mt }) => ({ vmid: vmidOf(mt), ws_url: `/ws/vnc/${mt[1]}/`, ticket: "PVEVNC:6F1A2B3C::skylab", port: "5900", clipboard: st.clipboard !== false, message: "ok" })],

    // ---- detail page
    ["GET", /^\/api\/v1\/resources\/(\d+)$/, ({ mt }) => findResource(m, vmidOf(mt)) ?? json(404, { detail: "Not Found" })],
    ["GET", /^\/api\/v1\/resources\/(\d+)\/ssh-key$/, () => ({
      login_username: "student", login_password: "Tq7#mVx2-pL9w", ssh_private_key: PRIVATE_KEY,
      uses_template_credentials: false, login_password_custom: false, login_password_pending: false,
      ...(st.sshKey ?? {}),
    })],
    ["GET", /^\/api\/v1\/resources\/(\d+)\/template-manual$/, ({ mt }) => (vmidOf(mt) === VM ? {
      count: 2, template_name: "ubuntu-24.04-desktop",
      data: [
        { id: "att-1", filename: m.L("OS-Lab-Guide.pdf", "作業系統實驗手冊.pdf") },
        { id: "att-2", filename: m.L("gcc-quick-reference.pdf", "gcc 速查表.pdf") },
      ],
    } : { count: 0, template_name: null, data: [] })],
    ["GET", /^\/api\/v1\/resources\/(\d+)\/current-stats$/, ({ mt }) => {
      const r = findResource(m, vmidOf(mt));
      const on = r?.status === "running";
      return {
        status: r?.status, cpu: on ? 0.184 : 0, maxcpu: r?.maxcpu ?? 4, mem: on ? 3.1 * GB : 0, maxmem: r?.maxmem ?? 8 * GB,
        disk: on ? 14.2 * GB : 0, maxdisk: 32 * GB, netin: 1.84 * GB, netout: 312 * MB, uptime: on ? r.uptime : 0,
      };
    }],
    ["GET", /^\/api\/v1\/resources\/(\d+)\/stats$/, ({ query }) => ({ data: rrd(query.timeframe) })],
    ["GET", /^\/api\/v1\/resources\/(\d+)\/snapshot-capability$/, ({ mt }) => (vmidOf(mt) === BACKUP_VM
      ? { available: false, reason: "unsupported" } : { available: true, reason: null })],
    ["GET", /^\/api\/v1\/resources\/(\d+)\/backup-capability$/, () => ({ available: true, reason: null, requires_shutdown: true, max_count: 3 })],
    ["GET", /^\/api\/v1\/resources\/(\d+)\/snapshots$/, () => st.snapshots ?? snapshots(m)],
    ["POST", /^\/api\/v1\/resources\/(\d+)\/snapshots$/, () => ({ message: "ok" })],
    ["DELETE", /^\/api\/v1\/resources\/(\d+)\/snapshots\/[^/]+$/, () => ({ message: "ok" })],
    ["POST", /^\/api\/v1\/resources\/(\d+)\/snapshots\/[^/]+\/rollback$/, () => ({ message: "ok" })],
    ["POST", /^\/api\/v1\/resources\/(\d+)\/(reset-to-init|init-snapshot)$/, () => json(202, { message: "ok", task_id: "task-reset-1" })],
    ["GET", /^\/api\/v1\/resources\/(\d+)\/backups$/, () => st.backups ?? backups(m)],
    ["POST", /^\/api\/v1\/resources\/(\d+)\/backups(\/restore)?$/, () => json(202, { message: "queued", task_id: "task-bk-1" })],
    ["DELETE", /^\/api\/v1\/resources\/(\d+)\/backups$/, () => ({ message: "ok" })],
    ["GET", /^\/api\/v1\/resources\/(\d+)\/specs$/, ({ mt }) => {
      const r = findResource(m, vmidOf(mt));
      return { cpu_cores: r?.maxcpu ?? 4, memory_mb: Math.round((r?.maxmem ?? 8 * GB) / MB), disk_gb: 32, resource_type: r?.type ?? "qemu" };
    }],
    ["GET", /^\/api\/v1\/spec-change-requests\/my$/, () => { const d = st.specRequests ?? []; return { data: d, count: d.length }; }],
    ["POST", /^\/api\/v1\/spec-change-requests\/$/, ({ body }) => {
      const r = findResource(m, body.vmid);
      const created = {
        id: "scr-9001", vmid: body.vmid, change_type: body.change_type, reason: body.reason, status: "pending", apply_status: null,
        current_cpu: r?.maxcpu, requested_cpu: body.requested_cpu ?? null,
        current_memory: r ? Math.round(r.maxmem / MB) : null, requested_memory: body.requested_memory ?? null,
        current_disk: 32, requested_disk: body.requested_disk ?? null,
        current_expiry_date: r?.expiry_date ?? null, requested_expiry_date: body.requested_expiry_date ?? null,
        created_at: iso(NOW),
      };
      st.specRequests = [created];
      return created;
    }],
    ["POST", /^\/api\/v1\/spec-change-requests\/[^/]+\/apply$/, () => {
      const req = { ...(st.specRequests?.[0] ?? {}), apply_status: "applying" };
      st.specRequests = [req];
      return { request: req, message: "ok" };
    }],
    ["POST", /^\/api\/v1\/spec-change-requests\/[^/]+\/cancel$/, () => { st.specRequests = []; return { message: "ok" }; }],
    ["GET", /^\/api\/v1\/audit-logs\/resources\/(\d+)$/, ({ query }) => {
      const all = auditLogs(m);
      const total = all.length;
      return { data: Number(query.limit) === 1 ? all.slice(0, 1) : all, count: total };
    }],

    // ---- advanced settings
    ["GET", /^\/api\/v1\/firewall\/(\d+)\/topology$/, () => firewallTopology(m)],
    ["GET", /^\/api\/v1\/firewall\/topology$/, () => firewallTopology(m)],
    ["GET", /^\/api\/v1\/firewall\/(\d+)\/rules$/, () => st.rules ?? firewallRules(m)],
    ["GET", /^\/api\/v1\/firewall\/(\d+)\/options$/, () => ({ enable: true, policy_in: "DROP", policy_out: "ACCEPT" })],
    ["PUT", /^\/api\/v1\/firewall\/(\d+)\/rules\/\d+$/, () => ({ message: "ok" })],
    ["DELETE", /^\/api\/v1\/firewall\/(\d+)\/rules\/\d+$/, () => ({ message: "ok" })],
    ["GET", /^\/api\/v1\/reverse-proxy\/setup-context$/, () => ({ enabled: false, zones: [] })],
    ["GET", /^\/api\/v1\/resources\/(\d+)\/boot-options$/, () => bootOptions(st)],
    ["PUT", /^\/api\/v1\/resources\/(\d+)\/boot-options$/, ({ body }) => {
      if (body.boot_order) st.bootOrder = body.boot_order;
      if (body.cdrom_iso) st.iso = body.cdrom_iso;
      if (body.eject_cdrom) st.iso = null;
      return bootOptions(st);
    }],
    ["GET", /^\/api\/v1\/resources\/(\d+)\/iso-images$/, () => ISO_IMAGES],
    ["GET", /^\/api\/v1\/resources\/(\d+)\/credentials$/, ({ mt }) => {
      const r = findResource(m, vmidOf(mt));
      return {
        username: "student", has_login_password: true, has_custom_login_password: false,
        platform_public_key: PLATFORM_PUB, authorized_keys: st.authorizedKeys ?? [PLATFORM_PUB, OWN_PUB],
        requires_running: r?.type === "lxc", running: r?.status === "running", resource_type: r?.type ?? "qemu", is_windows: false,
        ...(st.credentials ?? {}),
      };
    }],
    ["POST", /^\/api\/v1\/resources\/(\d+)\/credentials\/reset-password$/, ({ body }) => ({
      password: body?.password || "Hv4!rNc8-Wq2z", applied_immediately: false, rebooting: true,
      message: m.L("Password reset. The VM is rebooting to apply it.", "密碼已重設，虛擬機正在重新開機套用。"),
    })],
    ["POST", /^\/api\/v1\/resources\/(\d+)\/credentials\/regenerate-ssh-key$/, () => ({
      ssh_public_key: PLATFORM_PUB, ssh_private_key: PRIVATE_KEY,
      message: m.L("New key pair created. Reboot the VM to apply it.", "已建立新的金鑰對，請重新開機以套用。"),
    })],
    ["POST", /^\/api\/v1\/resources\/(\d+)\/credentials\/authorized-keys$/, ({ body }) => {
      st.authorizedKeys = [...(st.authorizedKeys ?? [PLATFORM_PUB, OWN_PUB]), body.public_key];
      return { authorized_keys: st.authorizedKeys, message: m.L("Key added", "已加入公鑰") };
    }],
    ["DELETE", /^\/api\/v1\/resources\/(\d+)\/credentials\/authorized-keys$/, ({ body }) => {
      st.authorizedKeys = (st.authorizedKeys ?? [PLATFORM_PUB, OWN_PUB]).filter((k) => k !== body?.public_key);
      return { authorized_keys: st.authorizedKeys, message: m.L("Key removed", "已移除公鑰") };
    }],
    ["GET", /^\/api\/v1\/resources\/(\d+)\/shares$/, () => st.shares ?? [
      { id: "sh-1", user_email: "11256003@ntub.edu.tw", user_full_name: m.L("Emily Chang", "張雅婷"), created_at: ago(5 * D) },
      { id: "sh-2", user_email: "11156012@ntub.edu.tw", user_full_name: m.L("Kevin Chen", "陳冠宇"), created_at: ago(2 * D) },
    ]],
    ["POST", /^\/api\/v1\/resources\/(\d+)\/shares$/, () => ({ message: "ok" })],
    ["DELETE", /^\/api\/v1\/resources\/(\d+)\/shares\/[^/]+$/, () => ({ message: "ok" })],
    ["POST", /^\/api\/v1\/resources\/(\d+)\/transfer$/, () => ({ message: "ok" })],
  ]);
}

// ---------------------------------------------------------------- consoles

// noVNC on Playwright's fake WebSocket: copy the handlers to the instance and turn
// Blob messages into ArrayBuffers (run with page.evaluate after the page loaded).
export const VNC_FIX = `(() => {
  if (window.__vncBlobFix) return;
  window.__vncBlobFix = true;
  const W = window.WebSocket;
  const findDesc = (obj, key) => {
    for (let p = Object.getPrototypeOf(obj); p; p = Object.getPrototypeOf(p)) {
      const d = Object.getOwnPropertyDescriptor(p, key);
      if (d) return d;
    }
    return null;
  };
  const Wrap = function (url, protos) {
    const ws = protos === undefined ? new W(url) : new W(url, protos);
    if (!String(url).includes("/ws/vnc/")) return ws;
    let chain = Promise.resolve();
    const conv = (fn) => (e) => {
      chain = chain.then(async () => {
        const data = e && e.data instanceof Blob ? await e.data.arrayBuffer() : e && e.data;
        fn.call(ws, { type: "message", data, target: ws, currentTarget: ws });
      });
    };
    for (const key of ["send", "close"]) {
      const fn = ws[key];
      Object.defineProperty(ws, key, { configurable: true, enumerable: true, writable: true, value: fn.bind(ws) });
    }
    for (const key of ["binaryType", "onerror", "onopen", "onclose"]) {
      const d = findDesc(ws, key);
      let local = key === "binaryType" ? "arraybuffer" : null;
      Object.defineProperty(ws, key, {
        configurable: true, enumerable: true,
        get() { return d && d.get ? d.get.call(ws) : local; },
        set(v) { local = v; if (d && d.set) d.set.call(ws, v); },
      });
    }
    const onmsg = findDesc(ws, "onmessage");
    let handler = null;
    Object.defineProperty(ws, "onmessage", {
      configurable: true, enumerable: true,
      get() { return handler; },
      set(fn) { handler = fn; if (onmsg && onmsg.set) onmsg.set.call(ws, fn ? conv(fn) : null); },
    });
    const realAdd = ws.addEventListener.bind(ws);
    ws.addEventListener = (t, fn, o) => (t === "message" ? realAdd(t, conv(fn), o) : realAdd(t, fn, o));
    return ws;
  };
  Wrap.prototype = W.prototype;
  for (const k of ["CONNECTING", "OPEN", "CLOSING", "CLOSED"]) Wrap[k] = W[k];
  window.WebSocket = Wrap;
})();`;

const DESK_W = 1280;
const DESK_H = 800;

function desktopHtml(lang) {
  const zh = lang !== "en";
  const tr = (en, z) => (zh ? z : en);
  return `<!doctype html><html><head><meta charset=utf-8><style>
*{box-sizing:border-box;margin:0}
body{width:${DESK_W}px;height:${DESK_H}px;overflow:hidden;font-family:"Ubuntu","Segoe UI","Microsoft JhengHei",sans-serif;
 background:radial-gradient(900px 600px at 75% 70%,#e95420 0%,#a1314f 38%,#4a1e47 70%,#2c0f2b 100%);color:#fff;position:relative}
.top{position:absolute;left:0;right:0;top:0;height:30px;background:#1d1d1d;display:flex;align-items:center;justify-content:space-between;padding:0 14px;font-size:14px}
.dock{position:absolute;left:0;top:30px;bottom:0;width:62px;background:rgba(20,20,20,.72);display:flex;flex-direction:column;align-items:center;gap:12px;padding-top:12px}
.ic{width:42px;height:42px;border-radius:11px}
.win{position:absolute;border-radius:10px;overflow:hidden;box-shadow:0 18px 50px rgba(0,0,0,.55)}
.bar{height:36px;background:#303030;display:flex;align-items:center;padding:0 12px;font-size:13px;color:#ddd;justify-content:center;position:relative}
.dots{position:absolute;right:12px;display:flex;gap:8px}.dots i{width:13px;height:13px;border-radius:50%;background:#555;display:block}.dots i:last-child{background:#e95420}
.term{background:#300a24;color:#eee;font-family:"Ubuntu Mono",Consolas,monospace;font-size:15px;line-height:1.45;padding:12px 14px;height:100%;white-space:pre}
.g{color:#8ae234;font-weight:700}.b{color:#729fcf;font-weight:700}
.web{background:#fff;color:#222;height:100%}
.url{background:#f0f0f4;color:#444;font-size:13px;padding:7px 12px;margin:8px 12px;border-radius:8px}
.hero{padding:26px 34px}.hero h1{font-size:28px;color:#4a1e47;margin-bottom:8px}.hero p{color:#555;font-size:15px;line-height:1.6}
.card{margin-top:16px;display:flex;gap:12px}.card div{flex:1;background:#f6f2f7;border-radius:10px;padding:12px;font-size:13px;color:#4a1e47}
</style></head><body>
<div class=top><b>${tr("Activities", "活動")}</b><span>${tr("Mon Oct 12  10:00", "10月12日 星期一 10:00")}</span><span>${tr("en ▾", "中 ▾")} 🔊 ⏻</span></div>
<div class=dock>
 <div class=ic style="background:linear-gradient(135deg,#ff9500,#e33b2e)"></div>
 <div class=ic style="background:linear-gradient(135deg,#3b8beb,#1a5fb4)"></div>
 <div class=ic style="background:#2d2d2d;border:2px solid #555"></div>
 <div class=ic style="background:linear-gradient(135deg,#f6d32d,#e5a50a)"></div>
 <div class=ic style="background:linear-gradient(135deg,#26a269,#1c7a4f)"></div>
</div>
<div class=win style="left:120px;top:70px;width:640px;height:420px">
 <div class=bar>student@ubuntu-desktop: ~<span class=dots><i></i><i></i><i></i></span></div>
 <div class=term><span class=g>student@ubuntu-desktop</span>:<span class=b>~</span>$ lsb_release -d
Description:  Ubuntu 24.04.1 LTS
<span class=g>student@ubuntu-desktop</span>:<span class=b>~</span>$ gcc -o sched sched.c && ./sched
Round-robin (q=4): avg waiting time 7.25 ms
<span class=g>student@ubuntu-desktop</span>:<span class=b>~</span>$ free -h
              total        used        free
Mem:          7.8Gi       2.9Gi       4.6Gi
<span class=g>student@ubuntu-desktop</span>:<span class=b>~</span>$ █</div>
</div>
<div class=win style="left:560px;top:260px;width:660px;height:470px">
 <div class=bar>${tr("OS Lab — Mozilla Firefox", "作業系統實驗 — Mozilla Firefox")}<span class=dots><i></i><i></i><i></i></span></div>
 <div class=web>
  <div class=url>🔒 os-lab-wang.skylab.ntub.edu.tw</div>
  <div class=hero><h1>${tr("Operating Systems Lab · Week 5", "作業系統實驗 · 第 5 週")}</h1>
   <p>${tr("CPU scheduling and memory management exercises.<br>Compare FCFS, SJF and round-robin on your own machine.", "行程排程與記憶體管理練習。<br>在自己的機器上比較 FCFS、SJF 與 Round-robin。")}</p>
   <div class=card><div><b>CPU</b><br>${tr("4 cores", "4 核心")}</div><div><b>${tr("Memory", "記憶體")}</b><br>8 GB</div><div><b>${tr("Status", "狀態")}</b><br>${tr("Running", "執行中")}</div></div>
  </div>
 </div>
</div>
</body></html>`;
}

// Renders the fake desktop for the current language as a JPEG (for the RFB server).
export async function desktopJpeg(s) {
  const p = await s.browser.newPage({ viewport: { width: DESK_W, height: DESK_H } });
  await p.setContent(desktopHtml(s.lang));
  await p.waitForTimeout(200);
  const jpg = await p.screenshot({ type: "jpeg", quality: 88 });
  await p.close();
  return jpg;
}

const u16 = (n) => { const b = Buffer.alloc(2); b.writeUInt16BE(n); return b; };
const u32 = (n) => { const b = Buffer.alloc(4); b.writeUInt32BE(n); return b; };
function compactLen(n) {
  const out = [n & 0x7f];
  if (n > 0x7f) { out[0] |= 0x80; out.push((n >> 7) & 0x7f); if (n > 0x3fff) { out[1] |= 0x80; out.push((n >> 14) & 0xff); } }
  return Buffer.from(out);
}

// RFB 3.8 server: security None, then one Tight JPEG frame of the desktop.
export function vncServer(ws, jpg) {
  let stage = "version";
  let sentFirst = false;
  setTimeout(() => ws.send(Buffer.from("RFB 003.008\n")), 250);
  ws.onMessage((msg) => {
    const buf = Buffer.isBuffer(msg) ? msg : Buffer.from(msg);
    if (stage === "version") { stage = "security"; ws.send(Buffer.from([1, 1])); return; }
    if (stage === "security") { stage = "init"; ws.send(u32(0)); return; }
    if (stage === "init") {
      stage = "normal";
      const name = Buffer.from("ubuntu-desktop");
      const pf = Buffer.from([32, 24, 0, 1, 0, 255, 0, 255, 0, 255, 16, 8, 0, 0, 0, 0]);
      ws.send(Buffer.concat([u16(DESK_W), u16(DESK_H), pf, u32(name.length), name]));
      return;
    }
    for (let i = 0; i < buf.length; i++) {
      if (buf[i] === 3 && !sentFirst) {
        sentFirst = true;
        ws.send(Buffer.concat([Buffer.from([0, 0]), u16(1), u16(0), u16(0), u16(DESK_W), u16(DESK_H), u32(7), Buffer.from([0x90]), compactLen(jpg.length), jpg]));
        return;
      }
    }
  });
}

// termproxy-style terminal: "OK" then a short shell session; optional drop after it.
export function termServer(ws, { name = "flask-blog-lab", drop = false } = {}) {
  const P = `\x1b[1;32mstudent@${name}\x1b[0m:\x1b[1;34m~\x1b[0m$ `;
  const session = [
    "Welcome to Ubuntu 24.04.1 LTS (GNU/Linux 6.8.12-4-pve x86_64)\r\n\r\n",
    `${P}cd ~/flask-blog && source .venv/bin/activate\r\n`,
    `(.venv) ${P}flask --version\r\n`,
    "Python 3.12.3\r\nFlask 3.0.3\r\nWerkzeug 3.0.4\r\n",
    `(.venv) ${P}curl -s localhost:5000/health\r\n`,
    '{"status": "ok", "posts": 12}\r\n',
    `(.venv) ${P}`,
  ].join("");
  ws.send("OK" + session);
  ws.onMessage((msg) => {
    const text = typeof msg === "string" ? msg : msg.toString("utf8");
    const mt = text.match(/^0:\d+:([\s\S]*)$/);
    if (mt) ws.send(mt[1].replace(/\r/g, "\r\n"));
  });
  if (drop) setTimeout(() => ws.close({ code: 1011, reason: "upstream closed" }), 600);
}
