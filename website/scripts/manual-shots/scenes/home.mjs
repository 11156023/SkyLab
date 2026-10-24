// Manual page: manual/home.md
import { D, H, NOW, ago, later } from "../lib/mock.mjs";
import { shotNew } from "../mocks/auth.mjs";

const GB = 1024 ** 3;

// ---------------------------------------------------------------- administrator

function overview(m) {
  return {
    collected_at: ago(20 * 1000),
    data_status: m.state.partial ? "partial" : "ok",
    nodes_online: m.state.partial ? 4 : 5, nodes_total: 5,
    vms_running: 186, vms_stopped: 58, lxc_running: 141, lxc_stopped: 37,
    cpu_used: 171.6, cpu_total: 384, mem_used: 1180 * GB, mem_total: 2048 * GB, disk_used: 12.4 * 1024 * GB, disk_total: 26 * 1024 * GB,
    nodes: [], top_cpu: [], top_mem: [],
    issues: [
      { kind: "node_offline", severity: "critical", scope: "node", target: "pve-gpu-02", vmid: null, signals: [] },
      { kind: "vm_cpu_high", severity: "warning", scope: "qemu", target: "crypto-lab-test", vmid: 10388, signals: [{ metric: "cpu", value: 96 }] },
    ],
  };
}

function adminMocks(m) {
  const alerts = [
    // Same as the live issue above: listed once.
    { id: "a0d1c3e0-0001-4000-8000-000000000001", scope: "vm", target: "10388", metric: "cpu", value: 95.8, threshold: 90, message: "", created_at: ago(5 * H), resolved_at: null },
    { id: "a0d1c3e0-0002-4000-8000-000000000002", scope: "node", target: "pve-03", metric: "disk", value: 91.4, threshold: 90, message: "", created_at: ago(2 * H), resolved_at: null },
  ];
  const req = (n) => Array.from({ length: n }, (_, i) => ({ id: `req-${i}`, status: "pending" }));
  m.use([
    ["GET", /^\/api\/v1\/monitoring\/overview$/, () => overview(m)],
    ["GET", /^\/api\/v1\/monitoring\/alerts$/, () => alerts],
    ["GET", /^\/api\/v1\/vm-requests\/$/, () => ({ data: req(4), count: 4 })],
    ["GET", /^\/api\/v1\/spec-change-requests\/$/, () => ({ data: req(1), count: 1 })],
    ["GET", /^\/api\/v1\/batch-provision\/pending$/, () => req(2)],
    ["GET", /^\/api\/v1\/ai-api\/requests$/, () => ({ data: req(1), count: 3 })],
    ["GET", /^\/api\/v1\/mining-incidents$/, ({ query }) => (query.status === "suspended"
      ? [{ id: "mi-3", vmid: 10388, avg_cpu: 96.8, window_hours: 6, snapshot_name: "skylab-mining-20261012-0412", status: "suspended", review_note: null, detected_at: ago(5 * H), owner_email: "11156077@ntub.edu.tw" }]
      : [])],
    ["GET", /^\/api\/v1\/jobs\/$/, ({ query }) => (String(query.statuses || "").includes("failed")
      ? { items: [{ id: "vm_request:req-31", status: "failed" }, { id: "template:tpl-7", status: "blocked" }], total: 2 }
      : { items: [], total: 0 })],
    ["POST", /^\/api\/v1\/ai\/pve-log\/chat$/, ({ body }) => {
      const reply = m.L(
        "### Summary\nTwo things need attention right now:\n\n| Target | Problem | Suggestion |\n|---|---|---|\n| **pve-gpu-02** | Node offline since 09:41 | Check the power and the management network of the node |\n| **crypto-lab-test (10388)** | CPU 96% for 6 hours, suspected mining | Already suspended by the mining detector; decide on the incident |\n\nThe other 4 nodes and 325 machines are within the thresholds.",
        "### 診斷摘要\n目前有兩件事需要處理：\n\n| 對象 | 問題 | 建議 |\n|---|---|---|\n| **pve-gpu-02** | 09:41 起節點離線 | 檢查節點電源與管理網路 |\n| **crypto-lab-test (10388)** | CPU 連續 6 小時 96%，疑似挖礦 | 反挖礦已自動暫停，請到事件頁定奪 |\n\n其餘 4 個節點與 325 台機器都在門檻內。",
      );
      const user = body?.messages || (body?.message ? [{ role: "user", content: body.message }] : []);
      return {
        __delay: 300,
        __body: {
          reply,
          tools_called: [
            { tool_call_id: "t1", name: "get_cluster_overview", args: { reason: "nodes" }, result: { nodes_online: 4 } },
            { tool_call_id: "t2", name: "get_resources", args: { reason: "cpu" }, result: { count: 327 } },
          ],
          needs_confirmation: false,
          messages: [...user, { role: "assistant", content: reply }],
          error: null,
        },
      };
    }],
  ]);
}

