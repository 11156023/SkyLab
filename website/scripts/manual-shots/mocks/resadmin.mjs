// Shared mock data for the admin resource pages: request-review, batch-review,
// resource-management and templates.
import { D, H, MIN, NOW, ago, later } from "../lib/mock.mjs";
import { people, users } from "./common.mjs";

// ---------------------------------------------------------------- scene helpers

// The page without the sidebar. Scenes that use frame() crop inside this area.
export const MAIN = { x: 340, y: 24, width: 1100, height: 852 };

// Starts a new frame of the section picture: the next s.shot() gets newFrame: true
// (switching between two requests in the same detail panel changes too few pixels
// for finish() to split frames on its own).
export async function frame(s) {
  if (!s.__frameWrapped) {
    const shot = s.shot.bind(s);
    s.shot = (step, target, opts = {}) => {
      const newFrame = s.__nextNewFrame;
      s.__nextNewFrame = false;
      return shot(step, target, newFrame ? { ...opts, newFrame: true } : opts);
    };
    s.__frameWrapped = true;
  }
  s.__nextNewFrame = true;
}

// ---------------------------------------------------------------- people

export function persons(m) {
  const U = users(m);
  const [emily, kevin, grace, brian, iris, chen] = people(m);
  return { alex: U.student, lin: U.teacher, admin: U.admin, emily, kevin, grace, brian, iris, chen };
}

