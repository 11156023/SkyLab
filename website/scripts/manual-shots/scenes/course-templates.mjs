// Manual page: manual/course-templates.md (Teaching Environments list and editor)
import { D, MIN, NOW, ago } from "../lib/mock.mjs";

const TEACHER_ID = "7d3c1a2e-0002-4a00-9000-000000000002";

// ---------------------------------------------------------------- mock data

function machineTemplates(m) {
  const tpl = (id, vmid, name, description, type, cores, memory, disk, status = "ready", days = 30) => ({
    id, pve_vmid: vmid, name, description, owner_id: TEACHER_ID, node: "pve-01", storage: "local-lvm",
    resource_type: type, status, visibility: "private", default_cores: cores, default_memory: memory, default_disk: disk,
    password_settable: true, requires_gpu: false, attachment_count: 0, source_vmid: null, version: 1, error_message: null, pve_exists: true,
    created_at: ago(days * D), updated_at: ago(days * D),
  });
  return [
    tpl("tpl-n8n", 9111, "n8n", m.L("n8n 2.x workflow automation (Docker Compose)", "n8n 2.x 工作流程自動化平台（Docker Compose）"), "qemu", 2, 4096, 32, "ready", 9),
    tpl("tpl-pg", 9112, "PostgreSQL", m.L("PostgreSQL 16 with remote access enabled", "PostgreSQL 16，已開啟遠端連線"), "lxc", 2, 2048, 16, "ready", 9),
    tpl("tpl-linux", 9113, "Linux", m.L("Ubuntu 24.04 practice host with psql and cron", "Ubuntu 24.04 實作主機，含 psql、cron"), "lxc", 1, 1024, 12, "ready", 9),
    tpl("tpl-ubuntu", 9101, m.L("Ubuntu 24.04 Base", "Ubuntu 24.04 基礎環境"), m.L("Clean Ubuntu 24.04 with common tools", "乾淨的 Ubuntu 24.04，含常用系統工具"), "lxc", 1, 1024, 12, "ready", 40),
    tpl("tpl-kali", 9104, "Kali Linux 2026.3", m.L("Kali Linux with the default toolset", "Kali Linux，含預設工具組"), "qemu", 2, 4096, 40, "ready", 25),
  ];
}

const node = (key, tplId, name, role, type, cpu, memGb, disk, x, y = 120) => ({
  id: `n-${key}`, node_key: key, source_type: "template", source_template_id: tplId, custom_image_ref: null, custom_username: null,
  custom_unprivileged: true, name, role, resource_type: type, cpu, memory_mb: memGb * 1024, disk_gb: disk, network: "lab-net",
  position_x: x, position_y: y,
});

