// Manual page: manual/my-requests.md
import { D, H, MIN, NOW, ago, iso, later } from "../lib/mock.mjs";

// ---------------------------------------------------------------- data

function requests(m) {
  const base = {
    resource_type: "lxc", cores: 2, memory: 4096, storage: "local-lvm", gpu_mapping_id: null, os_info: "Ubuntu 24.04 (LXC)",
    username: null, assigned_node: "pve-03", desired_node: null, requested_mode: "auto", review_comment: null,
    provisioning_error: null, resource_warning: null, environment_type: "Custom", request_kind: "research",
  };
  const row = (id, hostname, status, provisioning_status, extra = {}) => ({
    ...base, id, hostname, status, provisioning_status, vmid: null,
    reason: m.L("Course assignment for web programming; needs a container to test a Flask app.", "網頁程式設計課程作業，需要容器測試 Flask 應用程式。"),
    created_at: ago(2 * D), start_at: later(2 * D), end_at: later(60 * D), ...extra,
  });
  return [
    row("req-01", "flask-blog-lab", "pending", "idle", { created_at: ago(3 * H) }),
    row("req-02", "jupyter-ml-lab", "approved", "idle", {
      cores: 4, os_info: "JupyterLab (LXC)",
      reason: m.L("Machine learning homework with scikit-learn and pandas.", "機器學習課程作業，使用 scikit-learn 與 pandas。"),
    }),
    row("req-03", "docker-lab", "approved", "running", { start_at: ago(5 * MIN), created_at: ago(D),
      reason: m.L("Docker and Compose practice for the cloud computing course.", "雲端運算實務課程的 Docker 與 Compose 練習。") }),
    row("req-04", "ubuntu-desktop", "approved", "completed", {
      resource_type: "vm", vmid: 10231, os_info: "Ubuntu 24.04 LTS", username: "student", cores: 4, memory: 8192, start_at: ago(10 * D), created_at: ago(12 * D),
      reason: m.L("Operating systems course labs that need a graphical desktop.", "作業系統課程需要圖形桌面環境做實驗。"),
    }),
    row("req-05", "cuda-training", "approved", "completed", {
      resource_type: "vm", vmid: 10287, os_info: "Ubuntu 24.04 CUDA", username: "student", cores: 8, memory: 32768, gpu_mapping_id: "h200-vgpu",
      start_at: ago(H), created_at: ago(5 * D),
      resource_warning: m.L("Not enough free GPU memory on pve-gpu-01 (needs 35 GB, 18 GB free). Retrying every minute.", "pve-gpu-01 的 GPU 記憶體不足（需要 35 GB，剩 18 GB），每分鐘自動重試。"),
      reason: m.L("Graduation project: fine-tuning a small language model.", "畢業專題：微調小型語言模型。"),
    }),
    row("req-06", "redis-cache", "approved", "failed", {
      start_at: ago(2 * H), created_at: ago(2 * D),
      provisioning_error: "TASK ERROR: unable to create CT 10312 - storage 'local-lvm' does not have enough free space",
      reason: m.L("Redis cache for the database systems course project.", "資料庫系統課程專題需要 Redis 快取。"),
    }),
    row("req-10", "mysql-lab", "approved", "failed", {
      vmid: 10305, start_at: ago(D), created_at: ago(3 * D), os_info: "Debian 12 (LXC)",
      provisioning_error: "TASK ERROR: startup for container '10305' failed",
      reason: m.L("MySQL practice for the database systems course.", "資料庫系統課程的 MySQL 練習。"),
    }),
    row("req-07", "game-server", "rejected", "idle", {
      created_at: ago(6 * D),
      review_comment: m.L("Please use the class machine for this assignment.", "這份作業請使用班級機器。"),
      reason: m.L("Minecraft server for the club event.", "社團活動用的 Minecraft 伺服器。"),
    }),
    row("req-08", "old-notes", "cancelled", "idle", { created_at: ago(9 * D),
      reason: m.L("Personal note-taking wiki for the semester.", "這學期的個人筆記 Wiki。") }),
    row("req-09", "summer-project", "expired", "idle", { start_at: ago(40 * D), end_at: ago(10 * D), created_at: ago(45 * D),
      reason: m.L("Summer research project on network monitoring.", "暑期網路監控研究專題。") }),
  ];
}