const uuid = (n) => `5e1f0000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const dateOnly = (ms) => new Date(ms).toISOString().slice(0, 10);

// Paged list endpoint: filters by ?status=, honours limit/skip like the backend.
function paged(rows, query) {
  const list = query.status ? rows.filter((r) => r.status === query.status) : rows;
  const skip = Number(query.skip || 0);
  const limit = Number(query.limit || 100);
  const sorted = [...list].sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
  return { data: sorted.slice(skip, skip + limit), count: list.length };
}

// ---------------------------------------------------------------- request review

export function vmRequests(m) {
  const P = persons(m);
  const row = (n, who, hostname, status, extra = {}) => ({
    id: uuid(100 + n),
    user_id: who.id,
    user_email: who.email,
    user_full_name: who.full_name,
    reason: m.L("Course assignment for web programming; I need a container to test a Flask app.", "網頁程式設計課程作業，需要容器測試 Flask 應用程式。"),
    resource_type: "lxc",
    request_kind: "course",
    hostname,
    cores: 2,
    memory: 4096,
    storage: "local-lvm",
    environment_type: "Ubuntu",
    os_info: "Ubuntu 24.04 (LXC)",
    ostemplate: "local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst",
    rootfs_size: 20,
    disk_size: null,
    template_id: null,
    gpu_mapping_id: null,
    requested_mode: "manual",
    status,
    reviewer_id: null,
    review_comment: null,
    reviewed_at: null,
    vmid: null,
    assigned_node: null,
    desired_node: null,
    provisioning_status: "idle",
    start_at: later(1 * D),
    end_at: later(60 * D),
    created_at: ago(2 * H),
    ...extra,
  });
  const vm = { resource_type: "vm", os_info: "Ubuntu 24.04 LTS", ostemplate: null, rootfs_size: null, disk_size: 64, template_id: 9001 };
  return [
    row(1, P.alex, "flask-blog-lab", "pending", {
      reason: m.L(
        "Final project for Web Programming (Prof. Lin). Our team of three is building a blog platform with Flask, PostgreSQL and Nginx as a reverse proxy. " +
          "We need one container to run the app, the database and a small Redis cache so we can test sessions and background jobs. " +
          "We will also run load tests with Locust in week 14, so 4 GB of RAM would help. The container is only needed until the demo day on 12 December; we will back up our work and release it right after.",
        "網頁程式設計（林志明老師）期末專題。我們三人小組要用 Flask 做部落格平台，搭配 PostgreSQL 與 Nginx 反向代理。" +
          "需要一台容器同時跑應用程式、資料庫與小型 Redis 快取，用來測試登入狀態與背景工作。" +
          "第 14 週還會用 Locust 做壓力測試，所以希望有 4 GB 記憶體。只用到 12 月 12 日成果發表，發表完會備份並立即釋出。",
      ),
    }),
    row(2, P.grace, "cuda-yolo-train", "pending", {
      ...vm, cores: 8, memory: 32768, disk_size: 120, os_info: "Ubuntu 24.04 CUDA 12.6", gpu_mapping_id: "h200-vgpu",
      reason: m.L("Training a YOLOv11 model for the smart-campus parking project; needs a GPU for about four weeks.", "智慧校園停車專題要訓練 YOLOv11 模型，需要 GPU 約四週。"),
      start_at: later(2 * D), end_at: later(30 * D), created_at: ago(5 * H),
    }),
    row(3, P.kevin, "mysql-dbms-hw", "pending", {
      reason: m.L("Database Systems homework 3: practise MySQL replication.", "資料庫系統作業三：練習 MySQL 主從複寫。"),
      created_at: ago(1 * D),
    }),
    row(4, P.iris, "ubuntu-desktop-iris", "pending", {
      ...vm, cores: 4, memory: 8192, disk_size: 64,
      reason: m.L("Need a Linux desktop for the Operating Systems course labs.", "作業系統課程實習需要一台 Linux 桌面。"),
      created_at: ago(1 * D + 3 * H),
    }),
    row(5, P.brian, "docker-lab", "approved", {
      cores: 2, memory: 2048, vmid: 10342, assigned_node: "pve-01", provisioning_status: "completed",
      review_comment: m.L("Fits the course requirement.", "符合課程需求。"), reviewed_at: ago(3 * D), reviewer_id: P.admin.id,
      start_at: ago(3 * D), created_at: ago(4 * D),
    }),
    row(6, P.grace, "rag-chatbot", "approved", {
      ...vm, cores: 4, memory: 8192, vmid: 10355, assigned_node: "pve-02", provisioning_status: "completed",
      reviewed_at: ago(6 * D), reviewer_id: P.admin.id, start_at: ago(6 * D), created_at: ago(7 * D),
    }),
    row(7, P.kevin, "minecraft-server", "rejected", {
      cores: 4, memory: 8192,
      reason: m.L("Hosting a Minecraft server for the game design club.", "幫遊戲設計社架 Minecraft 伺服器。"),
      review_comment: m.L("Platform machines are for coursework and research only. Please use the club's own server for games.", "平台機器僅供課程與研究使用，遊戲伺服器請使用社團自己的主機。"),
      reviewed_at: ago(2 * D), reviewer_id: P.admin.id, created_at: ago(2 * D + 4 * H),
    }),
    row(8, P.iris, "summer-project", "expired", { start_at: ago(40 * D), end_at: ago(10 * D), created_at: ago(41 * D) }),
    row(9, P.alex, "old-notes", "cancelled", { created_at: ago(9 * D) }),
  ];
}

export function specRequests(m) {
  const P = persons(m);
  const row = (n, who, vmid, name, status, extra = {}) => ({
    id: uuid(200 + n),
    vmid,
    resource_name: name,
    resource_exists: true,
    user_id: who.id,
    user_email: who.email,
    user_full_name: who.full_name,
    change_type: "combined",
    reason: m.L("The model no longer fits in memory during training.", "訓練時模型已經放不進記憶體。"),
    current_cpu: null, current_memory: null, current_disk: null,
    requested_cpu: null, requested_memory: null, requested_disk: null,
    current_expiry_date: null, requested_expiry_date: null,
    status,
    reviewer_id: null, review_comment: null, reviewed_at: null,
    applied_at: null, apply_started_at: null, apply_error: null, apply_status: null,
    created_at: ago(3 * H),
    ...extra,
  });
  return [
    row(1, P.iris, 10401, "thesis-gpu", "pending", {
      current_cpu: 8, requested_cpu: 12, current_memory: 16384, requested_memory: 32768, created_at: ago(3 * H),
    }),
    row(2, P.alex, 10231, "ubuntu-desktop", "pending", {
      change_type: "expiry", current_expiry_date: "2026-12-31", requested_expiry_date: "2027-01-31",
      reason: m.L("The thesis experiments continue through winter break.", "論文實驗會延續到寒假。"), created_at: ago(26 * H),
    }),
    row(3, P.brian, 10144, "old-web-lab", "pending", {
      change_type: "disk", current_disk: 20, requested_disk: 40, resource_exists: false,
      reason: m.L("Logs are filling the disk.", "日誌把磁碟塞滿了。"), created_at: ago(2 * D),
    }),
    row(4, P.emily, 10318, "jupyter-ml-lab", "approved", {
      change_type: "disk", current_disk: 20, requested_disk: 40, reviewed_at: ago(1 * D), reviewer_id: P.admin.id,
      reason: m.L("Need room for the ImageNet subset.", "需要空間放 ImageNet 子集。"), created_at: ago(1 * D + 2 * H),
    }),
    row(5, P.alex, 10231, "ubuntu-desktop", "approved", {
      change_type: "cpu", current_cpu: 2, requested_cpu: 4, apply_status: "applied", applied_at: ago(2 * D - 3 * H),
      review_comment: m.L("Approved for the thesis experiments.", "論文實驗需要，核准。"), reviewed_at: ago(2 * D), reviewer_id: P.admin.id,
      reason: m.L("Compiling the kernel takes too long on two cores.", "兩核心編譯核心太慢。"), created_at: ago(2 * D + 5 * H),
    }),
    row(6, P.brian, 10342, "docker-lab", "approved", {
      change_type: "memory", current_memory: 2048, requested_memory: 4096, apply_status: "applying", apply_started_at: ago(2 * MIN),
      reviewed_at: ago(5 * H), reviewer_id: P.admin.id, created_at: ago(6 * H),
    }),
    row(7, P.grace, 10355, "rag-chatbot", "approved", {
      change_type: "cpu", current_cpu: 4, requested_cpu: 8, apply_status: "failed",
      apply_error: m.L("Apply failed: node pve-02 does not have 8 free CPU cores.", "套用失敗：節點 pve-02 沒有 8 顆可用的 CPU 核心。"),
      reviewed_at: ago(3 * D), reviewer_id: P.admin.id, created_at: ago(3 * D + 2 * H),
    }),
    row(8, P.kevin, 10260, "mysql-replica", "rejected", {
      change_type: "memory", current_memory: 4096, requested_memory: 16384,
      review_comment: m.L("16 GB is more than this homework needs; 8 GB is enough.", "這份作業用不到 16 GB，8 GB 就夠了。"),
      reviewed_at: ago(4 * D), reviewer_id: P.admin.id, created_at: ago(4 * D + 6 * H),
    }),
  ];
}

export function reviewContext(m, req) {
  return {
    request: req,
    window_start: req.start_at,
    window_end: req.end_at,
    window_active_now: false,
    feasible: true,
    placement_strategy: "balanced",
    projected_node: req.resource_type === "vm" && req.gpu_mapping_id ? "pve-03" : "pve-02",
    summary: m.L("Enough capacity for the whole window.", "整個時段容量足夠。"),
    reasons: [], warnings: [], resource_warnings: [],
    cluster_nodes: ["pve-01", "pve-02", "pve-03"],
    current_running_resources: [], overlapping_approved_requests: [], projected_nodes: [], node_scores: [],
  };
}

export function useRequestReview(m) {
  m.state.vmReqs = vmRequests(m);
  m.state.specReqs = specRequests(m);
  const review = (list) => ({ mt, body }) => {
    const r = list().find((x) => x.id === mt[1]);
    if (!r) return { __status: 404, __body: { detail: "Not found" } };
    Object.assign(r, { status: body?.status, review_comment: body?.review_comment ?? null, reviewed_at: new Date(NOW).toISOString(), reviewer_id: m.users.admin.id });
    return r;
  };
  m.use([
    ["GET", /^\/api\/v1\/vm-requests\/$/, ({ query }) => paged(m.state.vmReqs, query)],
    ["GET", /^\/api\/v1\/spec-change-requests\/$/, ({ query }) => paged(m.state.specReqs, query)],
    ["GET", /^\/api\/v1\/vm-requests\/([^/]+)\/review-context$/, ({ mt }) => {
      const r = m.state.vmReqs.find((x) => x.id === mt[1]);
      return r ? reviewContext(m, r) : { __status: 404, __body: { detail: "Not found" } };
    }],
    ["POST", /^\/api\/v1\/vm-requests\/([^/]+)\/review$/, review(() => m.state.vmReqs)],
    ["POST", /^\/api\/v1\/spec-change-requests\/([^/]+)\/review$/, review(() => m.state.specReqs)],
  ]);
}

// ---------------------------------------------------------------- resource management (admin list)

const GB = 1024 ** 3;

export function res(o) {
  return {
    vmid: null, request_id: null, teaching_class_id: null, allocation_scope: "personal", control_policy: "owner",
    name: "", status: "running", node: "pve-01", type: "lxc", is_placeholder: false,
    can_control: true, can_delete: true, can_request_spec_change: true, can_extend: true,
    environment_type: null, os_info: null, guest_os: null, expiry_date: null, ip_address: null,
    ssh_public_key: null, has_login_password: true, cpu: 0.08, maxcpu: 2, mem: 1.2 * GB, maxmem: 4 * GB,
    uptime: 2 * 86400 + 7200, auto_stop_at: null, auto_stop_reason: null, idle_since: null,
    scheduled_deletion_at: null, mining_exempt: false, access_role: "admin", can_manage: true, owner_email: null,
    machine_kind: "personal", start_blocked_reason: null, window_start_at: null, window_end_at: null,
    class_relation: null, owner_name: null, teaching_class_name: null, public_urls: [],
    ...o,
  };
}

export const LINUX_CLASS = "c1a55000-0000-4000-8000-000000000003";

export function adminResources(m) {
  const P = persons(m);
  const own = (p) => ({ owner_name: p.full_name, owner_email: p.email });
  const exp = (d) => dateOnly(NOW + d * D);
  const linuxClass = {
    teaching_class_id: LINUX_CLASS, allocation_scope: "teaching_class", machine_kind: "teaching_class", control_policy: "class_member",
    teaching_class_name: m.L("Linux System Administration (IM 2A)", "Linux 系統管理（資管二甲）"),
    environment_type: m.L("Linux lab", "Linux 實驗環境"), can_delete: false, expiry_date: exp(70), type: "lxc", maxcpu: 2, maxmem: 2 * GB,
  };
  const st = students(m, 5);
  return [
    res({ vmid: 10231, request_id: "req-0001", name: "ubuntu-desktop", type: "qemu", node: "pve-02", ...own(P.alex),
      environment_type: m.L("OS course desktop", "作業系統課程桌面"), os_info: "Ubuntu 24.04 LTS", ip_address: "10.20.3.41",
      maxcpu: 4, maxmem: 8 * GB, expiry_date: exp(40), public_urls: ["https://os-lab-wang.skylab.ntub.edu.tw"] }),
    res({ vmid: 10120, name: "lin-demo-server", type: "qemu", node: "pve-01", ...own(P.lin),
      environment_type: m.L("Teaching demo", "教學示範"), os_info: "Ubuntu 24.04 LTS", ip_address: "10.20.5.10", maxcpu: 4, maxmem: 8 * GB }),
    res({ vmid: null, request_id: "5e1f0000-0000-4000-8000-000000000102", name: "cuda-yolo-train", type: "qemu", node: "pve-03", status: "scheduled",
      is_placeholder: true, can_control: false, ...own(P.grace), environment_type: m.L("GPU training", "GPU 訓練"), os_info: "Ubuntu 24.04 CUDA 12.6", expiry_date: exp(30) }),
    res({ vmid: 10318, request_id: "req-0002", name: "jupyter-ml-lab", node: "pve-01", ...own(P.emily),
      environment_type: m.L("Python data science", "Python 資料科學"), os_info: "Ubuntu 22.04 (LXC)", ip_address: "10.20.3.77",
      expiry_date: exp(60), public_urls: ["https://ml-lab-chang.skylab.ntub.edu.tw"] }),
    res({ vmid: 10342, request_id: "req-0105", name: "docker-lab", node: "pve-01", status: "stopped", ...own(P.brian),
      environment_type: "Docker", os_info: "Ubuntu 24.04 (LXC)", ip_address: "10.20.3.98", expiry_date: exp(57), cpu: 0, mem: 0, uptime: 0 }),
    res({ vmid: 10355, request_id: "req-0106", name: "rag-chatbot", type: "qemu", node: "pve-02", ...own(P.grace),
      environment_type: m.L("LLM application", "大型語言模型應用"), os_info: "Ubuntu 24.04 LTS", ip_address: "10.20.3.104", expiry_date: exp(54), maxcpu: 4, maxmem: 8 * GB }),
    res({ vmid: 10260, request_id: "req-0088", name: "mysql-replica", node: "pve-03", ...own(P.kevin),
      environment_type: m.L("Database", "資料庫"), os_info: "Debian 12 (LXC)", ip_address: "10.20.3.66", expiry_date: exp(25) }),
    res({ vmid: 10388, request_id: "req-0090", name: "crypto-lab-test", type: "qemu", node: "pve-02", status: "paused", ...own(P.brian),
      environment_type: "Ubuntu Desktop", os_info: "Ubuntu 24.04 LTS", ip_address: "10.20.3.88", expiry_date: exp(30) }),
    res({ vmid: 10401, request_id: "req-0110", name: "thesis-gpu", type: "qemu", node: "pve-03", status: "starting", ...own(P.iris),
      environment_type: m.L("GPU training", "GPU 訓練"), os_info: "Ubuntu 24.04 CUDA 12.6", ip_address: "10.20.3.121", expiry_date: exp(21), uptime: 30 }),
    res({ vmid: null, request_id: "req-0111", name: "redis-cache", node: "pve-02", status: "failed",
      is_placeholder: true, can_control: false, ...own(P.alex), environment_type: "Redis", os_info: "Ubuntu 24.04 (LXC)", expiry_date: exp(50) }),
    ...st.map((x, i) => res({ ...linuxClass, vmid: 20410 + i, name: `linux-lab-${x.sid}`, os_info: `linux-lab-${x.sid}`, owner_name: x.name, owner_email: x.email,
      ip_address: `10.30.2.${21 + i}`, node: ["pve-01", "pve-02", "pve-03"][i % 3], status: i >= 3 ? "stopped" : "running", cpu: i >= 3 ? 0 : 0.1 })),
    res({ vmid: 30410, request_id: "qpm-1", name: "n8n-11156012", type: "qemu", machine_kind: "quick_practice", node: "pve-02", ...own(P.kevin),
      environment_type: m.L("n8n automation practice", "n8n 自動化練習"), os_info: "Ubuntu 24.04 LTS", ip_address: "10.30.9.41", can_delete: false }),
    res({ vmid: 30411, request_id: "qpm-2", name: "pg-11156012", machine_kind: "quick_practice", node: "pve-02", ...own(P.kevin),
      environment_type: m.L("n8n automation practice", "n8n 自動化練習"), os_info: "PostgreSQL 16 (LXC)", ip_address: "10.30.9.42", can_delete: false }),
  ];
}

export function adminQuickSessions(m) {
  const mach = (i, name, role, type, os) => ({
    id: `qpm-node-${i}`, node_key: `n${i}`, request_id: `qpm-${i}`, vmid: 30409 + i, name, role, resource_type: type,
    os_info: os, status: "running", ip_address: `10.30.9.${40 + i}`, node: "pve-02", public_url: i === 1 ? "https://n8n-11156012.skylab.ntub.edu.tw" : null, forward_endpoints: [],
  });
  return [{
    id: "qps-7f2c", kind: "quick_practice", kind_label: "quick_practice", title: m.L("n8n automation practice", "n8n 自動化練習"),
    environment_id: "qpe-n8n", environment_version_id: "qpv-n8n-2", version: 2, status: "running",
    created_at: ago(H), expires_at: later(3 * H), topology_applied_at: ago(50 * MIN), reclaim_started_at: null, error: null, publication_pending: false,
    machines: [mach(1, "n8n-11156012", "n8n", "qemu", "Ubuntu 24.04 LTS"), mach(2, "pg-11156012", "PostgreSQL", "lxc", "PostgreSQL 16 (LXC)")],
  }];
}

// Admin list + quick practice sessions on top of the resources group's detail-page mocks.
export function useResourceMgmt(m) {
  const st = m.state;
  st.list = adminResources(m);
  m.use([
    ["GET", /^\/api\/v1\/resources\/$/, () => st.list
      .filter((r) => !(st.deleted ?? []).includes(r.vmid))
      .map((r) => (st.status?.[r.vmid] ? { ...r, status: st.status[r.vmid] } : r))],
    ["GET", /^\/api\/v1\/quick-practice\/sessions$/, () => adminQuickSessions(m)],
  ]);
}

// ---------------------------------------------------------------- templates

export function templateList(m) {
  const P = persons(m);
  const tpl = (n, o) => ({
    id: `7e3a0000-0000-4000-8000-${String(n).padStart(12, "0")}`, pve_vmid: 9100 + n, owner_id: P.lin.id, node: "pve-01", storage: "local-lvm",
    resource_type: "qemu", status: "ready", visibility: "global", default_cores: 2, default_memory: 4096, default_disk: 32,
    password_settable: true, requires_gpu: false, attachment_count: 0, source_vmid: null, version: 1, error_message: null, pve_exists: true,
    created_at: ago(30 * D), updated_at: ago(3 * D), description: null,
    ...o,
  });
  return [
    tpl(1, { name: "Ubuntu 24.04 Desktop", description: m.L("GNOME desktop with VS Code, gcc 14 and Python 3.12. For the Operating Systems and C programming courses.", "GNOME 桌面，內含 VS Code、gcc 14 與 Python 3.12，供作業系統與 C 語言課程使用。"), version: 3, attachment_count: 2 }),
    tpl(2, { name: m.L("Docker Lab (Ubuntu 24.04)", "Docker 實驗環境（Ubuntu 24.04）"), resource_type: "lxc", default_disk: 16, default_memory: 2048,
      description: m.L("Docker Engine 27 and Compose preinstalled.", "預裝 Docker Engine 27 與 Compose。"), version: 2, attachment_count: 1 }),
    tpl(3, { name: "CUDA 12.6 + PyTorch 2.5", requires_gpu: true, default_cores: 8, default_memory: 32768, default_disk: 120, node: "pve-03", visibility: "private",
      description: m.L("For deep learning projects; every machine gets a GPU.", "深度學習專題用，每台都要配 GPU。") }),
    tpl(4, { name: "Windows 11 Education", password_settable: false, default_cores: 4, default_memory: 8192, default_disk: 80,
      description: m.L("Office 2024 and SQL Server Management Studio.", "內含 Office 2024 與 SQL Server Management Studio。") }),
    tpl(5, { name: m.L("Kubernetes Lab", "Kubernetes 實驗環境"), status: "updating", version: 2,
      description: m.L("k3s single-node cluster with kubectl and Helm.", "k3s 單節點叢集，內含 kubectl 與 Helm。") }),
    tpl(6, { name: m.L("MySQL 8.4 Lab", "MySQL 8.4 實驗環境"), resource_type: "lxc", status: "failed", visibility: "private", default_disk: 16,
      error_message: m.L("Conversion failed: source machine 10466 was not found on PVE.", "轉換失敗：PVE 上找不到來源機器 10466。") }),
    tpl(7, { name: m.L("Node.js 22 Web Development", "Node.js 22 網頁開發"), resource_type: "lxc", status: "creating", visibility: "private", created_at: ago(3 * MIN) }),
    tpl(8, { name: m.L("CentOS 7 (legacy)", "CentOS 7（舊版）"), pve_exists: false, visibility: "private", version: 4, created_at: ago(400 * D) }),
  ];
}

export function templateAttachments(m, templateId) {
  const files = {
    "7e3a0000-0000-4000-8000-000000000001": [
      [m.L("OS-Lab-Guide.pdf", "作業系統實驗手冊.pdf"), 2_840_000, "application/pdf"],
      [m.L("gcc-quick-reference.pdf", "gcc 速查表.pdf"), 412_000, "application/pdf"],
    ],
    "7e3a0000-0000-4000-8000-000000000002": [[m.L("docker-compose-examples.zip", "docker-compose 範例.zip"), 1_250_000, "application/zip"]],
  }[templateId] ?? [];
  return files.map(([filename, size_bytes, content_type], i) => ({ id: `${templateId.slice(0, 8)}-att-${i}`, template_id: templateId, filename, size_bytes, content_type, created_at: ago(10 * D) }));
}

export function gpuOptions() {
  const profiles = [
    { mdev_type: "nvidia-1061", name: "NVIDIA H200-18C", vram_mb: 18432, max_instances: 7, creatable: true },
    { mdev_type: "nvidia-1062", name: "NVIDIA H200-35C", vram_mb: 35840, max_instances: 4, creatable: true },
    { mdev_type: "nvidia-1063", name: "NVIDIA H200-70C", vram_mb: 71680, max_instances: 2, creatable: false },
  ];
  return [
    { mapping_id: "h200-vgpu", description: "NVIDIA H200 vGPU", model: "H200", vram: "141G", node: "pve-03", physical_gpu_count: 2, device_count: 14, capacity_count: 14,
      used_count: 9, available_count: 5, is_sriov: true, has_mdev: true, total_vram_mb: 258048, used_vram_mb: 165888, used_vram_known: true, per_instance_vram_mb: 18432, mdev_profile: "NVIDIA H200-18C", profiles },
    { mapping_id: "rtx4090", description: "NVIDIA RTX 4090", model: "RTX 4090", vram: "24G", node: "pve-03", physical_gpu_count: 2, device_count: 2, capacity_count: 2,
      used_count: 2, available_count: 0, is_sriov: false, has_mdev: false, total_vram_mb: 49152, used_vram_mb: 49152, used_vram_known: true, per_instance_vram_mb: 0, mdev_profile: "", profiles: [] },
  ];
}

export function useTemplates(m) {
  const st = m.state;
  st.templates = templateList(m);
  const find = (id) => st.templates.find((t) => t.id === id);
  const task = (type) => ({ id: `task-${type}-1`, task_type: type, status: "pending", progress: 0 });
  m.use([
    ["GET", /^\/api\/v1\/templates\/$/, () => ({ data: st.templates, count: st.templates.length })],
    ["POST", /^\/api\/v1\/templates\/$/, ({ body }) => {
      const src = (st.list ?? []).find((r) => r.vmid === body?.source_vmid);
      const t = {
        ...templateList(m)[0], id: "7e3a0000-0000-4000-8000-000000000099", pve_vmid: body?.source_vmid ?? 9199, name: body?.name, description: body?.description,
        visibility: body?.visibility, status: "creating", version: 1, attachment_count: 0, resource_type: src?.type ?? "qemu", source_vmid: body?.source_vmid,
        owner_id: m.currentUser().id, created_at: new Date(NOW).toISOString(),
      };
      st.templates = [t, ...st.templates];
      if (src) st.list = st.list.filter((r) => r.vmid !== src.vmid);
      return { template: t, task: task("template_convert") };
    }],
    ["PATCH", /^\/api\/v1\/templates\/([^/]+)$/, ({ mt, body }) => Object.assign(find(mt[1]) ?? {}, body ?? {})],
    ["DELETE", /^\/api\/v1\/templates\/([^/]+)$/, () => ({ __status: 202, __body: { message: "queued", task_id: "task-tpl-del-1" } })],
    ["POST", /^\/api\/v1\/templates\/([^/]+)\/retry$/, ({ mt }) => { Object.assign(find(mt[1]) ?? {}, { status: "creating", error_message: null }); return { template: find(mt[1]), task: task("template_convert") }; }],
    ["POST", /^\/api\/v1\/templates\/([^/]+)\/clone$/, ({ body }) => ({ tasks: Array.from({ length: body?.count ?? 1 }, (_, i) => ({ ...task("template_clone"), id: `task-clone-${i}` })) })],
    ["POST", /^\/api\/v1\/templates\/([^/]+)\/update-cycle\/(start|finish|cancel)$/, ({ mt }) => {
      const t = find(mt[1]);
      if (t) t.status = mt[2] === "start" ? "updating" : mt[2] === "finish" ? "creating" : "ready";
      return { template: t, task: task("template_update_cycle") };
    }],
    ["GET", /^\/api\/v1\/templates\/([^/]+)\/attachments$/, ({ mt }) => { const d = templateAttachments(m, mt[1]); return { data: d, count: d.length }; }],
    ["POST", /^\/api\/v1\/templates\/([^/]+)\/attachments$/, ({ mt }) => ({ id: "att-new", template_id: mt[1], filename: "lab-setup.pdf", size_bytes: 1_048_576, content_type: "application/pdf", created_at: new Date(NOW).toISOString() })],
    ["DELETE", /^\/api\/v1\/templates\/([^/]+)\/attachments\/[^/]+$/, () => undefined],
    ["GET", /^\/api\/v1\/templates\/([^/]+)\/attachments\/[^/]+\/download$/, () => ({ __text: "%PDF-1.7\n", __contentType: "application/pdf" })],
    ["GET", /^\/api\/v1\/gpu\/options$/, () => gpuOptions()],
  ]);
}

// ---------------------------------------------------------------- class review (batch provisioning)

const STUDENTS = [
  ["11256001", "Daniel Lin", "林柏宇"], ["11256002", "Emily Chang", "張雅婷"], ["11256003", "Kevin Chen", "陳冠宇"],
  ["11256004", "Grace Lee", "李佳穎"], ["11256005", "Brian Huang", "黃柏翰"], ["11256006", "Iris Wu", "吳欣怡"],
  ["11256007", "Jason Liu", "劉承恩"], ["11256008", "Amy Tsai", "蔡宜蓁"], ["11256009", "Eric Yang", "楊子賢"],
  ["11256010", "Vivian Hsu", "許芷瑜"], ["11256011", "Ryan Cheng", "鄭宇翔"], ["11256012", "Cindy Hsieh", "謝佩珊"],
  ["11256013", "Tony Kuo", "郭彥廷"], ["11256014", "Joyce Hung", "洪詩涵"], ["11256015", "Leo Tseng", "曾冠廷"],
  ["11256016", "Sandy Chiu", "邱品妤"], ["11256017", "Allen Liao", "廖哲瑋"], ["11256018", "Wendy Lai", "賴怡安"],
  ["11256019", "Peter Chou", "周柏霖"], ["11256020", "Nina Yeh", "葉語彤"], ["11256021", "Howard Su", "蘇俊賢"],
  ["11256022", "Tina Chuang", "莊心怡"], ["11256023", "Oscar Lu", "呂明哲"], ["11256024", "Bella Ho", "何欣妍"],
  ["11256025", "Sam Kao", "高崇恩"], ["11256026", "Zoe Lo", "羅詠晴"], ["11256027", "Max Chung", "鍾承翰"],
  ["11256028", "Ivy Chiang", "江思妤"], ["11256029", "Ken Fan", "范凱文"], ["11256030", "Lily Pan", "潘采潔"],
];

export function students(m, n = 30) {
  return STUDENTS.slice(0, n).map(([sid, en, zh], i) => ({
    id: `7d3c1a2e-1${String(i).padStart(3, "0")}-4a00-9000-00000000${sid.slice(-4)}`,
    sid, email: `${sid}@ntub.edu.tw`, name: m.L(en, zh),
  }));
}

export function batchJobs(m) {
  const P = persons(m);
  let seq = 0;
  const cls = (n) => `c1a55000-0000-4000-8000-${String(n).padStart(12, "0")}`;
  const job = (o) => {
    const id = uuid(300 + ++seq);
    const roster = students(m, o.total);
    const done = o.done ?? 0;
    const failed = o.failed ?? 0;
    const tasks = roster.map((st, i) => {
      let status = "pending";
      let vmid = null;
      if (i < done) { status = "completed"; vmid = o.vmidBase + i; }
      else if (i < done + failed) status = "failed";
      else if (o.status === "running") status = i < done + failed + 3 ? "running" : "pending";
      return { id: `${id}-t${i}`, user_id: st.id, user_email: st.email, user_name: st.name, member_index: i, vmid, status,
        error: status === "failed" ? "clone timed out on pve-02" : null, started_at: null, finished_at: null };
    });
    return {
      id, teaching_class_id: o.cls ?? null, teaching_class_name: o.className ?? null,
      resource_type: o.type ?? "lxc", hostname_prefix: o.prefix, status: o.status,
      total: o.total, done, failed_count: failed, created_at: o.created, finished_at: null,
      initiated_by: (o.by ?? P.lin).id, initiated_by_email: (o.by ?? P.lin).email, initiated_by_name: (o.by ?? P.lin).full_name,
      reviewer_id: o.reviewed ? P.admin.id : null, reviewer_email: o.reviewed ? P.admin.email : null, reviewed_at: o.reviewed ?? null,
      review_comment: o.comment ?? null, recurrence_rule: o.rule ?? null, recurrence_duration_minutes: o.rule ? 180 : null,
      schedule_timezone: o.rule ? "Asia/Taipei" : null, next_window_start: null, next_window_end: null,
      spec: o.spec ?? { cores: 2, memory: 2048, rootfs_size: 16, os_info: "Ubuntu 24.04 (LXC)" },
      tasks,
    };
  };
  const web = m.L("Web Programming (IM 3A)", "網頁程式設計（資管三甲）");
  return [
    job({ cls: cls(1), className: web, prefix: "web-app", status: "pending_review", total: 24, created: ago(3 * H),
      spec: { cores: 2, memory: 4096, rootfs_size: 20, os_info: "Node.js 22 + Nginx (LXC)" } }),
    job({ cls: cls(1), className: web, prefix: "web-db", status: "pending_review", total: 24, created: ago(3 * H - MIN),
      spec: { cores: 1, memory: 2048, rootfs_size: 16, os_info: "PostgreSQL 16 (LXC)" } }),
    job({ cls: cls(2), className: m.L("Database Systems (IM 2B)", "資料庫系統（資管二乙）"), prefix: "dbms", status: "pending_review", total: 20,
      created: ago(20 * H), by: P.chen, rule: "FREQ=WEEKLY;BYDAY=MO,WE;BYHOUR=9;BYMINUTE=0",
      spec: { cores: 2, memory: 4096, rootfs_size: 20, os_info: "MySQL 8.4 (LXC)" } }),
    job({ prefix: "ai-workshop", status: "pending_review", total: 10, type: "vm", created: ago(1 * D + 4 * H),
      rule: "FREQ=WEEKLY;BYDAY=SA;BYHOUR=13;BYMINUTE=0;UNTIL=20261010T050000Z",
      spec: { cores: 4, memory: 8192, disk_size: 64, os_info: "Ubuntu 24.04 LTS" } }),
    job({ cls: cls(3), className: m.L("Linux System Administration (IM 2A)", "Linux 系統管理（資管二甲）"), prefix: "linux-lab", status: "running", total: 30, done: 18,
      vmidBase: 20410, created: ago(2 * D), reviewed: ago(25 * MIN) }),
    job({ cls: cls(4), className: m.L("Computer Networks (IM 3B)", "計算機網路（資管三乙）"), prefix: "net-lab", status: "failed", total: 25, done: 22, failed: 3,
      vmidBase: 20210, type: "vm", created: ago(4 * D), reviewed: ago(4 * D - 2 * H),
      spec: { cores: 2, memory: 4096, disk_size: 32, os_info: "Ubuntu 24.04 LTS" } }),
    job({ cls: cls(5), className: m.L("Python Programming (IM 1A)", "Python 程式設計（資管一甲）"), prefix: "py-lab", status: "completed", total: 30, done: 30,
      vmidBase: 20010, created: ago(9 * D), reviewed: ago(9 * D - 3 * H), comment: m.L("Approved for the first week of class.", "第一週上課使用，核准。") }),
    job({ cls: cls(6), className: m.L("Cloud Computing (IM 4A)", "雲端運算（資管四甲）"), prefix: "cloud-lab", status: "pending", total: 28, created: ago(5 * H), reviewed: ago(10 * MIN) }),
    job({ cls: cls(7), className: m.L("Game Design Club", "遊戲設計社"), prefix: "mc-server", status: "rejected", total: 12, created: ago(6 * D), reviewed: ago(5 * D),
      comment: m.L("Club activities are not covered by the teaching quota. Please use the club's own server.", "社團活動不在教學配額內，請使用社團自己的主機。") }),
    job({ cls: cls(8), className: m.L("Big Data Analytics (IM 4B)", "巨量資料分析（資管四乙）"), prefix: "spark", status: "cancelled", total: 15, created: ago(12 * D) }),
  ];
}

export function useBatchReview(m) {
  m.state.jobs = batchJobs(m);
  const decide = (jobs, body) => {
    for (const j of jobs) {
      Object.assign(j, {
        status: body?.decision === "approved" ? "pending" : "rejected", review_comment: body?.review_comment ?? null,
        reviewed_at: new Date(NOW).toISOString(), reviewer_id: m.users.admin.id, reviewer_email: m.users.admin.email,
      });
    }
    return jobs[0] ?? { __status: 404, __body: { detail: "Not found" } };
  };
  // Mon/Wed 09:00-12:00 Taipei = 01:00-04:00 UTC, counted from Monday 2026-10-12.
  const win = (d) => [new Date(Date.parse("2026-10-12T01:00:00Z") + d * D).toISOString(), new Date(Date.parse("2026-10-12T04:00:00Z") + d * D).toISOString()];
  m.use([
    ["GET", /^\/api\/v1\/batch-provision\/$/, ({ query }) => {
      const list = query.status ? m.state.jobs.filter((j) => j.status === query.status) : m.state.jobs;
      return [...list].sort((a, b) => (a.created_at < b.created_at ? 1 : -1)).slice(0, Number(query.limit || 100));
    }],
    ["GET", /^\/api\/v1\/batch-provision\/pending$/, () => m.state.jobs.filter((j) => j.status === "pending_review")],
    ["GET", /^\/api\/v1\/batch-provision\/([^/]+)\/recurrence-preview$/, ({ mt }) => {
      const j = m.state.jobs.find((x) => x.id === mt[1]);
      if (!j?.recurrence_rule || /UNTIL/.test(j.recurrence_rule)) return { windows: [] };
      return { windows: [2, 7, 9, 14, 16].map(win) };
    }],
    ["POST", /^\/api\/v1\/batch-provision\/class\/([^/]+)\/review$/, ({ mt, body }) =>
      decide(m.state.jobs.filter((j) => j.teaching_class_id === mt[1] && j.status === "pending_review"), body)],
    ["POST", /^\/api\/v1\/batch-provision\/([^/]+)\/review$/, ({ mt, body }) => decide(m.state.jobs.filter((j) => j.id === mt[1]), body)],
  ]);
}