function environments(m) {
  const env = (id, name, description, usage, status, version, classes, nodes, extra = {}) => {
    const sum = nodes.reduce((a, n) => ({ cpu: a.cpu + n.cpu, mem: a.mem + n.memory_mb, disk: a.disk + n.disk_gb }), { cpu: 0, mem: 0, disk: 0 });
    return {
      id, version_id: `${id}-v${version}`, owner_id: TEACHER_ID, name, description, usage_scope: usage, max_concurrent_sessions: null,
      files: [], version, status, configuration_hash: "c0ffee", draft_data: null, classes, peer_policy: "explicit",
      created_at: ago(60 * D), updated_at: ago(3 * D), published_at: status === "published" ? ago(3 * D) : null,
      nodes: nodes.map((n, i) => ({ ...n, version_id: `${id}-v${version}`, sort_order: i })),
      edges: [], publications: [],
      per_student: { machines: nodes.length, cpu_cores: sum.cpu, memory_mb: sum.mem, disk_gb: sum.disk, ip_count: nodes.length, network_count: 1 },
      ...extra,
    };
  };
  const n8nNodes = [
    node("node-n8n", "tpl-n8n", "n8n", m.L("Workflow server", "工作流程伺服器"), "qemu", 2, 4, 32, 640, 130),
    node("node-pg", "tpl-pg", "PostgreSQL", m.L("Database", "資料庫"), "lxc", 2, 2, 16, 320, 20),
    node("node-linux", "tpl-linux", "Linux", m.L("Practice host", "實作主機"), "lxc", 1, 1, 12, 320, 240),
  ];
  return [
    env("env-linux", m.L("Linux System Administration Lab", "Linux 系統管理實驗環境"), m.L("Ubuntu 24.04 with Docker and Nginx; one practice host per student", "Ubuntu 24.04＋Docker＋Nginx，每位學生一台實作機"), "course", "published", 3, 2,
      [node("web", "tpl-ubuntu", "web-server", m.L("Practice host", "實作主機"), "lxc", 2, 2, 16, 60)]),
    env("env-web", m.L("Full-stack Web Development", "Web 全端開發環境"), m.L("Node.js 22 app server with PostgreSQL", "Node.js 22 應用伺服器＋PostgreSQL"), "both", "published", 2, 1,
      [node("app", "tpl-ubuntu", "app", m.L("App server", "應用伺服器"), "lxc", 2, 4, 20, 640, 120), node("db", "tpl-pg", "db", m.L("Database", "資料庫"), "lxc", 1, 2, 16, 320, 120)],
      { edges: [{ id: "e-web-1", source_node_key: "app", target_node_key: "db", direction: "one_way", protocol: "tcp", port: 5432 }],
        files: [{ id: "f-web-1", filename: m.L("Project setup guide.pdf", "專案環境建置說明.pdf"), content_type: "application/pdf", size_bytes: 942080, created_at: ago(20 * D) }],
        publications: [{ id: "p-web-1", node_key: "app", mode: "domain", port: 3000, protocol: "tcp", hostname_prefix: "webapp", zone_id: "z1", enable_https: true }] }),
    env("env-docker", m.L("Docker Container Basics", "Docker 容器入門"), m.L("Self-paced practice: images, containers and volumes", "自學練習：映像、容器與資料卷"), "quick_practice", "published", 1, 0,
      [node("docker", "tpl-ubuntu", "docker-host", m.L("Practice host", "實作主機"), "lxc", 2, 2, 20, 60)]),
    env("env-n8n", m.L("n8n Automation Workshop", "n8n 自動化實務環境"), m.L("n8n connected to PostgreSQL, with a Linux practice host", "n8n 串接 PostgreSQL，搭配 Linux 實作主機"), "course", "draft", 1, 0, n8nNodes,
      { edges: [{ id: "e-n8n-1", source_node_key: "node-n8n", target_node_key: "node-pg", direction: "one_way", protocol: "tcp", port: 5432 }],
        publications: [{ id: "p-n8n-1", node_key: "node-n8n", mode: "domain", port: 5678, protocol: "tcp", hostname_prefix: "n8n", zone_id: "z1", enable_https: true }] }),
    env("env-sec", m.L("Network Security Attack and Defense", "網路安全攻防演練"), m.L("Kali attacker and a vulnerable web target", "Kali 攻擊機與含弱點的網站靶機"), "course", "draft", 1, 0,
      [node("kali", "tpl-kali", "kali", m.L("Attacker", "攻擊機"), "qemu", 2, 4, 40, 60), node("target", "tpl-ubuntu", "dvwa-target", m.L("Target", "靶機"), "lxc", 1, 1, 12, 360)]),
    env("env-untitled", "", null, "course", "draft", 1, 0, [node("node-a", "tpl-ubuntu", "ubuntu", m.L("Environment machine", "環境機器"), "lxc", 1, 1, 12, 60)], { updated_at: ago(40 * MIN) }),
  ];
}

// Turns a draft configuration (the editor's PUT body) into an environment response.
function applyConfiguration(env, cfg) {
  if (!cfg) return env;
  return {
    ...env, name: cfg.name ?? env.name, description: cfg.description ?? null, usage_scope: cfg.usage_scope ?? env.usage_scope,
    nodes: (cfg.nodes ?? []).map((n, i) => ({ id: `n-${n.node_key}`, version_id: env.version_id, ...n, sort_order: i })),
    edges: (cfg.edges ?? []).map((e, i) => ({ id: `e-${env.id}-${i}`, version_id: env.version_id, ...e })),
    publications: (cfg.publications ?? []).map((p, i) => ({ id: `p-${env.id}-${i}`, version_id: env.version_id, ...p, sort_order: i })),
    updated_at: ago(0),
  };
}

