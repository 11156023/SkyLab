// Shared mock data for the class pages: class-management, class-setup, class-workspace.
//
// installClasses(m, opts) registers a small stateful class store: list / get /
// create / update, roster, environment, weeks, capacity preview, provisioning,
// lifecycle (extend, archive, reclaim), classroom monitoring (usage, broadcast,
// live view sessions), the AI assistant endpoints and fake VNC / terminal
// WebSockets. Scenes tweak a class with opts.patch = { <key>: {...} } or by
// editing m.state.classes[<key>] before the page loads.

import { D, H, MIN, NOW, ago, iso } from "../lib/mock.mjs";

// ---------------------------------------------------------------- people

const ROSTER_EN = [
  "Alex Wang", "Emily Chang", "Kevin Chen", "Grace Lee", "Brian Huang", "Iris Wu", "Jason Lin", "Vivian Liu", "Eric Tsai", "Chloe Yang",
  "Henry Hsu", "Amy Cheng", "Ryan Hsieh", "Zoe Kuo", "Daniel Hung", "Sophie Tseng", "Leo Chiu", "Nina Liao", "Victor Lai", "Wendy Chou",
  "Oscar Yeh", "Tina Su", "Peter Chuang", "Ruby Lu", "Sam Ho", "Claire Kao", "Max Lo", "Fiona Chung", "Tom Chiang", "Hannah Fan",
  "Ivan Peng", "Joyce Tang", "Kyle Hou", "Lily Pan", "Mark Shih", "Olivia Chao", "Paul Weng", "Queenie Yu", "Ray Du",
  "Sandy Fu", "Tony Kang", "Una Shen", "Vince Tu", "Wanda Hsiao", "Xavier Lan", "Yvonne Cai", "Zack Gao", "Bella Fang",
];
const ROSTER_ZH = [
  "王小明", "張雅婷", "陳冠宇", "李佳穎", "黃柏翰", "吳欣怡", "林宜臻", "劉怡君", "蔡明哲", "楊詠晴",
  "許家豪", "鄭宇彤", "謝承佑", "郭芷瑄", "洪浩宇", "曾子涵", "邱冠霖", "廖品妍", "賴柏翰", "周欣妤",
  "葉昱辰", "蘇郁婷", "莊博鈞", "呂宜蓁", "何俊廷", "高子晴", "羅志豪", "鍾佩珊", "江冠廷", "范書瑜",
  "彭冠廷", "唐思妤", "侯宇軒", "潘怡安", "施承恩", "趙心怡", "翁志偉", "游雅筑", "杜俊宏",
  "傅佳蓉", "康柏宇", "沈詩涵", "涂建銘", "蕭郁涵", "藍士豪", "蔡亦晴", "高振宇", "方語彤",
];

// Student i of a class whose IDs start at `prefix` (for example "111560").
export function person(m, prefix, i) {
  const sid = `${prefix}${String(i + 1).padStart(2, "0")}`;
  const k = i % ROSTER_EN.length;
  const lap = Math.floor(i / ROSTER_EN.length);
  return {
    sid,
    email: `${sid}@ntub.edu.tw`,
    name: m.L(ROSTER_EN[k] + (lap ? ` ${lap + 1}` : ""), ROSTER_ZH[k] + (lap ? `${lap + 1}` : "")),
    user_id: `7d3c1a2e-${prefix.slice(0, 4)}-4b00-9000-${String(1000 + i).padStart(12, "0")}`,
  };
}

// ---------------------------------------------------------------- teaching environments

const TEACHER_ID = "7d3c1a2e-0002-4a00-9000-000000000002";

function envNode(versionId, key, name, role, type, cpu, mem, disk, x, y, extra = {}) {
  return {
    id: `${versionId}-${key}`, version_id: versionId, node_key: key, name, role, resource_type: type, cpu, memory_mb: mem, disk_gb: disk,
    source_type: "template", source_template_id: `tpl-${key}`, custom_image_ref: null, custom_username: null, custom_unprivileged: true,
    network: "lab-net", position_x: x, position_y: y, sort_order: 0, ...extra,
  };
}