function specRequests(m, u) {
  const base = {
    user_id: u.id, user_email: u.email, user_full_name: u.full_name, resource_exists: true, change_type: "resize",
    current_cpu: null, current_memory: null, current_disk: null, requested_cpu: null, requested_memory: null, requested_disk: null,
    current_expiry_date: null, requested_expiry_date: null, reviewer_id: null, review_comment: null, reviewed_at: null,
    applied_at: null, apply_started_at: null, apply_error: null, apply_status: null,
  };
  return [
    { ...base, id: "scr-01", vmid: 10231, resource_name: "ubuntu-desktop", current_cpu: 4, requested_cpu: 6, current_memory: 8192, requested_memory: 12288,
      reason: m.L("Android Studio emulator needs more cores and memory.", "Android Studio 模擬器需要更多核心與記憶體。"),
      status: "approved", apply_status: "ready", reviewed_at: ago(3 * H), created_at: ago(D) },
    { ...base, id: "scr-02", vmid: 10287, resource_name: "cuda-training", current_disk: 100, requested_disk: 150,
      reason: m.L("The training dataset no longer fits on the disk.", "訓練資料集已經放不下了。"),
      status: "approved", apply_status: "failed", reviewed_at: ago(D), apply_started_at: ago(20 * H), created_at: ago(2 * D),
      apply_error: m.L("Resize failed: storage 'local-lvm' has only 12 GB free.", "調整失敗：儲存區 local-lvm 只剩 12 GB 可用空間。") },
    { ...base, id: "scr-03", vmid: 10342, resource_name: "capstone-web", current_memory: 4096, requested_memory: 6144,
      reason: m.L("Running the app together with PostgreSQL for the final demo.", "期末展示需要同時執行應用程式與 PostgreSQL。"),
      status: "pending", created_at: ago(2 * H) },
    { ...base, id: "scr-04", vmid: 10342, resource_name: "capstone-web", change_type: "extend",
      current_expiry_date: "2026-12-31", requested_expiry_date: "2027-01-31",
      reason: m.L("The final project demo is in mid-January.", "期末專題展示在一月中旬。"),
      status: "approved", apply_status: "applied", reviewed_at: ago(4 * D), applied_at: ago(4 * D), created_at: ago(5 * D) },
  ];
}

const LXC_IMAGES = [
  { volid: "local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst" },
  { volid: "local:vztmpl/ubuntu-22.04-standard_22.04-1_amd64.tar.zst" },
  { volid: "local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst" },
  { volid: "local:vztmpl/rockylinux-9-default_20240912_amd64.tar.xz" },
  { volid: "local:vztmpl/alpine-3.20-default_20240908_amd64.tar.xz" },
];
const VM_IMAGES = [
  { vmid: 9001, name: "ubuntu-24.04-desktop", node: "pve-02", is_windows: false, cores: 2, memory_mb: 4096, disk_gb: 32 },
  { vmid: 9004, name: "ubuntu-24.04-server", node: "pve-02", is_windows: false, cores: 2, memory_mb: 2048, disk_gb: 20 },
  { vmid: 9002, name: "windows-11-edu", node: "pve-02", is_windows: true, cores: 4, memory_mb: 8192, disk_gb: 64 },
  { vmid: 9003, name: "ubuntu-24.04-cuda-GPU", node: "pve-gpu-01", is_windows: false, cores: 8, memory_mb: 32768, disk_gb: 100 },
];
function catalog(m) {
  return [
    { id: "tpl-web", pve_vmid: 9102, name: m.L("Full-stack Web Dev", "Web 全端開發環境"), description: m.L("Node.js 22, PostgreSQL 16 and Nginx, ready for course projects.", "Node.js 22＋PostgreSQL 16＋Nginx，可直接做課程專題。"),
      resource_type: "lxc", version: 2, cores: 2, memory_mb: 4096, disk_gb: 20, node: "pve-01", is_windows: false, requires_gpu: false, password_settable: true },
    { id: "tpl-linux", pve_vmid: 9101, name: m.L("Linux System Administration Lab", "Linux 系統管理實驗環境"), description: m.L("Ubuntu 22.04 with common admin tools, used in the Linux course.", "Ubuntu 22.04＋常用系統管理工具，Linux 課程使用。"),
      resource_type: "vm", version: 3, cores: 2, memory_mb: 4096, disk_gb: 32, node: "pve-02", is_windows: false, requires_gpu: false, password_settable: true },
    { id: "tpl-kali", pve_vmid: 9105, name: m.L("Kali Linux Security Lab", "Kali Linux 資安實驗"), description: m.L("Kali 2025.3 with the course's CTF tools pre-installed.", "Kali 2025.3，已安裝課程用的 CTF 工具。"),
      resource_type: "vm", version: 1, cores: 2, memory_mb: 4096, disk_gb: 40, node: "pve-02", is_windows: false, requires_gpu: false, password_settable: false },
  ];
}