function registerEnvironments(m) {
  m.state.envs = Object.fromEntries(environments(m).map((e) => [e.id, e]));
  m.state.uploads = [];
  const get = (id) => m.state.envs[id];
  m.use([
    ["GET", /^\/api\/v1\/course-environments$/, () => Object.values(m.state.envs)],
    ["GET", /^\/api\/v1\/course-environments\/published$/, () => Object.values(m.state.envs).filter((e) => e.status === "published")],
    ["POST", /^\/api\/v1\/course-environments\/drafts$/, ({ body }) => {
      const base = { ...environments(m).find((e) => e.id === "env-untitled"), id: "env-new", version_id: "env-new-v1", nodes: [], files: [], created_at: ago(0) };
      const env = applyConfiguration(base, body?.configuration);
      m.state.envs[env.id] = env;
      return { __status: 201, __body: env };
    }],
    ["PUT", /^\/api\/v1\/course-environments\/([^/]+)\/draft$/, ({ mt, body }) => {
      const env = applyConfiguration(get(mt[1]), body?.configuration);
      m.state.envs[env.id] = env;
      return env;
    }],
    ["POST", /^\/api\/v1\/course-environments\/([^/]+)\/publish$/, ({ mt }) => {
      const env = { ...get(mt[1]), status: "published", published_at: ago(0) };
      m.state.envs[env.id] = env;
      return { __delay: 300, __body: env };
    }],
    ["PATCH", /^\/api\/v1\/course-environments\/([^/]+)\/basics$/, ({ mt, body }) => {
      const env = { ...get(mt[1]), name: body.name, description: body.description, usage_scope: body.usage_scope };
      m.state.envs[env.id] = env;
      return env;
    }],
    ["POST", /^\/api\/v1\/course-environments\/([^/]+)\/files$/, ({ mt }) => {
      const env = get(mt[1]);
      const [filename, size_bytes, content_type] = m.state.uploads.shift() ?? ["document.pdf", 120000, "application/pdf"];
      const files = [...(env.files ?? []), { id: `f-${(env.files ?? []).length + 1}`, filename, size_bytes, content_type, created_at: ago(0) }];
      m.state.envs[env.id] = { ...env, files };
      return m.state.envs[env.id];
    }],
    ["DELETE", /^\/api\/v1\/course-environments\/([^/]+)\/files\/([^/]+)$/, ({ mt }) => {
      const env = get(mt[1]);
      m.state.envs[env.id] = { ...env, files: env.files.filter((f) => f.id !== mt[2]) };
      return m.state.envs[env.id];
    }],
    ["GET", /^\/api\/v1\/course-environments\/([^/]+)$/, ({ mt }) => get(mt[1]) ?? { __status: 404, __body: { detail: "Not found" } }],
    ["DELETE", /^\/api\/v1\/course-environments\/([^/]+)$/, ({ mt }) => { delete m.state.envs[mt[1]]; return undefined; }],

    ["GET", /^\/api\/v1\/templates\/$/, () => { const data = machineTemplates(m); return { data, count: data.length }; }],
    ["GET", /^\/api\/v1\/vm\/templates$/, () => [
      { vmid: 9001, name: "ubuntu-24.04-cloudinit", node: "pve-02", ostype: "l26", is_windows: false, cores: 2, memory_mb: 4096, disk_gb: 32 },
      { vmid: 9002, name: "debian-13-cloudinit", node: "pve-02", ostype: "l26", is_windows: false, cores: 2, memory_mb: 2048, disk_gb: 20 },
    ]],
    ["GET", /^\/api\/v1\/lxc\/templates$/, () => [
      { volid: "local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst", format: "tzst", size: 141557760, nodes: ["pve-01"] },
      { volid: "local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst", format: "tzst", size: 126877696, nodes: ["pve-01"] },
    ]],
    ["GET", /^\/api\/v1\/reverse-proxy\/setup-context$/, () => ({
      enabled: true, gateway_ready: true, cloudflare_ready: true, reasons: [],
      zones: [{ id: "z1", name: "skylab.ntub.edu.tw" }, { id: "z2", name: "lab.ntub.edu.tw" }],
      default_dns_target_type: null, default_dns_target_value: null,
    })],
  ]);
}