export function environments(m) {
  const make = (id, version, name, description, nodes, edges = [], publications = []) => {
    const versionId = `${id}-v${version}`;
    const ns = nodes.map((n, i) => ({ ...envNode(versionId, ...n), sort_order: i }));
    const sum = ns.reduce((a, n) => ({ cpu: a.cpu + n.cpu, mem: a.mem + n.memory_mb, disk: a.disk + n.disk_gb }), { cpu: 0, mem: 0, disk: 0 });
    return {
      id, version_id: versionId, owner_id: TEACHER_ID, name, description, usage_scope: "course", max_concurrent_sessions: null, files: [],
      version, status: "published", configuration_hash: `${id}-${version}`, draft_data: null, classes: 1, peer_policy: "explicit",
      created_at: ago(60 * D), updated_at: ago(20 * D), published_at: ago(20 * D),
      nodes: ns,
      edges: edges.map(([s, t, port], i) => ({ id: `${versionId}-e${i}`, version_id: versionId, source_node_key: s, target_node_key: t, direction: "one_way", protocol: "tcp", port })),
      publications: publications.map(([key, port, prefix], i) => ({ id: `${versionId}-p${i}`, version_id: versionId, node_key: key, mode: "domain", port, protocol: "tcp", hostname_prefix: prefix, zone_id: "z1", enable_https: true, sort_order: i })),
      per_student: { machines: ns.length, cpu_cores: sum.cpu, memory_mb: sum.mem, disk_gb: sum.disk, ip_count: ns.length, network_count: 1 },
    };
  };
  return {
    web: make("env-web", 3, m.L("Web Full-Stack Lab", "Web 全端開發環境"),
      m.L("Ubuntu desktop with VS Code and Node.js 22, plus a PostgreSQL 16 database server.", "Ubuntu 桌面＋VS Code 與 Node.js 22，搭配一台 PostgreSQL 16 資料庫。"),
      [["web", "web", m.L("Development desktop", "開發桌面"), "qemu", 2, 4096, 32, 80, 120], ["db", "db", m.L("Database", "資料庫"), "lxc", 1, 2048, 16, 380, 300]],
      [["web", "db", 5432]], [["web", 80, "cloud-web"]]),
    linux: make("env-linux", 2, m.L("Linux System Administration Lab", "Linux 系統管理實驗環境"),
      m.L("Ubuntu 24.04 server with Docker and Nginx; one practice machine per student.", "Ubuntu 24.04 伺服器，含 Docker 與 Nginx，每位學生一台實作機。"),
      [["srv", "linux-server", m.L("Practice server", "實作主機"), "lxc", 2, 2048, 16, 80, 120]]),
    k8s: make("env-k8s", 1, m.L("Docker & Kubernetes Lab", "Docker 與 Kubernetes 實作環境"),
      m.L("A three-node Kubernetes cluster (one control plane, two workers) for container orchestration exercises.", "三節點 Kubernetes 叢集（一台控制平面、兩台工作節點），練習容器編排。"),
      [["master", "k8s-master", m.L("Control plane", "控制平面"), "qemu", 2, 4096, 32, 80, 60], ["w1", "k8s-worker-1", m.L("Worker", "工作節點"), "qemu", 2, 4096, 32, 380, 20], ["w2", "k8s-worker-2", m.L("Worker", "工作節點"), "qemu", 2, 4096, 32, 380, 200]],
      [["master", "w1", 6443], ["master", "w2", 6443]]),
  };
}

// An environment whose Kali machine has lost its template binding (cannot be applied).
export function brokenEnvironment(m) {
  const env = environments(m).linux;
  return {
    ...env, id: "env-sec", version_id: "env-sec-v1", version: 1, name: m.L("Network Security Lab", "網路安全攻防環境"),
    description: m.L("Kali Linux attacker and a vulnerable web server on an isolated network.", "Kali Linux 攻擊機與一台有弱點的網站伺服器，位於隔離網段。"),
    nodes: [
      { ...env.nodes[0], id: "env-sec-v1-kali", version_id: "env-sec-v1", node_key: "kali", name: "kali", resource_type: "qemu", cpu: 2, memory_mb: 4096, disk_gb: 40, source_template_id: null },
      { ...env.nodes[0], id: "env-sec-v1-target", version_id: "env-sec-v1", node_key: "target", name: "dvwa-target", sort_order: 1 },
    ],
  };
}

// ---------------------------------------------------------------- classes

const CID = (n) => `c1a55000-0000-4000-8000-0000000000${String(n).padStart(2, "0")}`;
export const CLASS_IDS = {
  cloud: CID(11), linux: CID(12), db: CID(13), net: CID(14), ml: CID(15), web: CID(16), ds: CID(17), iot: CID(18), py: CID(19),
};

const T = (en, zh) => [en, zh];

function cloudWeeks() {
  return {
    1: { title: T("Course intro and SkyLab accounts", "課程介紹與 SkyLab 帳號"), status: "completed" },
    2: { title: T("Linux basics and SSH", "Linux 基本指令與 SSH"), status: "completed" },
    3: { title: T("Nginx web server", "Nginx 網頁伺服器"), status: "completed", files: ["week03-nginx.pdf"] },
    4: { title: T("Deploy a Node.js app", "部署 Node.js 應用程式"), status: "completed", files: ["week04-node-deploy.pdf"] },
    5: { title: T("Connect to PostgreSQL", "連線 PostgreSQL 資料庫"), status: "completed", files: ["week05-postgresql.pdf", "schema.sql"] },
    6: { title: T("Docker container basics", "Docker 容器基礎"), status: "published", files: ["week06-docker-basics.pdf", "Dockerfile.sample"] },
    7: { title: T("Multi-service apps with Docker Compose", "Docker Compose 多服務部署"), status: "draft" },
    8: { title: T("Midterm project checkpoint", "期中專題檢查點"), status: "draft" },
  };
}

