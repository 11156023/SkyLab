// Manual page: manual/quick-practice.md
import { D, H, MIN, ago, later } from "../lib/mock.mjs";

const ENV_DOCKER = "5b1f0c3a-1001-4c00-8000-0000000000a1";
const ENV_WEB = "5b1f0c3a-1002-4c00-8000-0000000000b2";
const ENV_K8S = "5b1f0c3a-1003-4c00-8000-0000000000c3";
const SESSION_ID = "8e4d2b10-2001-4d00-9000-0000000000d1";

const node = (env, i, name, role, type, cpu, memoryMb, diskGb) => ({
  id: `${env.slice(-4)}-${i}`, node_key: name, name, role, resource_type: type, cpu, memory_mb: memoryMb, disk_gb: diskGb,
});

function templates(m) {
  const tpl = (id, version, name, description, nodes) => ({
    id, version_id: `${id.slice(0, -2)}f${version}`, name, description, version, status: "published",
    usage_scope: "quick_practice", published_at: ago(12 * D), duration_hours: 3, nodes,
    per_student: {
      machines: nodes.length,
      cpu_cores: nodes.reduce((s, n) => s + n.cpu, 0),
      memory_mb: nodes.reduce((s, n) => s + n.memory_mb, 0),
      disk_gb: nodes.reduce((s, n) => s + n.disk_gb, 0),
    },
  });
  return [
    tpl(ENV_WEB, 3, m.L("Three-Tier Web Lab", "Web 三層式架構"),
      m.L("Nginx front end, Flask API and PostgreSQL on separate machines.", "Nginx 前端、Flask API 與 PostgreSQL 分別部署在不同機器。"), [
        node(ENV_WEB, 1, "web-frontend", m.L("Web server", "網頁伺服器"), "lxc", 1, 1024, 10),
        node(ENV_WEB, 2, "api-server", m.L("Application", "應用程式"), "lxc", 2, 2048, 16),
        node(ENV_WEB, 3, "postgres-db", m.L("Database", "資料庫"), "lxc", 1, 2048, 20),
      ]),
    tpl(ENV_DOCKER, 2, m.L("Docker Basics", "Docker 入門練習"),
      m.L("A single Docker host for practising images, containers and Compose.", "單機 Docker 與 Compose 練習環境。"), [
        node(ENV_DOCKER, 1, "docker-lab", m.L("Docker host", "Docker 主機"), "lxc", 2, 2048, 16),
      ]),
    tpl(ENV_K8S, 1, m.L("Kubernetes Cluster Lab", "Kubernetes 叢集實作"),
      m.L("One control plane, three workers and an NFS storage node.", "一台控制平面、三台工作節點與一台 NFS 儲存節點。"), [
        node(ENV_K8S, 1, "k8s-master", m.L("Control plane", "控制平面"), "vm", 2, 4096, 30),
        node(ENV_K8S, 2, "k8s-worker-1", m.L("Worker", "工作節點"), "vm", 2, 2048, 20),
        node(ENV_K8S, 3, "k8s-worker-2", m.L("Worker", "工作節點"), "vm", 2, 2048, 20),
        node(ENV_K8S, 4, "k8s-worker-3", m.L("Worker", "工作節點"), "vm", 2, 2048, 20),
        node(ENV_K8S, 5, "nfs-storage", m.L("Storage", "儲存"), "lxc", 1, 1024, 40),
      ]),
  ];
}

// The Web three-tier environment launched by the student, as /quick-practice/sessions/my
// and /resources/ return it. phase: "creating" right after launch, "ready" once running.
function practice(m, phase, stopped = []) {
  const env = templates(m)[0];
  const ips = ["10.30.12.21", "10.30.12.22", "10.30.12.23"];
  const machines = env.nodes.map((n, i) => {
    const created = phase === "ready" || i === 0;
    const status = phase === "ready" ? (stopped.includes(n.name) ? "stopped" : "running") : created ? "running" : "provisioning";
    return {
      id: `qpm-${i + 1}`, node_key: n.node_key, name: n.name, role: n.role, resource_type: n.resource_type,
      request_id: `qpr-${i + 1}`, vmid: created ? 31240 + i : null, status: created ? "running" : "provisioning",
      node: i === 1 ? "pve-02" : "pve-03", ip_address: created ? ips[i] : null, os_info: "Ubuntu 24.04 (LXC)",
      public_url: null, forward_endpoints: [], _status: status, _spec: n,
    };
  });
  const session = {
    id: SESSION_ID, kind: "quick_practice", kind_label: "快速練習", title: env.name, environment_id: env.id,
    environment_version_id: env.version_id, version: env.version,
    status: phase === "ready" ? "running" : "provisioning",
    created_at: phase === "ready" ? ago(40 * MIN) : ago(MIN), expires_at: phase === "ready" ? later(2 * H + 20 * MIN) : later(3 * H - MIN),
    topology_applied_at: phase === "ready" ? ago(35 * MIN) : null, reclaim_started_at: null, error: null, publication_pending: false,
    machines: machines.map(({ _status, _spec, ...rest }) => rest),
  };
  const resources = machines.filter((x) => x.vmid).map((x) => ({
    vmid: x.vmid, request_id: x.request_id, name: `qp-11156001-${x.name}`, type: "lxc", status: x._status,
    machine_kind: "quick_practice", class_relation: null, course_environment_name: x.name,
    owner_name: m.currentUser().full_name, owner_email: m.currentUser().email,
    environment_type: x.role, os_info: "Ubuntu 24.04 (LXC)", ip_address: x.ip_address,
    uptime: x._status === "running" ? 38 * 60 : 0, cpu: x._status === "running" ? 0.06 + 0.04 * Number(x.id.slice(-1)) : 0,
    mem: x._status === "running" ? 0.6 * 1024 ** 3 : 0, maxmem: x._spec.memory_mb * 1024 ** 2, maxcpu: x._spec.cpu,
    expiry_date: session.expires_at, auto_stop_at: null, node: x.node, can_control: true, can_manage: false, can_delete: false,
    public_urls: [], allocation_scope: "quick_practice", is_placeholder: false,
  }));
  return { session, resources };
}