// ---------------------------------------------------------------- helpers

const editorUrl = (id, tab) => `/course-template-management/${id}${tab ? `?tab=${tab}` : ""}`;
const flowNode = (s, name) => s.page.locator(".react-flow__node", { hasText: name }).first();
const button = (s, en, zh) => s.page.getByRole("button", { name: s.t(en, zh), exact: true }).first();

async function openMachines(s, id) {
  await s.go(editorUrl(id, "machines"), { wait: 900 });
  await s.page.locator(".react-flow__node").first().waitFor();
  await s.settle(500);
}

async function openConnectionDialog(s) {
  await s.click(button(s, "Add connection", "新增連線"), { wait: 500 });
  return s.page.getByRole("dialog").last();
}

export default {
  page: "course-templates",
  role: "teacher",
  mocks(m) { registerEnvironments(m); },
  sections: [
    // s01 Browse the list
    async (s) => {
      await s.go("/course-template-management", { wait: 700 });
      const nav = s.page.locator("aside nav");
      await s.shot(1, [nav.getByRole("button", { name: s.t("Teaching", "教學"), exact: true }), nav.getByRole("button", { name: s.t("Teaching Environments", "教學環境"), exact: true })], { crop: { x: 0, y: 0, width: 1440, height: 560 } });
      await s.shot(2, s.page.locator('[data-guide="course-tpl-controls"] > *').first());
      await s.shot(4, [s.page.locator("thead tr").first(), s.page.locator("tbody tr", { hasText: s.t("Untitled environment", "無環境名稱") }).locator("td").first()]);
      const row = s.page.locator("tbody tr", { hasText: s.t("Full-stack Web Development", "Web 全端開發環境") }).first();
      await s.shot(5, row.getByRole("button", { name: s.t("Open template", "開啟模板") }));
      const search = s.page.getByPlaceholder(s.t("Search environment name or description", "搜尋環境名稱或說明"));
      await s.fill(search, "Linux");
      await s.shot(3, search.locator(".."), { crop: { x: 330, y: 0, width: 1110, height: 340 }, newFrame: true });
    },
    // s02 Create an environment
    {
      mocks(m) {
        m.use([
          ["POST", /^\/api\/v1\/course-environments\/drafts$/, () => ({ __status: 503, __body: { detail: m.L("Database is temporarily unavailable.", "資料庫暫時無法連線。") } })],
        ]);
      },
      run: async (s) => {
        await s.go("/course-template-management", { wait: 700 });
        await s.shot(1, button(s, "Create teaching environment", "建立教學環境"), { crop: { x: 330, y: 0, width: 1110, height: 305 } });
        await s.click(button(s, "Create teaching environment", "建立教學環境"), { wait: 800 });
        await s.fill(s.page.getByPlaceholder(s.t("e.g. Linux three-tier classroom environment", "例如：Linux 三層式上課環境")), s.t("Database Design Lab", "資料庫設計實驗環境"));
        await s.page.waitForTimeout(1200);
        await s.page.locator("[data-sonner-toast]").first().waitFor({ state: "detached", timeout: 8000 }).catch(() => {});
        await s.settle(300);
        const alert = s.page.getByRole("alert").filter({ hasText: s.t("Draft not synced", "草稿尚未同步") }).first();
        await s.shot(2, alert.getByRole("button", { name: s.t("Retry", "重試") }), { crop: { x: 330, y: 0, width: 1110, height: 420 } });
        await s.shot(3, button(s, "Back", "返回"), { crop: { x: 330, y: 0, width: 1110, height: 420 } });
      },
    },
    // s03 Fill in the basic info
    async (s) => {
      s.m.state.uploads.push(
        [s.t("Lab handout - week 1.pdf", "第一週實作講義.pdf"), 1843200, "application/pdf"],
        [s.t("Database schema.sql", "資料表結構.sql"), 6144, "application/sql"],
      );
      await s.go("/course-template-management/new", { wait: 800 });
      const name = s.page.getByPlaceholder(s.t("e.g. Linux three-tier classroom environment", "例如：Linux 三層式上課環境"));
      await s.fill(name, s.t("Database Design Lab", "資料庫設計實驗環境"), { wait: 1200 });
      const usage = s.page.locator('[data-guide="course-tpl-editor-usage"] select');
      await usage.selectOption("both");
      const desc = s.page.locator("textarea").first();
      await s.fill(desc, s.t("PostgreSQL server and a client host for the database design course: schema design, SQL queries and backups.", "資料庫設計課用：PostgreSQL 伺服器加一台用戶端主機，練習資料表設計、SQL 查詢與備份。"), { wait: 1200 });
      await s.page.locator('[data-guide="course-tpl-editor-files"] input[type="file"]').setInputFiles([
        { name: "a.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4") },
        { name: "b.sql", mimeType: "application/sql", buffer: Buffer.from("create table t();") },
      ]);
      await s.settle(800);
      await s.page.locator("[data-sonner-toast]").first().waitFor({ state: "detached", timeout: 8000 }).catch(() => {});
      await s.settle(300);
      const crop = { x: 330, y: 0, width: 1110, height: 810 };
      await s.shot(1, name, { crop });
      await s.shot(2, usage, { crop });
      await s.shot(3, desc, { crop });
      await s.shot(4, s.page.locator('[data-guide="course-tpl-editor-files"]'), { crop });
      await s.shot(5, s.page.locator('[data-guide="course-tpl-editor-to-machines"]'), { crop });
    },
    // s04 Add machines
    {
      mocks(m) { const e = m.state.envs["env-sec"]; m.state.envs["env-sec"] = { ...e, nodes: [] }; },
      run: async (s) => {
        await s.go(editorUrl("env-sec", "machines"), { wait: 900 });
        const bar = s.page.locator('[data-guide="course-tpl-editor-add-bar"]');
        const selects = bar.locator("select");
        const add = bar.getByRole("button", { name: s.t("Add machine", "加入機器") });
        await selects.nth(1).selectOption("tpl-kali");
        await s.settle(200);
        const crop = { x: 330, y: 0, width: 1110, height: 900 };
        await s.shot(1, [selects.nth(0), selects.nth(1)], { crop: { x: 330, y: 0, width: 1110, height: 550 } });
        await s.shot(2, add, { crop: { x: 330, y: 0, width: 1110, height: 550 } });
        // The editor names new machines after Date.now(); move the frozen clock on per machine.
        let tick = 0;
        for (const id of ["tpl-kali", "tpl-ubuntu", "tpl-linux"]) {
          await s.page.clock.setFixedTime(new Date(NOW + ++tick * 1000));
          await selects.nth(1).selectOption(id);
          await s.click(add, { wait: 500 });
        }
        await s.settle(400);
        await s.shot(2, s.page.locator(".react-flow__node.selected").first(), { crop, newFrame: true });
        await s.shot(3, [s.page.getByText(s.t("3 / 3 machines", "3 / 3 台")), bar.getByRole("button", { name: s.t("Limit reached", "已達上限") })], { crop });
      },
    },
    // s05 Edit a machine
    async (s) => {
      await openMachines(s, "env-n8n");
      const pg = flowNode(s, "PostgreSQL");
      await s.click(pg);
      const inspector = s.page.locator("aside").filter({ has: s.page.locator('input[type="range"]') }).last();
      const inputs = inspector.locator('input:not([type="range"])');
      await s.fill(inputs.nth(0), "postgres-db");
      await s.fill(inputs.nth(1), s.t("Course database", "課程資料庫"));
      const ranges = inspector.locator('input[type="range"]');
      await ranges.nth(0).fill("4");
      await ranges.nth(1).fill("4");
      await ranges.nth(2).fill("32");
      await s.settle(300);
      // Drag the Linux machine to the right.
      const linux = flowNode(s, "Linux");
      const b = await linux.boundingBox();
      await s.page.mouse.move(b.x + 40, b.y + b.height / 2);
      await s.page.mouse.down();
      await s.page.mouse.move(b.x + 220, b.y + b.height / 2 + 10, { steps: 12 });
      await s.page.mouse.up();
      await s.settle(400);
      const crop = { x: 330, y: 160, width: 1110, height: 620 };
      await s.shot(1, [flowNode(s, "postgres-db"), inspector.locator("strong").first().locator("..")], { crop });
      await s.shot(2, [inputs.nth(0), inputs.nth(1)], { crop });
      await s.shot(3, inspector.locator('input[type="range"]').first().locator("../.."), { crop });
      await s.shot(4, inspector.getByRole("button", { name: s.t("Remove node", "移除節點") }), { crop });
      await s.shot(5, flowNode(s, "Linux"), { crop });
    },
    // s06 Connect machines
    async (s) => {
      await openMachines(s, "env-n8n");
      const dialog = await openConnectionDialog(s);
      const card = dialog.getByRole("button", { name: new RegExp(s.t("Connect two machines", "讓兩台機器互通")) });
      await s.shot(1, [card, s.page.locator('[data-guide="course-tpl-editor-add-bar"]').getByRole("button", { name: s.t("Add connection", "新增連線") })], { crop: { x: 380, y: 210, width: 1000, height: 400 } });
      await s.click(card);
      await dialog.locator("#cd-peer-source").selectOption({ label: "Linux" });
      await dialog.locator("#cd-peer-target").selectOption({ label: "PostgreSQL" });
      const ports = dialog.locator("[data-port-input]");
      await s.fill(ports.nth(0), "5432");
      await s.click(dialog.getByRole("button", { name: s.t("Add port", "新增 port") }));
      await s.fill(dialog.locator("[data-port-input]").nth(1), "22");
      await s.shot(2, [dialog.locator("#cd-peer-source"), dialog.locator("#cd-peer-target"), dialog.locator('[class*="dirToggle"]').first()]);
      await s.shot(3, [dialog.locator("[data-port-input]").nth(0).locator(".."), dialog.locator("[data-port-input]").nth(1).locator(".."), dialog.getByRole("button", { name: s.t("Add port", "新增 port") })]);
      await s.shot(4, dialog.getByRole("button", { name: s.t("Add to environment", "加入環境設定") }));
    },
    // s07 Publish a service
    {
      mocks(m) { const e = m.state.envs["env-n8n"]; m.state.envs["env-n8n"] = { ...e, publications: [] }; },
      run: async (s) => {
        await openMachines(s, "env-n8n");
        const dialog = await openConnectionDialog(s);
        const card = dialog.getByRole("button", { name: new RegExp(s.t("Expose a service", "開放服務給外部")) });
        await s.shot(1, card);
        await s.click(card);
        await dialog.locator("#cd-vm").selectOption({ label: "n8n" });
        await s.fill(dialog.locator("#cd-domain-port"), "5678");
        await s.fill(dialog.locator("#cd-prefix"), "n8n");
        await s.shot(2, [dialog.locator("#cd-vm"), dialog.locator('[class*="dirToggle"]').first(), dialog.locator("#cd-domain-port"), dialog.locator("#cd-prefix"), dialog.locator("#cd-zone")]);
        await s.click(dialog.getByRole("button", { name: s.t("Add to environment", "加入環境設定") }), { wait: 800 });
        // Adding selects the new service and opens its detail panel; close it to see the map.
        await s.click(s.page.locator('[data-guide="course-tpl-editor-topology"]').getByRole("button", { name: s.t("Close", "關閉"), exact: true }), { wait: 500 });
        await s.shot(3, [flowNode(s, "n8n"), s.page.locator(".react-flow__node-gateway").first()], { crop: { x: 360, y: 280, width: 1020, height: 480 } });
      },
    },
    // s08 Review or delete a connection
    async (s) => {
      await openMachines(s, "env-n8n");
      const label = s.page.locator(".react-flow__edgelabel-renderer").getByText("PostgreSQL (5432)").first();
      // The label does not take pointer events; clicking the line itself selects it.
      await s.page.locator('[data-testid="rf__edge-e-n8n-1"]').click({ force: true });
      await s.settle(600);
      const head = s.page.locator('[data-guide="course-tpl-editor-topology"]').getByText(s.t("Connection details", "連線內容"), { exact: true });
      const del = s.page.getByRole("button", { name: new RegExp(s.t("Delete connection", "刪除連線")) }).last();
      const crop = { x: 360, y: 280, width: 1020, height: 480 };
      await s.shot(1, [label, head], { crop });
      await s.shot(2, del, { crop });
    },
    // s09 Publish the environment
    {
      // A draft whose machines were all removed: Publish stays on Machine Configuration and says why.
      mocks(m) { const e = m.state.envs["env-sec"]; m.state.envs["env-sec"] = { ...e, nodes: [], edges: [], publications: [] }; },
      run: async (s) => {
      // Three frames at most: Publish + the check's message / the confirmation / the result.
      await s.go(editorUrl("env-sec", "machines"), { wait: 900 });
      const publish = s.page.locator('[data-guide="course-tpl-editor-publish"]');
      const MAIN = { x: 330, y: 0, width: 1110, height: 900 };
      await s.shot(1, publish, { crop: MAIN });
      await s.click(publish, { wait: 400 });
      const why = s.page.locator("[data-sonner-toast]").first();
      await why.waitFor();
      await s.shot(2, why, { crop: MAIN, scroll: false });
      await openMachines(s, "env-n8n");
      await s.click(s.page.locator('[data-guide="course-tpl-editor-publish"]'), { wait: 500 });
      const confirm = s.page.getByRole("alertdialog").or(s.page.getByRole("dialog")).last();
      await s.shot(3, confirm.getByRole("button", { name: s.t("Publish", "發布"), exact: true }));
      await confirm.getByRole("button", { name: s.t("Publish", "發布"), exact: true }).click();
      const toast = s.page.locator("[data-sonner-toast]").first();
      await toast.waitFor();
      await s.page.waitForTimeout(700);
      await s.shot(4, toast, { crop: { x: 330, y: 0, width: 1110, height: 300 } });
      },
    },
    // s10 Change a published environment
    async (s) => {
      await s.go(editorUrl("env-web"), { wait: 900 });
      const name = s.page.locator('[data-guide="course-tpl-editor-name"] input');
      await s.fill(name, s.t("Full-stack Web Development (Node.js 22)", "Web 全端開發環境（Node.js 22）"));
      await s.page.locator('[data-guide="course-tpl-editor-usage"] select').selectOption("both");
      await s.settle(200);
      const crop = { x: 330, y: 0, width: 1110, height: 900 };
      await s.shot(1, [name, s.page.locator('[data-guide="course-tpl-editor-usage"] select'), s.page.locator("textarea").first(), s.page.locator('[data-guide="course-tpl-editor-files"] li').first()], { crop });
      await s.shot(2, s.page.locator('[data-guide="course-tpl-editor-save-basics"]'), { crop });
      await s.click(s.page.locator('[data-guide="course-tpl-editor-save-basics"]'), { wait: 400 });
      await s.page.locator("[data-sonner-toast]").first().waitFor({ state: "detached", timeout: 8000 }).catch(() => {});
      await s.click(s.page.locator('[data-guide="course-tpl-editor-view-machines"]'), { wait: 900 });
      await s.shot(3, [s.page.locator('[data-guide="course-tpl-editor-topology"]'), s.page.locator('[data-guide="course-tpl-editor-add-bar"] select').last()], { crop: { x: 330, y: 160, width: 1110, height: 700 } });
    },
    // s11 Delete an environment
    async (s) => {
      await s.go("/course-template-management", { wait: 700 });
      const row = s.page.locator("tbody tr", { hasText: s.t("Network Security Attack and Defense", "網路安全攻防演練") }).first();
      const del = row.getByRole("button", { name: s.t("Delete", "刪除") });
      await s.shot(1, del, { crop: { x: 330, y: 0, width: 1110, height: 560 } });
      await s.click(del, { wait: 500 });
      const confirm = s.page.getByRole("alertdialog").or(s.page.getByRole("dialog")).last();
      await s.shot(2, confirm.getByRole("button", { name: s.t("Delete", "刪除"), exact: true }));
    },
  ],
};