function baseClasses() {
  return {
    cloud: { id: CLASS_IDS.cloud, name: T("Cloud Computing Practice (IM 3A)", "雲端運算實務（資管三甲）"), term: "115-1", location: T("Information Building B302", "資訊大樓 B302"),
      status: "active", weekday: 0, start: "09:10", end: "12:00", startDate: "2026-09-07", endDate: "2027-01-04", boot: 10, grace: 30, env: "web",
      students: 30, prefix: "111560", vmBase: 30100, teacher: true, weeks: cloudWeeks(), created: ago(50 * D) },
    linux: { id: CLASS_IDS.linux, name: T("Linux System Administration (IM 2B)", "Linux 系統管理（資管二乙）"), term: "115-1", location: T("Information Building B204", "資訊大樓 B204"),
      status: "active", weekday: 2, start: "13:10", end: "16:00", startDate: "2026-09-09", endDate: "2027-01-06", boot: 15, grace: 30, env: "linux",
      students: 38, prefix: "112561", vmBase: 31100, weeks: { 1: { title: T("Shell basics", "Shell 基礎"), status: "completed" } }, created: ago(48 * D) },
    db: { id: CLASS_IDS.db, name: T("Database Systems (IM 2A)", "資料庫系統（資管二甲）"), term: "115-1", location: T("Information Building B301", "資訊大樓 B301"),
      status: "pending_review", weekday: 3, start: "13:10", end: "15:00", startDate: "2026-10-22", endDate: "2027-01-14", boot: 10, grace: 30, env: "web",
      students: 40, prefix: "112560", vmBase: 32100, weeks: { 1: { title: T("Relational model and SQL", "關聯式模型與 SQL"), status: "published" } }, created: ago(6 * D) },
    net: { id: CLASS_IDS.net, name: T("Network Programming (CS 3B)", "網路程式設計（資工三乙）"), term: "115-1", location: T("Engineering Building E405", "工程大樓 E405"),
      status: "provisioning", weekday: 1, start: "10:10", end: "12:00", startDate: "2026-10-13", endDate: "2027-01-12", boot: 10, grace: 30, env: "linux",
      students: 46, prefix: "111580", vmBase: 33100, ready: 31, weeks: { 1: { title: T("Sockets in Python", "Python Socket 程式設計"), status: "published" } }, created: ago(4 * D) },
    ml: { id: CLASS_IDS.ml, name: T("Machine Learning Lab (IM 4A)", "機器學習實務（資管四甲）"), term: "115-1", location: T("Information Building B501", "資訊大樓 B501"),
      status: "partial_failed", weekday: 4, start: "09:10", end: "12:00", startDate: "2026-10-16", endDate: "2027-01-15", boot: 10, grace: 30, env: "linux",
      students: 24, prefix: "110560", vmBase: 34100, failed: [6, 17], weeks: { 1: { title: T("Python and NumPy refresher", "Python 與 NumPy 複習"), status: "published" } }, created: ago(3 * D) },
    web: { id: CLASS_IDS.web, name: T("Web Programming (IM 1A)", "網頁程式設計（資管一甲）"), term: "115-1", location: T("Information Building B303", "資訊大樓 B303"),
      status: "planning", weekday: 1, start: "13:10", end: "16:00", startDate: "2026-10-20", endDate: "2027-01-12", boot: 10, grace: 30, env: "web",
      students: 45, prefix: "115560", vmBase: 35100, weeks: {}, created: ago(2 * D) },
    ds: { id: CLASS_IDS.ds, name: T("Data Structures (IM 1B)", "資料結構（資管一乙）"), term: "115-1", location: T("Information Building B305", "資訊大樓 B305"),
      status: "planning", weekday: 2, start: "09:10", end: "12:00", startDate: "2026-10-21", endDate: "2027-01-13", boot: 10, grace: 30, env: null,
      students: 42, prefix: "115561", vmBase: 36100, weeks: {}, created: ago(D) },
    iot: { id: CLASS_IDS.iot, name: T("IoT Systems Workshop (IM 3B)", "物聯網系統實作（資管三乙）"), term: "115-1", location: T("Innovation Lab A", "創新實驗室 A"),
      status: "planning", weekday: 3, start: "15:10", end: "17:00", startDate: "2026-10-22", endDate: "2027-01-14", boot: 5, grace: 10, env: null,
      students: 0, prefix: "111562", vmBase: 37100, weeks: {}, created: ago(3 * H) },
    py: { id: CLASS_IDS.py, name: T("Python Programming (IM 1A)", "Python 程式設計（資管一甲）"), term: "114-2", location: T("Information Building B302", "資訊大樓 B302"),
      status: "archived", weekday: 0, start: "13:10", end: "16:00", startDate: "2026-02-23", endDate: "2026-06-15", boot: 10, grace: 30, env: "linux",
      students: 48, prefix: "114560", vmBase: 29100, weeks: { 1: { title: T("Hello Python", "Hello Python"), status: "completed" } },
      archivedAt: "2026-06-22T02:00:00Z", reclaimedAt: "2026-06-22T02:10:00Z", created: ago(240 * D) },
  };
}

const addDays = (date, n) => new Date(Date.parse(date + "T00:00:00Z") + n * D).toISOString().slice(0, 10);
const weekdayOf = (date) => (new Date(date + "T00:00:00Z").getUTCDay() + 6) % 7; // 0 = Monday

function sessionDates(c) {
  let first = c.startDate;
  while (weekdayOf(first) !== c.weekday) first = addDays(first, 1);
  const out = [];
  for (let d = first; d <= c.endDate && out.length < 104; d = addDays(d, 7)) out.push(d);
  return out;
}