// The AI assistant button floats over the bottom-right corner, where the launch
// button sits; hide it so it does not cover the control being explained.
async function hideFloatingAssistant(s) {
  await s.page.evaluate(() => {
    let el = document.elementFromPoint(window.innerWidth - 50, window.innerHeight - 40);
    while (el && el !== document.body && getComputedStyle(el).position !== "fixed") el = el.parentElement;
    if (el && el !== document.body) el.style.visibility = "hidden";
  });
}

// Endpoints the My Resources page and the student home page call.
function resourcePageMocks(m, getPractice) {
  m.use([
    ["GET", /^\/api\/v1\/resources\/(my)?$/, () => getPractice().resources],
    ["GET", /^\/api\/v1\/quotas\/my-usage$/, () => ({ used_cpu_cores: 6, used_memory_mb: 7168, used_disk_gb: 66, used_instances: 4, quota: { max_cpu_cores: 16, max_memory_mb: 32768, max_disk_gb: 200, max_instances: 8 } })],
    ["GET", /^\/api\/v1\/quick-practice\/sessions\/my$/, () => [getPractice().session]],
    ["GET", /^\/api\/v1\/vm-requests\/my$/, () => ({ data: [], count: 0 })],
    ["GET", /^\/api\/v1\/courses\/schedule$/, () => []],
  ]);
}