function gpuOptions() {
  const h200Profiles = [
    { mdev_type: "nvidia-1433", name: "NVIDIA H200-18C", vram_mb: 18432, max_instances: 7, creatable: true },
    { mdev_type: "nvidia-1435", name: "NVIDIA H200-35C", vram_mb: 35840, max_instances: 4, creatable: true },
    { mdev_type: "nvidia-1436", name: "NVIDIA H200-70C", vram_mb: 71680, max_instances: 2, creatable: true },
    { mdev_type: "nvidia-1437", name: "NVIDIA H200-141C", vram_mb: 144384, max_instances: 1, creatable: false },
  ];
  const g = (o) => ({ model: "", vram: "", node: "pve-gpu-01", physical_gpu_count: 1, device_count: 1, capacity_count: 1, used_count: 0, available_count: 1,
    is_sriov: false, has_mdev: false, total_vram_mb: 0, used_vram_mb: 0, used_vram_known: true, per_instance_vram_mb: 0, mdev_profile: "", profiles: [], ...o });
  return [
    g({ mapping_id: "h200-vgpu", description: "NVIDIA H200 NVL (vGPU)", model: "H200", physical_gpu_count: 2, device_count: 32, capacity_count: 14, used_count: 9, available_count: 5,
      is_sriov: true, has_mdev: true, total_vram_mb: 288768, per_instance_vram_mb: 18432, mdev_profile: "NVIDIA H200-18C", profiles: h200Profiles }),
    g({ mapping_id: "l40s", description: "NVIDIA L40S", model: "L40S", physical_gpu_count: 2, device_count: 2, capacity_count: 2, used_count: 1, available_count: 1, total_vram_mb: 98304 }),
    g({ mapping_id: "rtx-a6000", description: "NVIDIA RTX A6000", model: "RTX A6000", physical_gpu_count: 2, device_count: 2, capacity_count: 2, used_count: 2, available_count: 0, total_vram_mb: 98304 }),
  ];
}

// 90 days of availability: weekdays mostly free, a few busy and full days.
function availability() {
  const days = [];
  const start = Date.parse("2026-10-12T00:00:00+08:00");
  for (let i = 0; i < 92; i++) {
    const d = new Date(start + i * D + 8 * H);
    const date = d.toISOString().slice(0, 10);
    const full = [9, 10, 23].includes(i);
    const limited = [3, 4, 16, 17, 30, 31].includes(i);
    const slots = Array.from({ length: 4 }, (_, k) => ({
      status: full ? "unavailable" : limited ? (k < 3 ? "limited" : "available") : "available",
      recommended_nodes: ["pve-01", "pve-03"],
    }));
    days.push({ date, slots });
  }
  return { days, summary: "", timezone: "Asia/Taipei" };
}