export function installClasses(m, opts = {}) {
  const L = ([en, zh]) => m.L(en, zh);
  const envs = { ...environments(m), ...(opts.extraEnvs || {}) };
  const st = m.state;
  st.classes = baseClasses();
  for (const [k, v] of Object.entries(opts.patch || {})) st.classes[k] = { ...st.classes[k], ...v };
  st.sessions = st.sessions || [];

  const byId = (id) => Object.values(st.classes).find((c) => c.id === id);
  const envOf = (c) => (c.env ? envs[c.env] : null);
  const roster = (c) => {
    if (!c._roster) c._roster = Array.from({ length: c.students }, (_, i) => ({ ...person(m, c.prefix, i), idx: i, joined: c.created }));
    return c._roster;
  };
  const machineNodes = (c) => (envOf(c)?.nodes || []).map((n, j) => ({
    id: `mn-${c.id.slice(-2)}-${n.node_key}`, node_key: n.node_key, name: n.name, display_label: n.name, sort_order: j, role: n.role,
    resource_type: n.resource_type, cpu: n.cpu, memory_mb: n.memory_mb, disk_gb: n.disk_gb, source_type: n.source_type,
    source_template_id: n.source_template_id, template_name: n.name,
  }));
  const vmid = (c, i, j) => c.vmBase + i * 4 + j;

  function machineStatus(c, i, k, total) {
    if (c.status === "active" || c.status === "archived") return "completed";
    if (c.status === "pending_review") return "pending";
    if (c.status === "provisioning") return k < (c.ready ?? 0) ? "completed" : k < (c.ready ?? 0) + 6 ? "running" : "pending";
    if (c.status === "partial_failed") return c.allFailed || (c.failed || []).includes(i) ? "failed" : "completed";
    return "pending";
  }

  function classObj(c) {
    const nodes = machineNodes(c);
    const env = envOf(c);
    const students = roster(c);
    const total = (students.length + (c.teacher ? 1 : 0)) * nodes.length;
    const members = students.map((x, i) => ({
      id: `cm-${c.id.slice(-2)}-${i}`, user_id: x.user_id, status: "active", joined_at: x.joined, email: x.email, full_name: x.name,
      machines: c.status === "planning" ? [] : nodes.map((n, j) => {
        const status = machineStatus(c, i, i * nodes.length + j, total);
        return { machine_node_id: n.id, vmid: status === "pending" || (status === "failed" && j === 0) ? null : vmid(c, i, j), status,
          error: status === "failed" ? m.L("Clone timed out on pve-03 (storage busy)", "在 pve-03 複製逾時（儲存區忙碌）") : null };
      }),
    }));
    const ready = members.reduce((a, s) => a + s.machines.filter((x) => x.status === "completed").length, 0) + (c.teacher && ["active", "archived"].includes(c.status) ? nodes.length : 0);
    const dates = sessionDates(c);
    const weeks = dates.map((date, k) => {
      const w = c.weeks[k + 1] || {};
      return {
        id: `wk-${c.id.slice(-2)}-${k + 1}`, week_number: k + 1, session_date: date,
        title: Array.isArray(w.title) ? L(w.title) : w.title || "", status: w.status || "draft", target_node_key: null,
        files: (w.files || []).map((f, i) => (typeof f === "string" ? { id: `f-${c.id.slice(-2)}-${k + 1}-${i}`, filename: f, size_bytes: 482113 + i * 9000 } : f)),
      };
    });
    const jobs = c.status === "planning" ? [] : nodes.map((n, j) => {
      const mine = members.map((s) => s.machines[j]);
      const done = mine.filter((x) => x.status === "completed").length;
      const failed = mine.filter((x) => x.status === "failed").length;
      const status = c.status === "pending_review" ? "pending_review" : failed ? "failed" : done === mine.length ? "completed" : "running";
      return { id: `job-${c.id.slice(-2)}-${j}`, status, total: mine.length, done, failed_count: failed };
    });
    return {
      id: c.id, name: L(c.name), term: c.term, location: c.location ? L(c.location) : null, status: c.status, timezone: "Asia/Taipei",
      start_date: c.startDate, end_date: c.endDate, weekday: c.weekday, start_time: `${c.start}:00`, end_time: `${c.end}:00`,
      boot_lead_minutes: c.boot, shutdown_grace_minutes: c.grace,
      course_version_id: env ? env.version_id : null,
      course_environment: env ? { id: env.id, version_id: env.version_id, name: env.name, version: env.version, status: "published" } : null,
      machine_nodes: nodes, students: members, member_count: members.length,
      instructor_machine: c.teacher && c.status !== "planning"
        ? { user_id: TEACHER_ID, machines: nodes.map((n, j) => ({ machine_node_id: n.id, vmid: c.vmBase - 10 + j, status: "completed", error: null })) } : null,
      weeks, provision_jobs: jobs, ready_machines: ready, total_machines: total,
      topology_edges: env ? env.edges : [], node_positions: env ? Object.fromEntries(env.nodes.map((n) => [n.node_key, { x: n.position_x, y: n.position_y }])) : {},
      publications: env ? env.publications : [],
      archived_at: c.archivedAt ?? null, reclaim_requested_at: c.archivedAt ?? null, resources_reclaimed_at: c.reclaimedAt ?? null,
      created_at: c.created, updated_at: ago(H),
    };
  }
  st.classObj = (key) => classObj(st.classes[key]);

  const P = "^/api/v1/teaching-classes/([^/]+)";
  const R = (s) => new RegExp(P + s + "$");
  const get = (mt) => byId(mt[1]);
  const notFound = { __status: 404, __body: { detail: "Class not found" } };
  const wrap = (fn) => ({ mt, body, ...rest }) => { const c = get(mt); return c ? fn(c, body, rest) : notFound; };

  // Who can be added: anyone with a 1115xxxx-style student ID; teacher emails are rejected.
  function addEmails(c, emails) {
    const r = roster(c);
    const notFound = [];
    const invalid = [];
    let added = 0;
    for (const raw of emails) {
      const email = String(raw).trim().toLowerCase();
      if (!email) continue;
      if (/^(lin|chen|wu|admin)@/.test(email)) { invalid.push(email); continue; }
      if (!/^\d{8}@ntub\.edu\.tw$/.test(email)) { notFound.push(email); continue; }
      if (r.some((x) => x.email === email)) continue;
      const i = r.length;
      const p = person(m, c.prefix, i);
      r.push({ ...p, email, sid: email.slice(0, 8), idx: i, joined: iso(NOW) });
      added++;
    }
    c.students = r.length;
    return { added, not_found: notFound, invalid_role: invalid, class: classObj(c) };
  }

  m.use([
    ["GET", /^\/api\/v1\/teaching-classes\/?$/, () => Object.values(st.classes).filter((c) => !c.hidden).map(classObj)],
    ["POST", /^\/api\/v1\/teaching-classes\/?$/, ({ body }) => {
      const c = { id: CID(30), name: [body.name, body.name], term: body.term, location: body.location ? [body.location, body.location] : null, status: "planning",
        weekday: body.weekday, start: String(body.start_time).slice(0, 5), end: String(body.end_time).slice(0, 5), startDate: body.start_date, endDate: body.end_date,
        boot: body.boot_lead_minutes, grace: body.shutdown_grace_minutes, env: null, students: 0, prefix: "115570", vmBase: 38100, weeks: {}, created: iso(NOW) };
      st.classes.created = c;
      return { __status: 201, __body: classObj(c) };
    }],
    ["GET", R(""), wrap((c) => classObj(c))],
    ["PATCH", R(""), wrap((c, b) => {
      if (b.name) c.name = [b.name, b.name];
      if (b.location !== undefined) c.location = b.location ? [b.location, b.location] : null;
      Object.assign(c, {
        term: b.term ?? c.term, weekday: b.weekday ?? c.weekday, startDate: b.start_date ?? c.startDate, endDate: b.end_date ?? c.endDate,
        start: b.start_time ? String(b.start_time).slice(0, 5) : c.start, end: b.end_time ? String(b.end_time).slice(0, 5) : c.end,
        boot: b.boot_lead_minutes ?? c.boot, grace: b.shutdown_grace_minutes ?? c.grace,
      });
      return classObj(c);
    })],
    ["GET", R("/capacity-preview"), wrap((c) => {
      const env = envOf(c);
      const n = roster(c).length;
      if (st.capacityFail) return { __delay: 300, __body: { ready: false, issues: st.capacityFail, student_count: n, machine_count: 0, cpu_cores: 0, memory_mb: 0, disk_gb: 0, ip_count: 0 } };
      const e = env.per_student;
      return { __delay: 300, __body: { student_count: n, machines_per_student: e.machines, machine_count: n * e.machines, cpu_cores: n * e.cpu_cores, memory_mb: n * e.memory_mb,
        disk_gb: n * e.disk_gb, ip_count: n * e.machines, network_count: n, available_ips: 412, ready: true, issues: [], cluster_checked: true, placement_plan: {} } };
    })],
    ["POST", R("/students"), wrap((c, b) => addEmails(c, b?.emails || []))],
    ["POST", R("/students/import-csv"), wrap((c) => {
      const emails = Array.from({ length: 28 }, (_, i) => `${c.prefix}${String(roster(c).length + i + 1).padStart(2, "0")}@ntub.edu.tw`);
      const res = addEmails(c, emails);
      if (st.csvRejects) { res.not_found = st.csvRejects.notFound; res.invalid_role = st.csvRejects.invalid; }
      return { __delay: 300, __body: res };
    })],
    // A whole school class from User Management; its homeroom teacher is listed as not a student.
    ["POST", R("/students/import-group"), wrap((c) => {
      const emails = Array.from({ length: 6 }, (_, i) => `${c.prefix}${String(roster(c).length + i + 1).padStart(2, "0")}@ntub.edu.tw`);
      const res = addEmails(c, [...emails, "lin@ntub.edu.tw"]);
      return { __delay: 300, __body: res };
    })],
    ["DELETE", R("/students/([^/]+)"), wrap((c, b, { mt }) => {
      const i = Number(String(mt[2]).split("-").pop());
      c._roster = roster(c).filter((x) => x.idx !== i);
      c.students = c._roster.length;
      return classObj(c);
    })],
    ["PUT", R("/course"), wrap((c, b) => {
      const key = Object.keys(envs).find((k) => envs[k].version_id === b?.course_version_id);
      c.env = key || c.env;
      return classObj(c);
    })],
    ["PUT", R("/weeks"), wrap((c, b) => {
      c.weeks = {};
      for (const w of Array.isArray(b) ? b : []) {
        const prev = classObj(c);
        c.weeks[w.week_number] = { title: w.title, status: w.status, files: (w.files || []).map((f) => ({ id: f.id || f.file_id, filename: f.filename || f.name || "file" })) };
        void prev;
      }
      return classObj(c);
    })],
    ["POST", R("/weeks/([^/]+)/files"), wrap((c, b, { mt }) => {
      const n = Number(String(mt[2]).split("-").pop());
      const w = (c.weeks[n] ||= { title: "", status: "draft" });
      (w.files ||= []).push({ id: `f-up-${n}-${(w.files || []).length}`, filename: "week07-compose-lab.pdf", size_bytes: 512000 });
      return { __delay: 200, __body: classObj(c) };
    })],
    ["DELETE", R("/weeks/([^/]+)/files/([^/]+)"), wrap((c, b, { mt }) => {
      const n = Number(String(mt[2]).split("-").pop());
      const w = c.weeks[n];
      if (w?.files) w.files = w.files.filter((f, i) => (typeof f === "string" ? `f-${c.id.slice(-2)}-${n}-${i}` : f.id) !== mt[3]);
      return classObj(c);
    })],
    ["POST", R("/provision"), wrap((c) => { c.status = "pending_review"; return { __delay: 300, __body: classObj(c) }; })],
    ["GET", R("/provision-status"), wrap((c) => classObj(c))],
    ["POST", R("/reconcile"), wrap((c) => classObj(c))],
    ["POST", R("/retry-failed"), wrap((c) => { c.status = "provisioning"; c.ready = roster(c).length - 2; return classObj(c); })],
    ["POST", R("/reset-failed"), wrap((c) => { c.status = "planning"; c.allFailed = false; return classObj(c); })],
    ["POST", R("/extend"), wrap((c, b) => { c.endDate = b.end_date; return classObj(c); })],
    ["POST", R("/archive"), wrap((c) => {
      c.status = "archived"; c.archivedAt = iso(NOW);
      const failed = st.reclaimFailures ?? 0;
      if (!failed) c.reclaimedAt = iso(NOW);
      return { __delay: st.archiveDelay ?? 200, __body: { class: classObj(c), reclaim: { submitted: [], failed: Array.from({ length: failed }, (_, i) => ({ vmid: c.vmBase + i, reason: "PVE task timeout" })) } } };
    })],
    ["POST", R("/reclaim"), wrap((c) => { c.reclaimedAt = iso(NOW); return { submitted: [], failed: [] }; })],
    ["GET", R("/resource-usage"), wrap((c) => usage(c))],

    // Teaching environments
    ["GET", /^\/api\/v1\/course-environments\/published$/, () => (opts.publishedEnvs ? opts.publishedEnvs(envs) : Object.values(envs))],
    ["GET", /^\/api\/v1\/course-environments$/, () => Object.values(envs)],

    // Classroom monitoring
    ["GET", /^\/api\/v1\/classroom\/sessions$/, () => st.sessions.filter((x) => x.mode === "broadcast")],
    ["GET", /^\/api\/v1\/classroom\/classes\/[^/]+\/broadcast-sources$/, () => (st.noBroadcastSources ? [] : [
      { vmid: 30090, name: m.L("teacher-web (demo desktop)", "teacher-web（教師示範桌面）"), status: "running", node_key: "web", display_label: "web", vm_type: "qemu" },
      { vmid: 10520, name: "ubuntu-demo-2404", status: "running", node_key: null, display_label: null, vm_type: "qemu" },
    ])],
    ["POST", /^\/api\/v1\/classroom\/sessions$/, ({ body }) => {
      const s = { id: `cs-${st.sessions.length + 1}`, vmid: body?.vmid, mode: body?.mode, class_id: body?.class_id, started_by: TEACHER_ID, controller_user_id: null, subscriber_count: body?.mode === "broadcast" ? 27 : 0, created_at: iso(NOW) };
      st.sessions.push(s);
      return { __delay: 200, __body: s };
    }],
    ["POST", /^\/api\/v1\/classroom\/sessions\/[^/]+\/control$/, () => ({ message: "ok" })],
    ["DELETE", /^\/api\/v1\/classroom\/sessions\/([^/]+)$/, ({ mt }) => { st.sessions = st.sessions.filter((x) => x.id !== mt[1]); return { message: "ok" }; }],
    ["POST", /^\/api\/v1\/resources\/batch$/, ({ body }) => ({ __delay: st.batchDelay ?? 100, __body: { results: (body?.vmids || []).map((v) => ({ vmid: v, success: true, message: "ok" })) } })],
    ["GET", /^\/api\/v1\/resources\/(\d+)$/, ({ mt }) => ({ vmid: Number(mt[1]), name: `vm-${mt[1]}`, type: "lxc", status: "running", node: "pve-02" })],

    // AI assistant
    ["POST", /^\/api\/v1\/ai\/navigation\/resolve$/, () => ({ __delay: 400, __body: { intent: "guide", confidence: 0.9, action: "answer", answer: aiAnswer(), suggestions: [], steps: [] } })],
    ["POST", /^\/api\/v1\/ai\/contextual-help\/explain$/, () => ({ __delay: 400, __body: { intent: "next_step", answer: aiAnswer(), target: null, related: [], grounded_in: [], context_level: 2, context_version: 1 } })],
    ["POST", /^\/api\/v1\/ai\/template-recommendation\/chat$/, () => ({ __delay: 400, __body: { reply: aiAnswer(), sources: [] } })],
  ]);

  function aiAnswer() {
    return st.aiAnswer || m.L(
      "You are on step 5, **Confirm Creation**. The class is saved with 45 students and the **Web Full-Stack Lab** environment, and the capacity check has passed.\n\nNext:\n1. Check the four summary rows below (Weekly tasks is optional).\n2. Click **Finish setup and submit for provisioning** to send the class to an administrator for review.\n\nI can only guide you; I won't submit anything for you.",
      "你目前在第 5 步「確認建立」。班級已存檔，有 45 位學生，教學環境也選好「Web 全端開發環境」，容量預檢已通過。\n\n接下來：\n1. 檢查下方四列摘要（每週任務是選填）。\n2. 按「**完成設定並送出建機**」，班級會交給管理員審核。\n\n我只能引導你，不會替你送出。",
    );
  }

  // Live usage per class machine: a few busy students, two powered off, one without data.
  function usage(c) {
    const nodes = machineNodes(c);
    const items = [];
    roster(c).forEach((x, i) => nodes.forEach((n, j) => {
      if (i === 9) return; // no data yet
      const off = i === 27 || i === 28;
      let cpu = n.resource_type === "lxc" ? 4 + ((i * 13) % 26) : 12 + ((i * 37) % 55);
      if (j === 0 && i === 2) cpu = 97;
      if (j === 0 && i === 11) cpu = 88;
      if (j === 0 && i === 19) cpu = 83;
      items.push({ vmid: vmid(c, i, j), status: off ? "stopped" : "running", cpu_usage_pct: off ? 0 : cpu, ram_usage_pct: off ? 0 : 28 + ((i * 17) % 50),
        mem_used_bytes: 1.4 * 1024 ** 3, mem_total_bytes: (n.memory_mb / 1024) * 1024 ** 3 });
    }));
    if (c.teacher) nodes.forEach((n, j) => items.push({ vmid: c.vmBase - 10 + j, status: "running", cpu_usage_pct: 21, ram_usage_pct: 44, mem_used_bytes: 1.8 * 1024 ** 3, mem_total_bytes: (n.memory_mb / 1024) * 1024 ** 3 }));
    return { collected_at: iso(NOW - 3000), items };
  }

  // Fake VNC (RFB 3.8, one Tight/JPEG frame) and terminal WebSockets.
  m.ws = (p, ws) => {
    if (/^\/ws\/classroom\/[^/]+\/watch/.test(p) || p.startsWith("/ws/vnc/")) return vncServer(ws, st.vncFrame);
    const t = p.match(/^\/ws\/terminal\/(\d+)/);
    if (t) return termServer(m, ws, Number(t[1]));
    return false;
  };
}