export default {
  page: "quick-practice",
  role: "student",
  mocks(m) {
    const list = templates(m);
    m.use([
      ["GET", /^\/api\/v1\/quick-practice\/templates$/, () => list],
      ["GET", /^\/api\/v1\/quick-practice\/templates\/([^/]+)$/, ({ mt }) => list.find((x) => x.id === mt[1]) ?? { __status: 404, __body: { detail: "Not found" } }],
    ]);
  },
  sections: [
    // s01 Pick an environment
    async (s) => {
      await s.go("/quick-create", { wait: 800 });
      const item = s.page.getByRole("button", { name: s.t("Quick Practice", "快速練習"), exact: true }).first();
      await s.shot(1, item);
      const folders = s.page.locator('[data-guide="quick-template-folder"]');
      const web = folders.nth(0);
      await s.shot(2, [web.locator("strong").first(), web.locator('[data-guide="quick-template-meta"]')]);
      const k8s = folders.nth(2);
      // The sheets are tall cards tucked behind the front cover; outline only the
      // part that sticks out (an invisible marker laid over that strip).
      await k8s.evaluate((folder) => {
        const sheets = [...folder.querySelectorAll('[data-guide="quick-template-sheet"]')].map((el) => el.getBoundingClientRect());
        const front = folder.querySelector("strong").parentElement.parentElement.getBoundingClientRect();
        const top = Math.min(...sheets.map((r) => r.top));
        const left = Math.min(...sheets.map((r) => r.left));
        const right = Math.max(...sheets.map((r) => r.right));
        const mark = document.createElement("div");
        mark.id = "qp-sheet-strip";
        mark.style.cssText = `position:fixed;pointer-events:none;left:${left}px;top:${top}px;width:${right - left}px;height:${front.top - top}px`;
        document.body.appendChild(mark);
      });
      await s.shot(3, s.page.locator("#qp-sheet-strip"), { scroll: false });
      await s.shot(4, folders.nth(1));
    },
    // s02 Review the launch confirmation
    async (s) => {
      await s.go("/quick-create", { wait: 800 });
      await s.click(s.page.locator('[data-guide="quick-template-folder"]').first(), { wait: 800 });
      await s.shot(1, s.page.locator('[data-guide="quick-launch-summary"]'));
      await s.shot(2, s.page.locator('[data-guide="quick-launch-machines"]'));
      await s.shot(3, s.page.locator('[data-guide="quick-launch-rules"]'));
    },
    // s03 Launch the environment
    {
      mocks(m) {
        m.state.launched = false;
        resourcePageMocks(m, () => practice(m, "creating"));
        m.use([
          ["POST", /^\/api\/v1\/quick-practice\/templates\/([^/]+)\/launch$/, () => {
            m.state.launched = true;
            return { __delay: m.state.launchDelay ?? 0, __status: 202, __body: practice(m, "creating").session };
          }],
        ]);
      },
      run: async (s) => {
        await s.go("/quick-create", { wait: 800 });
        await s.click(s.page.locator('[data-guide="quick-template-folder"]').first(), { wait: 800 });
        const launch = s.page.locator('[data-guide="quick-launch-actions"] button').last();
        await hideFloatingAssistant(s);
        const rules = await s.page.locator('[data-guide="quick-launch-rules"]').boundingBox();
        const crop = { x: rules.x - 24, y: rules.y - 24, width: rules.width + 48, height: 900 - rules.y + 24 };
        await s.shot(1, launch, { crop });
        s.m.state.launchDelay = 6000;
        await launch.click();
        await s.page.waitForTimeout(400);
        await s.shot(2, launch, { crop, newFrame: true });
        await s.page.waitForURL(/\/my-resources/, { timeout: 15000 });
        const toast = s.page.locator("[data-sonner-toast]").first();
        await toast.waitFor({ timeout: 5000 });
        await s.page.locator("tr", { hasText: templates(s.m)[0].name }).first().waitFor({ timeout: 5000 });
        await s.page.waitForTimeout(400);
        const table = await s.page.locator("table").first().boundingBox();
        await s.shot(3, toast, { crop: { x: 340, y: 0, width: 1100, height: table.y + table.height + 24 }, scroll: false });
      },
    },
    // s04 Reach the machines
    {
      mocks(m) { resourcePageMocks(m, () => practice(m, "ready")); },
      run: async (s) => {
        await s.go("/my-resources", { wait: 900 });
        const title = templates(s.m)[0].name;
        const groupRow = s.page.locator("tr", { hasText: title }).first();
        const table = s.page.locator("table").first();
        const cells = groupRow.locator(":scope > td");
        await s.shot(1, [cells.nth(0), cells.nth(2), cells.nth(4)], { crop: table });
        const rows = ["web-frontend", "api-server", "postgres-db"].map((n) => s.page.locator("tr", { hasText: n }).first().locator(":scope > td").first());
        await s.shot(2, [groupRow.getByRole("button", { name: new RegExp(title) }), ...rows], { crop: table });
        await s.go("/dashboard", { wait: 1200 });
        const card = s.page.locator("article", { hasText: "api-server" }).first();
        const enter = card.getByRole("button", { name: s.t("Enter Machine", "進入機器") });
        await s.shot(3, [card, enter], { crop: s.page.locator("section", { has: card }).first() });
      },
    },
    // s05 Start or shut down the whole set
    {
      mocks(m) { resourcePageMocks(m, () => practice(m, "ready", ["postgres-db"])); },
      run: async (s) => {
        await s.go("/my-resources", { wait: 900 });
        const btn = s.page.getByRole("button", { name: s.t("Group power", "整組電源") }).first();
        await s.shot(1, btn);
        await s.click(btn);
        const start = s.page.getByRole("menuitem", { name: s.t("Start all", "整組開機") }).or(s.page.getByRole("button", { name: s.t("Start all", "整組開機") })).first();
        const stop = s.page.getByRole("menuitem", { name: s.t("Shut down all", "整組關機") }).or(s.page.getByRole("button", { name: s.t("Shut down all", "整組關機") })).first();
        await s.shot(2, [start, stop]);
      },
    },
    // s06 End the practice early
    {
      mocks(m) { resourcePageMocks(m, () => practice(m, "ready")); },
      run: async (s) => {
        await s.go("/my-resources", { wait: 900 });
        const btn = s.page.getByRole("button", { name: s.t("Group power", "整組電源") }).first();
        await s.click(btn);
        const end = s.page.getByRole("menuitem", { name: s.t("End Practice", "結束練習") }).or(s.page.getByRole("button", { name: s.t("End Practice", "結束練習") })).first();
        await s.shot(1, end);
        await s.click(end, { wait: 600 });
        const dialog = s.page.locator('[role="dialog"],[role="alertdialog"]').last();
        await s.shot(2, dialog.getByRole("button", { name: s.t("End Practice", "結束練習") }));
      },
    },
  ],
};