const USERNAME_RULES = {
  pattern: "^[a-z][a-z0-9_-]{0,31}$", min_length: 1, max_length: 32,
  reserved: ["root", "daemon", "bin", "sys", "nobody", "admin"], reserved_prefixes: ["systemd-", "libvirt", "polkit", "cloud-init"],
  platform_reserved: ["skylab", "ansible", "provision"], default_user_warn: ["ubuntu", "debian", "cloud-user"],
};

function checkUsername(name) {
  const violations = [];
  if (!/^[a-z][a-z0-9_-]{0,31}$/.test(name)) violations.push({ code: "LNX_FORMAT", message: "format", severity: "error" });
  else if (USERNAME_RULES.reserved.includes(name)) violations.push({ code: "LNX_RESERVED", message: "reserved", severity: "error" });
  else if (USERNAME_RULES.default_user_warn.includes(name)) violations.push({ code: "LNX_DEFAULT_USER", message: "default user", severity: "warning" });
  return { ok: !violations.some((v) => v.severity === "error"), violations };
}

// ---------------------------------------------------------------- helpers

const OS = '[data-field="ostemplate template_id"] select';
const IMG_UBUNTU = "img:local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst";

async function openForm(s) {
  await s.go("/my-requests");
  await s.click(s.page.getByRole("button", { name: s.t("Request Resource", "申請資源") }).first(), { wait: 900 });
}

async function pickOs(s, value) {
  await s.page.locator(OS).selectOption(value);
  await s.settle(500);
}

// Native <select> popups are not captured in screenshots: render the list inline instead.
async function expandSelect(loc, rows) {
  await loc.evaluate((el, n) => { el.setAttribute("size", String(n)); el.style.height = "auto"; el.style.backgroundImage = "none"; }, rows);
  await loc.page().waitForTimeout(200);
}

const field = (s, name) => s.page.locator(`[data-field="${name}"]`);
const reqRow = (s, host) => s.page.locator("tr", { hasText: host }).first();
// The badge plus its note icon (the status column is off-screen at 1440 px until scrolled to).
const statusOf = (s, host) => reqRow(s, host).locator("td").nth(8).locator("div > span").first();
const noteOf = (s, host) => reqRow(s, host).locator("td").nth(8).locator("div > span").nth(1);
const actionsCell = (s, host) => reqRow(s, host).locator("td").nth(9);

async function blurAll(s) {
  await s.page.evaluate(() => document.activeElement?.blur?.());
}

async function fillPeriod(s, start = "2026-10-14T08:00", end = "2026-12-31T23:00") {
  await s.fill(field(s, "start_at").locator("input"), start);
  await s.fill(field(s, "end_at").locator("input"), end);
  await blurAll(s);
  await s.settle(600);
}

// Scrolls `loc` to a given place in its scroll container ("start" | "center" | "end").
// Moving the page between two states also keeps the framework from merging them
// into one frame when only a small field differs.
async function scrollTo(s, loc, block = "center", inline = "nearest") {
  await loc.evaluate((el, [b, i]) => el.scrollIntoView({ block: b, inline: i }), [block, inline]);
  await s.page.waitForTimeout(250);
}

// A mark cropped to the main column (sidebar left out), at least `h` px tall around the targets.
async function mark(s, n, target, { h = 520, x = 340, newFrame = false } = {}) {
  const ts = Array.isArray(target) ? target : [target];
  const boxes = [];
  for (const t of ts) {
    const b = await t.boundingBox();
    if (!b) throw new Error(`mark ${n}: target not visible (${t})`);
    boxes.push(b);
  }
  const top = Math.min(...boxes.map((b) => b.y)) - 36;
  const bottom = Math.max(...boxes.map((b) => b.y + b.height)) + 36;
  const height = Math.max(h, bottom - top);
  const y = top - (height - (bottom - top)) / 2;
  await s.shot(n, target, { crop: { x, y, width: 1440 - x, height }, scroll: false, newFrame });
}