// ---------------------------------------------------------------- fake console servers

const u16 = (n) => { const b = Buffer.alloc(2); b.writeUInt16BE(n); return b; };
const u32 = (n) => { const b = Buffer.alloc(4); b.writeUInt32BE(n); return b; };
function compactLen(n) {
  const out = [n & 0x7f];
  if (n > 0x7f) { out[0] |= 0x80; out.push((n >> 7) & 0x7f); if (n > 0x3fff) { out[1] |= 0x80; out.push((n >> 14) & 0xff); } }
  return Buffer.from(out);
}
const FW = 1280, FH = 800;

function vncServer(ws, jpg) {
  let stage = "version";
  let sent = false;
  setTimeout(() => ws.send(Buffer.from("RFB 003.008\n")), 150);
  ws.onMessage((msg) => {
    const buf = Buffer.isBuffer(msg) ? msg : Buffer.from(msg);
    if (stage === "version") { stage = "security"; ws.send(Buffer.from([1, 1])); return; }
    if (stage === "security") { stage = "init"; ws.send(u32(0)); return; }
    if (stage === "init") {
      stage = "normal";
      const name = Buffer.from("student-desktop");
      ws.send(Buffer.concat([u16(FW), u16(FH), Buffer.from([32, 24, 0, 1, 0, 255, 0, 255, 0, 255, 16, 8, 0, 0, 0, 0]), u32(name.length), name]));
      return;
    }
    // Walk the client messages; answer the first framebuffer update request with the JPEG frame.
    let o = 0;
    let wants = false;
    while (o < buf.length) {
      const type = buf[o];
      if (type === 0) o += 20;
      else if (type === 2) o += 4 + 4 * buf.readUInt16BE(o + 2);
      else if (type === 3) { wants = true; o += 10; }
      else if (type === 4) o += 8;
      else if (type === 5) o += 6;
      else if (type === 6) o += 8 + Math.abs(buf.readInt32BE(o + 4));
      else break;
    }
    if (wants && !sent && jpg) {
      sent = true;
      ws.send(Buffer.concat([Buffer.from([0, 0]), u16(1), u16(0), u16(0), u16(FW), u16(FH), u32(7), Buffer.from([0x90]), compactLen(jpg.length), jpg]));
    }
  });
  return true;
}