// ---------------------------------------------------------------- teacher

const dateKey = (ms) => new Date(ms + 8 * H).toISOString().slice(0, 10);

function teacherMocks(m) {
  // The greeting uses the first word of the name; "Prof. Lin" would read "Hi, Prof.".
  m.state.meExtra = { ...m.state.meExtra, full_name: m.L("Chih-Ming Lin", "林志明") };
  const cls =(id, name, status, weekday, start_time, members, nodes, ready, total) => ({
    id, name, status, weekday, start_time, member_count: members,
    start_date: dateKey(NOW - 28 * D), end_date: dateKey(NOW + 95 * D),
    machine_nodes: Array.from({ length: nodes }, (_, i) => ({ node_key: `node-${i}` })),
    ready_machines: ready, total_machines: total,
  });
  const classes = [
    cls("c1a55000-0000-4000-8000-000000000001", m.L("Linux System Administration (IM 2A)", "Linux 系統管理（資管二甲）"), "active", 1, "13:10:00", 28, 1, 26, 28),
    cls("c1a55000-0000-4000-8000-000000000002", m.L("Cloud Computing Practice (IM 3A)", "雲端運算實務（資管三甲）"), "active", 3, "09:10:00", 32, 2, 64, 64),
    cls("c1a55000-0000-4000-8000-000000000003", m.L("Containers and Automation (Evening)", "容器與自動化（夜間班）"), "planning", 2, "18:30:00", 20, 2, 0, 40),
    cls("c1a55000-0000-4000-8000-000000000004", m.L("Database Systems (IM 2B)", "資料庫系統（資管二乙）"), "provisioning", 0, "13:10:00", 30, 1, 12, 30),
    cls("c1a55000-0000-4000-8000-000000000005", m.L("Network Security Practice (CS 4B)", "網路安全實務（資工四乙）"), "pending_review", 4, "10:10:00", 24, 1, 0, 24),
  ];
  m.use([
    ["GET", /^\/api\/v1\/teaching-classes\/?$/, () => classes],
    ["GET", /^\/api\/v1\/course-environments\/published$/, () => []],
  ]);
}

// ---------------------------------------------------------------- student