// The floating AI assistant button sits on the summary card and the tables; hide it.
const withoutFab = (list) => list.map((run) => async (s) => {
  await s.page.addInitScript(() => {
    const add = () => {
      if (!document.head) return setTimeout(add, 10);
      const st = document.createElement("style");
      st.textContent = 'button[aria-label="Open AI assistant"],button[aria-label="開啟 AI 助手"]{visibility:hidden !important}';
      document.head.appendChild(st);
    };
    add();
  });
  await run(s);
});

// ---------------------------------------------------------------- scene

export default {
  page: "my-requests",
  role: "student",
  mocks(m) {
    const list = requests(m);
    const specs = specRequests(m, m.users.student);
    m.state.list = list;
    m.state.specs = specs;
    m.use([
      ["GET", /^\/api\/v1\/vm-requests\/my$/, () => ({ data: m.state.list, count: m.state.list.length })],
      ["GET", /^\/api\/v1\/spec-change-requests\/my$/, () => ({ data: m.state.specs, count: m.state.specs.length })],
      ["GET", /^\/api\/v1\/quotas\/my-usage$/, () => m.state.quota ?? ({ used_cpu_cores: 4, used_memory_mb: 8192, used_disk_gb: 60, used_instances: 2, quota: { max_cpu_cores: 16, max_memory_mb: 32768, max_disk_gb: 200, max_instances: 5 } })],
      ["GET", /^\/api\/v1\/lxc\/templates$/, () => (m.state.failOs ? { __status: 500, __body: { detail: "Proxmox API timeout" } } : LXC_IMAGES)],
      ["GET", /^\/api\/v1\/vm\/templates$/, () => VM_IMAGES],
      ["GET", /^\/api\/v1\/templates\/catalog$/, () => { const c = catalog(m); return { data: c, count: c.length }; }],
      ["GET", /^\/api\/v1\/gpu\/options$/, () => (m.state.noGpu ? [] : gpuOptions())],
      ["GET", /^\/api\/v1\/policies\/username$/, () => USERNAME_RULES],
      ["POST", /^\/api\/v1\/policies\/username\/check$/, ({ body }) => checkUsername(String(body?.username || ""))],
      ["POST", /^\/api\/v1\/vm-requests\/availability$/, () => (m.state.failAvail ? { __status: 502, __body: { detail: "Proxmox cluster unreachable" } } : availability())],
      ["POST", /^\/api\/v1\/vm-requests\/advise$/, ({ body }) => ({ resource_type: body?.gpu_mapping_id ? "vm" : "lxc", reason: "" })],
      ["POST", /^\/api\/v1\/vm-requests\/window-availability$/, () => ({ feasible: true, summary: "pve-03" })],
      ["POST", /^\/api\/v1\/vm-requests\/$/, ({ body }) => {
        const r = {
          ...requests(m)[0], id: "req-new", hostname: body.hostname, resource_type: body.resource_type, status: "pending", provisioning_status: "idle",
          os_info: body.os_info, cores: body.cores, memory: body.memory, username: body.username ?? null, reason: body.reason,
          created_at: iso(NOW), start_at: body.start_at, end_at: body.end_at,
        };
        m.state.list = [r, ...m.state.list];
        return r;
      }],
      ["POST", /^\/api\/v1\/vm-requests\/([^/]+)\/cancel$/, ({ mt }) => {
        const r = { ...m.state.list.find((x) => x.id === mt[1]), status: "cancelled" };
        m.state.list = m.state.list.map((x) => (x.id === r.id ? r : x));
        return r;
      }],
      ["POST", /^\/api\/v1\/vm-requests\/([^/]+)\/retry$/, ({ mt }) => {
        const r = { ...m.state.list.find((x) => x.id === mt[1]), provisioning_status: "running", provisioning_error: null };
        m.state.list = m.state.list.map((x) => (x.id === r.id ? r : x));
        return r;
      }],
    ]);
  },
  sections: withoutFab([
    // s01 Open the request form
    async (s) => {
      await s.go("/my-requests");
      const open = s.page.getByRole("button", { name: s.t("Request Resource", "申請資源") }).first();
      await mark(s, 1, open, { h: 440 });
      await s.click(open, { wait: 900 });
      await mark(s, 2, s.page.locator('[data-guide="request-summary"]'), { h: 900 });
      await mark(s, 3, s.page.getByRole("button", { name: s.t("Back", "返回"), exact: true }).first(), { h: 900 });
      const cancel = s.page.locator('[data-guide="request-submit"]').getByRole("button", { name: s.t("Cancel", "取消") });
      await scrollTo(s, cancel, "end");
      await mark(s, 3, cancel, { h: 300 });
    },

    // s02 Name the machine and choose an operating system
    async (s) => {
      await openForm(s);
      const os = s.page.locator(OS);
      await expandSelect(os, 17);
      const name = field(s, "hostname").locator("input");
      await s.fill(name, "flask-blog-lab");
      await blurAll(s);
      await mark(s, 1, name);
      await mark(s, 2, os);
      await os.evaluate((el) => { el.removeAttribute("size"); el.style.height = ""; el.style.backgroundImage = ""; });
      await pickOs(s, IMG_UBUNTU);
      await scrollTo(s, os, "center");
      await mark(s, 3, [os, field(s, "ostemplate template_id").locator("p").first()], { h: 360 });
      s.m.state.failOs = true;
      await openForm(s);
      const retry = field(s, "ostemplate template_id").getByRole("button", { name: s.t("Retry", "重試") });
      await mark(s, 4, retry, { h: 300, newFrame: true });
    },

    // s03 Set the login account and password
    async (s) => {
      await openForm(s);
      await s.fill(field(s, "hostname").locator("input"), "ubuntu-desktop");
      // Linux VM
      await pickOs(s, "vm:9001");
      const user = field(s, "username");
      await s.fill(user.locator("input"), "ubuntu", { wait: 900 });
      await blurAll(s);
      await scrollTo(s, user, "center");
      await mark(s, 1, user, { h: 360 });
      // Windows VM: no username, Admin in the summary; password checklist
      await pickOs(s, "vm:9002");
      await scrollTo(s, field(s, "hostname"), "start");
      const summaryUser = s.page.locator('[data-guide="request-summary"] div', { hasText: s.t("Admin (fixed for Windows)", "Admin（Windows 固定）") }).last();
      const pw = field(s, "password");
      await mark(s, 2, [pw.locator("p").first(), summaryUser], { h: 480, newFrame: true });
      // Container: root
      await pickOs(s, IMG_UBUNTU);
      await scrollTo(s, field(s, "password"), "center");
      await mark(s, 3, field(s, "password").locator("p").first(), { h: 300, newFrame: true });
      // Windows again: the password checklist
      await pickOs(s, "vm:9002");
      await scrollTo(s, field(s, "hostname"), "start");
      await s.fill(pw.locator("input"), "skylab2026", { wait: 400 });
      await blurAll(s);
      await mark(s, 4, [pw.locator("input"), pw.getByRole("list")], { h: 480, newFrame: true });
      // Template that keeps its own password
      await pickOs(s, "vm:9105");
      await s.fill(user.locator("input"), "student", { wait: 900 });
      await blurAll(s);
      await scrollTo(s, field(s, "hostname"), "start");
      await mark(s, 5, field(s, "password"), { h: 360, newFrame: true });
    },

    // s04 Choose CPU, memory and disk
    async (s) => {
      await openForm(s);
      await s.fill(field(s, "hostname").locator("input"), "web-dev");
      await pickOs(s, "tpl:tpl-web");
      await scrollTo(s, s.page.locator('[data-guide="request-hardware"]'), "start");
      await mark(s, 1, [field(s, "cores"), field(s, "memory"), field(s, "disk")], { h: 420 });
      // Remaining quota: 4 cores, 6 GB memory, 60 GB disk.
      s.m.state.quota = { used_cpu_cores: 12, used_memory_mb: 26624, used_disk_gb: 140, used_instances: 3, quota: { max_cpu_cores: 16, max_memory_mb: 32768, max_disk_gb: 200, max_instances: 5 } };
      await openForm(s);
      await pickOs(s, IMG_UBUNTU);
      await scrollTo(s, field(s, "memory"), "center");
      await mark(s, 2, [field(s, "cores").locator("p").first(), field(s, "memory").locator("p").first(), field(s, "disk").locator("p").first()], { h: 420, newFrame: true });
      // Nothing left for CPU and memory.
      s.m.state.quota = { used_cpu_cores: 16, used_memory_mb: 32512, used_disk_gb: 150, used_instances: 4, quota: { max_cpu_cores: 16, max_memory_mb: 32768, max_disk_gb: 200, max_instances: 5 } };
      await openForm(s);
      await pickOs(s, IMG_UBUNTU);
      await scrollTo(s, s.page.locator('[data-guide="request-hardware"]'), "start");
      await mark(s, 3, [field(s, "cores").locator("p").last(), field(s, "memory").locator("p").last()], { h: 360, x: 324, newFrame: true });
    },

    // s05 Choose a GPU
    async (s) => {
      await openForm(s);
      await s.fill(field(s, "hostname").locator("input"), "cuda-training");
      await pickOs(s, "vm:9003");
      const gpu = field(s, "gpu_mapping_id").locator("select");
      await expandSelect(gpu, 4);
      await fillPeriod(s);
      await scrollTo(s, gpu, "center");
      await mark(s, 1, [field(s, "start_at").locator("input"), field(s, "end_at").locator("input")]);
      await mark(s, 2, gpu);
      await gpu.evaluate((el) => { el.removeAttribute("size"); el.style.height = ""; el.style.backgroundImage = ""; });
      await gpu.selectOption("h200-vgpu");
      await s.settle(400);
      const prof = s.page.locator("label", { hasText: s.t("vGPU Profile", "vGPU 規格") }).locator("..").locator("select");
      await expandSelect(prof, 4);
      await scrollTo(s, gpu, "center");
      await mark(s, 3, prof, { h: 460, newFrame: true });
      s.m.state.noGpu = true;
      await openForm(s);
      await pickOs(s, "vm:9003");
      await fillPeriod(s, "2026-10-19T08:00", "2026-10-23T18:00");
      const none = s.page.locator("p", { hasText: s.t("No GPUs are currently available", "此時段目前沒有可用 GPU") });
      await scrollTo(s, none, "center");
      await mark(s, 4, none, { h: 300, newFrame: true });
    },

    // s06 Set the rental period
    async (s) => {
      await openForm(s);
      await s.fill(field(s, "hostname").locator("input"), "flask-blog-lab");
      await pickOs(s, IMG_UBUNTU);
      const cal = s.page.locator('[data-guide="request-schedule"]');
      await scrollTo(s, cal, "center");
      await s.click(cal.getByRole("button", { name: "23", exact: true }));
      await s.click(cal.getByRole("button", { name: "31", exact: true }), { wait: 500 });
      const calendarRoot = cal.locator("button", { hasText: /^31$/ }).locator("xpath=ancestor::div[3]");
      await scrollTo(s, cal, "end");
      await mark(s, 1, [field(s, "start_at").locator("input"), field(s, "end_at").locator("input")]);
      await mark(s, 2, calendarRoot);
      s.m.state.failAvail = true;
      await openForm(s);
      await pickOs(s, IMG_UBUNTU);
      await fillPeriod(s, "2026-10-19T08:00", "2026-10-30T18:00");
      const reload = cal.getByRole("button", { name: s.t("Reload", "重新載入") });
      await scrollTo(s, reload, "center");
      await mark(s, 3, [reload, field(s, "start_at").locator("input"), field(s, "end_at").locator("input")], { h: 360, newFrame: true });
    },

    // s07 Explain the reason and submit
    async (s) => {
      await openForm(s);
      await s.fill(field(s, "hostname").locator("input"), "web-api-lab");
      await pickOs(s, IMG_UBUNTU);
      await s.fill(field(s, "password").locator("input"), "Api-Lab!2026");
      await fillPeriod(s);
      const reason = field(s, "reason");
      await s.fill(reason.locator("textarea"), s.t(
        "FastAPI back end for our capstone project; the team needs a container to deploy and test it during the semester.",
        "專題用的 FastAPI 後端，需要一台容器讓組員整學期部署與測試。",
      ), { wait: 900 });
      await blurAll(s);
      const submit = s.page.getByRole("button", { name: s.t("Submit Request", "送出申請") });
      await scrollTo(s, submit, "end");
      await mark(s, 1, reason, { h: 900 });
      await mark(s, 2, s.page.locator('[data-guide="request-summary"]'), { h: 900 });
      await mark(s, 3, submit, { h: 900 });
      await s.click(submit, { wait: 1200 });
      const st = statusOf(s, "web-api-lab");
      await scrollTo(s, st, "nearest", "center");
      await mark(s, 4, st, { h: 360 });
    },

    // s08 Follow a request
    async (s) => {
      await s.go("/my-requests");
      await scrollTo(s, statusOf(s, "flask-blog-lab"), "nearest", "center");
      const hosts = ["flask-blog-lab", "jupyter-ml-lab", "docker-lab", "ubuntu-desktop", "cuda-training", null, "game-server", "old-notes", "summer-project"];
      for (let i = 0; i < hosts.length; i++) {
        const t = !hosts[i] ? [statusOf(s, "redis-cache"), statusOf(s, "mysql-lab")]
          : ["cuda-training", "game-server"].includes(hosts[i]) ? [statusOf(s, hosts[i]), noteOf(s, hosts[i])]
          : statusOf(s, hosts[i]);
        await mark(s, i + 1, t);
      }
    },

    // s09 Cancel a request
    async (s) => {
      await s.go("/my-requests");
      const btn = actionsCell(s, "flask-blog-lab").getByRole("button", { name: s.t("Cancel", "撤銷") });
      await scrollTo(s, btn, "nearest", "end");
      await mark(s, 1, [statusOf(s, "flask-blog-lab"), btn], { h: 360 });
      await s.click(btn, { wait: 500 });
      await s.shot(2, s.page.getByRole("alertdialog").getByRole("button", { name: s.t("Cancel Request", "撤銷申請") }));
    },

    // s10 Retry a failed creation
    async (s) => {
      await s.go("/my-requests");
      const btn = actionsCell(s, "redis-cache").getByRole("button", { name: s.t("Retry", "重試") });
      await scrollTo(s, btn, "center", "end");
      await mark(s, 1, [statusOf(s, "redis-cache"), btn], { h: 360 });
      await s.click(btn, { wait: 700 });
      await scrollTo(s, reqRow(s, "redis-cache"), "start", "end");
      await mark(s, 2, statusOf(s, "redis-cache"), { h: 360, newFrame: true });
    },

    // s11 Apply or cancel a spec change
    async (s) => {
      await s.go("/my-requests");
      const specRow = (name) => s.page.locator("section tr", { hasText: name }).first();
      const apply = specRow("ubuntu-desktop").getByRole("button", { name: s.t("Apply", "套用"), exact: true });
      const again = specRow("cuda-training").getByRole("button", { name: s.t("Apply Again", "重新套用") });
      const cancel = specRow("capstone-web").getByRole("button", { name: s.t("Cancel", "撤銷") });
      await scrollTo(s, specRow("ubuntu-desktop").getByRole("button", { name: s.t("Cancel", "撤銷") }), "center", "end");
      await mark(s, 1, apply, { h: 220 });
      await s.click(apply, { wait: 500 });
      await s.shot(1, s.page.getByRole("alertdialog").getByRole("button", { name: s.t("Shut down and apply", "關機並套用") }));
      await s.press("Escape", { wait: 400 });
      await blurAll(s);
      await s.page.mouse.move(200, 600);
      await mark(s, 2, [specRow("cuda-training").locator("td").nth(5).locator("div > span").first(), specRow("cuda-training").locator("td").nth(5).locator("div > span").nth(1), again], { h: 380, newFrame: true });
      await mark(s, 3, cancel, { h: 380 });
    },
  ]),
};