function termServer(m, ws, vmid) {
  const host = `db-${String(vmid).slice(-3)}`;
  const PROMPT = `\x1b[1;32mstudent@${host}\x1b[0m:\x1b[1;34m~\x1b[0m$ `;
  const lines = [
    `Welcome to Ubuntu 24.04.1 LTS (SkyLab LXC \u00b7 ${host})`,
    "",
    `${PROMPT}systemctl is-active postgresql`,
    "active",
    `${PROMPT}psql -U app -d shop -c "select count(*) from orders;"`,
    " count ",
    "-------",
    "   128",
    "(1 row)",
    "",
  ];
  ws.send("OK" + lines.join("\r\n") + PROMPT);
  ws.onMessage(() => {});
  void m;
  return true;
}

// Renders the student's desktop (1280x800 JPEG) shown in the live view.
const frames = {};
export async function desktopFrame(browser, lang) {
  if (frames[lang]) return frames[lang];
  const zh = lang !== "en";
  const html = `<!doctype html><html><head><meta charset=utf-8><style>
*{box-sizing:border-box;margin:0}
body{width:1280px;height:800px;overflow:hidden;font-family:"Segoe UI","Microsoft JhengHei",sans-serif;background:radial-gradient(900px 600px at 75% 70%,#e95420 0%,#a1314f 38%,#4a1e47 70%,#2c0f2b 100%);color:#fff;position:relative}
.top{position:absolute;left:0;right:0;top:0;height:30px;background:#1d1d1d;display:flex;align-items:center;justify-content:space-between;padding:0 14px;font-size:14px}
.dock{position:absolute;left:0;top:30px;bottom:0;width:62px;background:rgba(20,20,20,.72);display:flex;flex-direction:column;align-items:center;gap:12px;padding-top:12px}
.ic{width:42px;height:42px;border-radius:11px}
.win{position:absolute;border-radius:10px;overflow:hidden;box-shadow:0 18px 50px rgba(0,0,0,.55)}
.bar{height:36px;background:#303030;display:flex;align-items:center;padding:0 12px;font-size:13px;color:#ddd;justify-content:center;position:relative}
.dots{position:absolute;right:12px;display:flex;gap:8px}.dots i{width:13px;height:13px;border-radius:50%;background:#555;display:block}.dots i:last-child{background:#e95420}
.term{background:#300a24;color:#eee;font-family:Consolas,monospace;font-size:15px;line-height:1.45;padding:12px 14px;height:100%;white-space:pre}
.g{color:#8ae234;font-weight:700}.b{color:#729fcf;font-weight:700}
.web{background:#fff;color:#222;height:100%}
.url{background:#f0f0f4;color:#444;font-size:13px;padding:7px 12px;margin:8px 12px;border-radius:8px}
.hero{padding:26px 34px}.hero h1{font-size:28px;color:#0b5394;margin-bottom:8px}.hero p{color:#555;font-size:15px;line-height:1.6}
.card{margin-top:16px;display:flex;gap:12px}.card div{flex:1;background:#eef4fb;border-radius:10px;padding:12px;font-size:13px;color:#0b5394}
</style></head><body>
<div class=top><b>${zh ? "活動" : "Activities"}</b><span>${zh ? "10月12日 星期一 10:02" : "Mon Oct 12 10:02"}</span><span>${zh ? "中" : "en"} ▾ 🔊 ⏻</span></div>
<div class=dock>
 <div class=ic style="background:linear-gradient(135deg,#ff9500,#e33b2e)"></div><div class=ic style="background:linear-gradient(135deg,#3b8beb,#1a5fb4)"></div>
 <div class=ic style="background:#2d2d2d;border:2px solid #555"></div><div class=ic style="background:linear-gradient(135deg,#f6d32d,#e5a50a)"></div>
 <div class=ic style="background:linear-gradient(135deg,#26a269,#1c7a4f)"></div>
</div>
<div class=win style="left:110px;top:64px;width:690px;height:430px">
 <div class=bar>student@web-11156003: ~/docker-lab<span class=dots><i></i><i></i><i></i></span></div>
 <div class=term><span class=g>student@web-11156003</span>:<span class=b>~/docker-lab</span>$ docker build -t hello-web .
[+] Building 12.4s (8/8) FINISHED
 => naming to docker.io/library/hello-web:latest
<span class=g>student@web-11156003</span>:<span class=b>~/docker-lab</span>$ docker run -d -p 8080:80 hello-web
5c1f0e2a9b7d4e18a6f3c2b1d0e9f8a7
<span class=g>student@web-11156003</span>:<span class=b>~/docker-lab</span>$ docker ps
CONTAINER ID   IMAGE       STATUS         PORTS
5c1f0e2a9b7d   hello-web   Up 3 seconds   0.0.0.0:8080->80/tcp
<span class=g>student@web-11156003</span>:<span class=b>~/docker-lab</span>$ █</div>
</div>
<div class=win style="left:560px;top:280px;width:660px;height:460px">
 <div class=bar>Hello Docker — Mozilla Firefox<span class=dots><i></i><i></i><i></i></span></div>
 <div class=web><div class=url>🔒 localhost:8080</div>
  <div class=hero><h1>${zh ? "Hello Docker！第 6 週實作" : "Hello Docker! Week 6 lab"}</h1>
   <p>${zh ? "這個網頁由 Nginx 容器提供，映像檔是剛才用<br>Dockerfile 建出來的 hello-web。" : "This page is served by an Nginx container built<br>from the hello-web Dockerfile a moment ago."}</p>
   <div class=card><div><b>${zh ? "容器" : "Container"}</b><br>hello-web</div><div><b>${zh ? "連接埠" : "Port"}</b><br>8080 → 80</div><div><b>${zh ? "狀態" : "Status"}</b><br>${zh ? "執行中" : "Running"}</div></div>
  </div></div>
</div></body></html>`;
  const ctx = await browser.newContext({ viewport: { width: FW, height: FH } });
  const p = await ctx.newPage();
  await p.setContent(html);
  await p.waitForTimeout(200);
  frames[lang] = await p.screenshot({ type: "jpeg", quality: 85 });
  await ctx.close();
  return frames[lang];
}

// Playwright's routeWebSocket swaps in a WebSocket class whose methods live on a
// parent prototype; noVNC only looks at the instance's own prototype and throws
// "Raw channel missing property: send". Flatten the chain onto a subclass.
export async function patchWebSocket(page) {
  await page.evaluate(() => {
    if (window.__wsFlat) return;
    const Base = window.WebSocket;
    class FlatWebSocket extends Base {}
    const descs = {};
    for (let p = Base.prototype; p && p !== Object.prototype; p = Object.getPrototypeOf(p)) {
      for (const k of Object.getOwnPropertyNames(p)) if (k !== "constructor" && !(k in descs)) descs[k] = Object.getOwnPropertyDescriptor(p, k);
    }
    Object.defineProperties(FlatWebSocket.prototype, descs);
    window.WebSocket = FlatWebSocket;
    window.__wsFlat = true;
  });
}

export { iso, MIN };