function studentMocks(m) {
  const base = { owner_name: m.users.student.full_name, owner_email: m.users.student.email, can_control: true, can_manage: true, can_delete: true, public_urls: [], is_placeholder: false, auto_stop_at: null };
  const resources = [
    { ...base, vmid: 10318, name: "jupyter-ml-lab", type: "lxc", status: "running", machine_kind: "personal", allocation_scope: "personal", node: "pve-01",
      os_info: "Ubuntu 22.04 (LXC)", ip_address: "10.20.3.77", uptime: 31 * 3600, cpu: 0.18, mem: 2.6 * GB, maxmem: 4 * GB, expiry_date: later(60 * D) },
    { ...base, vmid: 20117, name: "cls-linux-a-1-07", course_environment_name: m.L("Linux Admin Lab", "Linux 系統管理實驗環境"), type: "lxc", status: "running", machine_kind: "teaching_class", class_relation: "student",
      allocation_scope: "class", can_manage: false, can_delete: false, node: "pve-02", os_info: "Ubuntu 24.04 (LXC)", ip_address: "10.20.8.21", uptime: 50 * 60, cpu: 0.04, mem: 0.6 * GB, maxmem: 2 * GB,
      expiry_date: later(95 * D), auto_stop_at: later(6 * H), auto_stop_reason: "class_end" },
    { ...base, vmid: 10231, name: "ubuntu-desktop", type: "qemu", status: "stopped", machine_kind: "personal", allocation_scope: "personal", node: "pve-02",
      os_info: "Ubuntu 24.04 LTS", ip_address: "10.20.3.41", uptime: 0, cpu: 0, mem: 0, maxmem: 8 * GB, expiry_date: later(100 * D) },
    { ...base, vmid: 10377, name: "cuda-training", type: "qemu", status: "stopped", machine_kind: "personal", allocation_scope: "personal", node: "pve-gpu-01",
      os_info: "Ubuntu 24.04 CUDA", ip_address: "10.20.3.95", uptime: 0, cpu: 0, mem: 0, maxmem: 32 * GB, expiry_date: later(20 * D),
      start_blocked_reason: "window_ended", window_end_at: ago(2 * D) },
  ];
  const today = dateKey(NOW);
  const schedule = [
    { id: "path-linux-a", title: m.L("Linux System Administration (IM 2A)", "Linux 系統管理（資管二甲）"), state: "now", session_date: today,
      start_at: "2026-10-12T01:10:00Z", end_at: "2026-10-12T04:00:00Z", teacher: m.L("Prof. Lin", "林志明"), location: m.L("Info Building B302", "資訊大樓 B302"), total_questions: 8, completed_questions: 3 },
    { id: "path-cloud", title: m.L("Cloud Computing Practice (IM 3A)", "雲端運算實務（資管三甲）"), state: "later", session_date: today,
      start_at: "2026-10-12T05:10:00Z", end_at: "2026-10-12T08:00:00Z", teacher: m.L("Prof. Chen", "陳美玲"), location: m.L("Info Building B201", "資訊大樓 B201"), total_questions: 6, completed_questions: 6 },
    { id: "path-db", title: m.L("Database Systems (IM 2B)", "資料庫系統（資管二乙）"), state: "available", session_date: dateKey(NOW + 3 * D),
      start_at: "2026-10-15T05:10:00Z", end_at: "2026-10-15T08:00:00Z", teacher: m.L("Prof. Lin", "林志明"), location: m.L("Info Building B105", "資訊大樓 B105"), total_questions: 5, completed_questions: 1 },
  ];
  const quick = [
    { id: "qp-1", version_id: "qpv-1", usage_scope: "public", name: m.L("Docker Basics", "Docker 入門練習"), description: m.L("Docker and Compose on a single host", "單機 Docker 與 Compose 練習環境"), duration_hours: 4, per_student: true,
      nodes: [{ id: "n1", node_key: "docker", name: "docker-lab", role: "primary", resource_type: "lxc", memory_mb: 2048, disk_gb: 16, cpu: 2 }] },
    { id: "qp-2", version_id: "qpv-2", usage_scope: "public", name: m.L("Python Data Analysis", "Python 資料分析"), description: m.L("JupyterLab with pandas", "JupyterLab＋pandas 練習環境"), duration_hours: 6, per_student: true,
      nodes: [{ id: "n2", node_key: "jupyter", name: "jupyter", role: "primary", resource_type: "lxc", memory_mb: 4096, disk_gb: 20, cpu: 2 }] },
    { id: "qp-3", version_id: "qpv-3", usage_scope: "public", name: m.L("Web Server Lab", "網站伺服器實作"), description: m.L("Nginx reverse proxy with a database", "Nginx 反向代理＋資料庫"), duration_hours: 3, per_student: true,
      nodes: [
        { id: "n3", node_key: "web", name: "web", role: "primary", resource_type: "lxc", memory_mb: 2048, disk_gb: 16, cpu: 2 },
        { id: "n4", node_key: "db", name: "db", role: "secondary", resource_type: "lxc", memory_mb: 2048, disk_gb: 20, cpu: 2 },
      ] },
  ];
  m.use([
    ["GET", /^\/api\/v1\/resources\/my$/, () => resources],
    ["GET", /^\/api\/v1\/resources\/(\d+)$/, ({ mt }) => resources.find((r) => r.vmid === Number(mt[1])) || { __status: 404, __body: { detail: "Not found" } }],
    ["POST", /^\/api\/v1\/resources\/\d+\/shutdown$/, () => ({ message: "ok" })],
    ["GET", /^\/api\/v1\/courses\/schedule$/, () => schedule],
    ["GET", /^\/api\/v1\/quick-practice\/templates$/, () => quick],
    ["GET", /^\/api\/v1\/quick-practice\/sessions\/my$/, () => []],
  ]);
  const prompt = "\x1b[1;32mstudent@jupyter-ml-lab\x1b[0m:\x1b[1;34m~\x1b[0m$ ";
  m.ws = (p, ws) => {
    if (!p.startsWith("/ws/terminal/")) return;
    ws.send("OK" + "Welcome to Ubuntu 22.04.5 LTS (GNU/Linux 6.8.12-4-pve x86_64)\r\n\r\n * Documentation:  https://help.ubuntu.com\r\n\r\nLast login: Mon Oct 12 09:12:41 2026 from 10.20.0.3\r\n" + prompt
      + "jupyter lab list\r\nCurrently running servers:\r\nhttp://0.0.0.0:8888/ :: /home/student/notebooks\r\n" + prompt);
    ws.onMessage(() => {});
  };
}

const machineCard = (s, name) => s.page.locator("article").filter({ has: s.page.getByRole("heading", { name, exact: true }) }).first();
// Page header down to the bottom of the stats cards.
async function headerCrop(s) {
  const b = await s.page.locator('[data-guide="admin-home-stats"]').boundingBox();
  return { x: 330, y: 0, width: 1110, height: Math.round(b.y + b.height + 24) };
}
// A target that outlines only the text and icon inside a full-width button
// (s.shot only calls these methods when an explicit crop is given).
const contentOf = (loc) => ({
  scrollIntoViewIfNeeded: () => loc.scrollIntoViewIfNeeded(),
  boundingBox: () => loc.evaluate((el) => {
    const r = document.createRange();
    r.selectNodeContents(el);
    const b = r.getBoundingClientRect();
    return { x: b.x - 6, y: b.y - 4, width: b.width + 12, height: b.height + 8 };
  }),
  toString: () => String(loc),
});

export default {
  page: "home",
  role: "admin",
  sections: [
    // s01 Check how fresh the data is
    {
      mocks: adminMocks,
      run: async (s) => {
        await s.go("/dashboard", { wait: 900 });
        const ts = s.page.getByText(s.t("Updated ", "更新於 ")).first();
        await s.shot(1, ts, { crop: await headerCrop(s) });
        s.m.state.partial = true;
        await s.go("/dashboard", { wait: 900 });
        await shotNew(s, 2, s.page.locator('[role="status"][title]').first(), { crop: await headerCrop(s) });
      },
    },
    // s02 Read the platform numbers
    {
      mocks: adminMocks,
      run: async (s) => {
        await s.go("/dashboard", { wait: 900 });
        const stats = s.page.locator('[data-guide="admin-home-stats"]');
        await s.shot(0, await stats.locator("button").all(), { crop: stats, pad: 16 });
      },
    },
    // s03 Ask the AI Operations Assistant
    {
      mocks: adminMocks,
      run: async (s) => {
        await s.go("/dashboard", { wait: 900 });
        const assistant = s.page.locator('[data-guide="admin-home-assistant"]');
        const suggestion = assistant.getByRole("button", { name: s.t("Which nodes or VMs are currently abnormal?", "目前有哪些節點或 VM 異常？") });
        await s.click(suggestion, { wait: 300 });
        const input = assistant.locator("input").first();
        await s.shot(1, [input, suggestion], { crop: assistant, pad: 24 });
        const start = assistant.getByRole("button", { name: s.t("Start Asking", "開始詢問") });
        await s.shot(2, start, { crop: assistant, pad: 24 });
        await s.click(start, { wait: 1500 });
        await s.page.waitForTimeout(800);
        const expand = assistant.getByRole("button", { name: s.t("Expand Chat", "放大對話") });
        await shotNew(s, 3, expand, { crop: assistant, pad: 16 });
        await s.click(expand, { wait: 800 });
        await shotNew(s, 3, assistant.getByRole("button", { name: s.t("Back to Overview", "回到總覽") }), { crop: assistant, pad: 16 });
        await s.shot(4, assistant.getByRole("button", { name: s.t("Ask Another Question", "重新問一題") }), { crop: assistant, pad: 16 });
      },
    },
    // s04 Work through "Handle now"
    {
      mocks: adminMocks,
      run: async (s) => {
        await s.go("/dashboard", { wait: 900 });
        const urgent = s.page.locator('[data-guide="admin-home-urgent"]');
        await s.shot(0, [
          urgent.locator("[class*='_rowList_']").first(),
          contentOf(urgent.getByRole("button", { name: s.t("View platform monitoring", "查看平台監控") })),
        ], { crop: urgent, pad: 16 });
      },
    },
    // s05 Clear the reviews in "Sometime today"
    {
      mocks: adminMocks,
      run: async (s) => {
        await s.go("/dashboard", { wait: 900 });
        const today = s.page.locator('[data-guide="admin-home-today"]');
        // One outline around the rows (the list itself is padded out to a fixed height).
        const rows = await today.locator("[class*='_rowList_'] > *").all();
        const rowsBox = {
          scrollIntoViewIfNeeded: () => rows[0].scrollIntoViewIfNeeded(),
          boundingBox: async () => {
            const bs = await Promise.all(rows.map((r) => r.boundingBox()));
            const x = Math.min(...bs.map((b) => b.x));
            const y = Math.min(...bs.map((b) => b.y));
            return { x, y, width: Math.max(...bs.map((b) => b.x + b.width)) - x, height: Math.max(...bs.map((b) => b.y + b.height)) - y };
          },
        };
        await s.shot(0, rowsBox, { crop: today, pad: 16 });
      },
    },
    // s06 Create a class
    {
      role: "teacher",
      mocks: teacherMocks,
      run: async (s) => {
        await s.go("/dashboard", { wait: 900 });
        const create = s.page.getByRole("button", { name: s.t("Create Class", "建立班級") });
        const metrics = await s.page.locator('[data-guide="teacher-home-metrics"]').boundingBox();
        await s.shot(1, create, { crop: { x: 330, y: 0, width: 1110, height: Math.round(metrics.y + metrics.height + 24) } });
        await s.click(create, { wait: 1500 });
        await shotNew(s, 2, s.page.locator('[data-guide="class-setup-stepper"]'), { crop: "viewport" });
      },
    },
    // s07 Read the summary cards
    {
      role: "teacher",
      mocks: teacherMocks,
      run: async (s) => {
        await s.go("/dashboard", { wait: 900 });
        const metrics = s.page.locator('[data-guide="teacher-home-metrics"]');
        await s.shot(0, await metrics.locator("button").all(), { crop: metrics, pad: 16 });
      },
    },
    // s08 Follow your upcoming classes
    {
      role: "teacher",
      mocks: teacherMocks,
      run: async (s) => {
        await s.go("/dashboard", { wait: 900 });
        const panel = s.page.locator('[data-guide="teacher-home-classes"]');
        await s.shot(0, [panel.locator("[class*='_classList_']").first(), panel.getByRole("button", { name: s.t("All Classes", "全部班級") })], { crop: panel, pad: 24 });
      },
    },
    // s09 Enter one of your machines
    {
      role: "student",
      mocks: studentMocks,
      run: async (s) => {
        await s.go("/dashboard", { wait: 1000 });
        const grid = s.page.locator('section[aria-labelledby="recent-machines-title"]');
        await s.shot(1, [
          machineCard(s, "jupyter-ml-lab").locator("button").first(),
          machineCard(s, "ubuntu-desktop").locator("button").first(),
          machineCard(s, "cuda-training").locator("button").first(),
        ], { crop: grid, pad: 16 });
        await s.click(machineCard(s, "jupyter-ml-lab").locator("button").first(), { wait: 1500 });
        // The terminal fills the screen; keep its title bar and the first lines of output.
        const term = await s.page.locator('[role="dialog"]').last().boundingBox();
        const title = s.page.locator('[role="dialog"] [class*="_headerTitle_"]').last();
        await shotNew(s, 2, title, { crop: { x: term.x - 16, y: term.y - 16, width: term.width + 32, height: 300 } });
      },
    },
    // s10 Shut down a machine
    {
      role: "student",
      mocks: studentMocks,
      run: async (s) => {
        await s.go("/dashboard", { wait: 1000 });
        const card = machineCard(s, "jupyter-ml-lab");
        const power = card.getByRole("button", { name: s.t("Shut down jupyter-ml-lab", "將 jupyter-ml-lab 關機") });
        await s.shot(1, power, { crop: card, pad: 12 });
        await s.click(power, { wait: 600 });
        const dlg = s.page.locator('[role="dialog"],[role="alertdialog"]').last();
        await shotNew(s, 2, dlg.getByRole("button", { name: s.t("Shut down", "關機"), exact: true }));
      },
    },
    // s11 Open a machine's details or all your machines
    {
      role: "student",
      mocks: studentMocks,
      run: async (s) => {
        await s.go("/dashboard", { wait: 1000 });
        const info = machineCard(s, "jupyter-ml-lab").locator("button").last();
        const row = await machineCard(s, "jupyter-ml-lab").boundingBox();
        await s.shot(0, [info, s.page.locator('button[class*="_headerButton_"]').first()], { crop: { x: 330, y: 0, width: 1110, height: Math.round(row.y + row.height + 20) } });
      },
    },
    // s12 Open a class
    {
      role: "student",
      mocks: studentMocks,
      run: async (s) => {
        await s.go("/dashboard", { wait: 1000 });
        const section = s.page.locator('[data-guide="home-schedule"]');
        await section.evaluate((el) => el.scrollIntoView({ block: "center" }));
        await s.shot(1, section.locator("button[data-state]").first(), { crop: section, pad: 24, scroll: false });
        await s.shot(2, section.getByRole("button", { name: s.t("View all courses", "查看所有課程") }), { crop: section, pad: 24, scroll: false });
      },
    },
    // s13 Start a quick practice environment
    {
      role: "student",
      mocks: studentMocks,
      run: async (s) => {
        await s.go("/dashboard", { wait: 1000 });
        const section = s.page.locator('[data-guide="home-quick-templates"]');
        await section.scrollIntoViewIfNeeded();
        await s.shot(0, section.locator("a, button").first(), { crop: section, pad: 24 });
      },
    },
    // s14 Request your own research environment
    {
      role: "student",
      mocks: studentMocks,
      run: async (s) => {
        await s.go("/dashboard", { wait: 1000 });
        const link = s.page.locator('[data-guide="home-other-needs"] button').first();
        await s.shot(0, link, { crop: s.page.locator('[data-guide="home-other-needs"]'), pad: 22 });
      },
    },
  ],
};
