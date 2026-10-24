// Manual page: manual/jobs.md
import { D, H, MIN, ago, later } from "../lib/mock.mjs";

const STUDENT = "11156001@ntub.edu.tw";
const STUDENT_B = "11256003@ntub.edu.tw";
const STUDENT_C = "11156012@ntub.edu.tw";
const TEACHER = "lin@ntub.edu.tw";
const ADMIN = "admin@skylab.edu.tw";
const UID = {
  [STUDENT]: "7d3c1a2e-0001-4a00-9000-000000000001",
  [TEACHER]: "7d3c1a2e-0002-4a00-9000-000000000002",
  [ADMIN]: "7d3c1a2e-0003-4a00-9000-000000000003",
  [STUDENT_B]: "7d3c1a2e-0100-4a00-9000-0000000000aa",
  [STUDENT_C]: "7d3c1a2e-0101-4a00-9000-0000000000aa",
};

// Every task of the last 30 days; the admin sees all, other roles only their own.
function jobs(m) {
  const L = m.L;
  const vmTitle = (host, cores, mem) => L(`VM request: ${host} (${cores} cores / ${mem} MB)`, `開機申請：${host}（${cores} cores / ${mem} MB）`);
  const j = (id, kind, title, status, progress, email, created, updated, extra = {}) => ({
    id, kind, title, status, progress, message: null, user_id: UID[email], user_email: email,
    created_at: created, updated_at: updated,
    completed_at: ["completed", "failed", "cancelled"].includes(status) ? updated : null,
    detail_url: null, meta: {}, ...extra,
  });
  return [
    j("vm_request:5b2f8c1e-41d7-4c3a-9f0e-2a7d61c0b318", "vm_request", vmTitle("docker-lab", 2, 4096), "running", 60, STUDENT, ago(25 * MIN), ago(4 * MIN)),
    j("batch_provision:3f9c2a71-0d4e-4b8a-a2c5-91e7d3b4f602", "batch_provision", L("Batch provisioning: 3f9c2a71", "批次佈建：3f9c2a71"), "running", 45, TEACHER, ago(18 * MIN), ago(MIN)),
    j("resource_backup:8e41b0d2-6a3f-4c19-b7d5-0c2e9a4f1b73", "resource_backup", L("Backup: VMID 10318", "備份：VMID 10318"), "running", 35, STUDENT, ago(6 * MIN), ago(2 * MIN)),
    j("vm_request:c7a0e5d4-2b91-4f6e-8d3c-5e1f0a9b2c47", "vm_request", vmTitle("redis-cache", 1, 2048), "pending", 0, STUDENT_B, ago(2 * H), ago(2 * H)),
    j("spec_change:a4d93e0b-7c25-4e81-b6f0-3d2a8c9e1f54", "spec_change", L("Spec change: VMID 10231 (cpu)", "規格變更：VMID 10231（cpu）"), "blocked", null, STUDENT_C, ago(D + 3 * H), ago(5 * H), {
      message: L("Approved; waiting for the applicant to apply the new specs", "已核准，等待申請人套用新規格"),
    }),
    j("template:e2b7f4a9-58c0-4d3e-91a6-7f0c2d8b5e16", "template", L("Clone provisioning: web-fullstack-07", "克隆開通：web-fullstack-07"), "completed", 100, TEACHER, ago(3 * H), ago(3 * H - 4 * MIN)),
    j("vm_request:9d1c6b3e-0f47-4a28-b5e9-c3a7d2f80e61", "vm_request", vmTitle("cuda-training", 8, 32768), "failed", null, STUDENT_C, ago(5 * H), ago(4 * H), {
      message: L("Not enough free GPU memory on pve-gpu-01 for the requested vGPU profile", "pve-gpu-01 的 GPU 記憶體不足以建立申請的 vGPU 規格"),
    }),
    j("deletion:0b6e2d9f-3c84-47a1-a5f2-e8d1c7b40a39", "deletion", L("Delete old-notes", "刪除 old-notes"), "completed", 100, STUDENT, ago(D + 6 * H), ago(D + 6 * H - 2 * MIN)),
    j("resource_reset:f5a28c71-9e3d-4b06-8c4f-1d7e0b9a3c52", "resource_reset", L("Reset: VMID 20117", "重置：VMID 20117"), "completed", 100, STUDENT_B, ago(2 * D), ago(2 * D - 3 * MIN)),
    j("resource_restore:2c9e7a40-d1b5-4f83-9e6a-0b4d8f2c7e15", "resource_restore", L("Restore backup: VMID 10342", "還原備份：VMID 10342"), "completed", 100, STUDENT, ago(3 * D), ago(3 * D - 6 * MIN)),
    j("template:71d4b8e3-2a6f-4c90-b3e7-5f1a0c9d2e84", "template", L("Convert to template: Linux System Admin Lab", "轉換範本：Linux 系統管理實驗環境"), "completed", 100, TEACHER, ago(4 * D), ago(4 * D - 9 * MIN)),
    j("vm_request:4e8a1f6c-b2d7-4930-8e5c-a9f3d0b7c216", "vm_request", vmTitle("game-server", 4, 8192), "cancelled", null, STUDENT_B, ago(6 * D), ago(5 * D), {
      message: L("Cancelled by the applicant", "申請人已撤銷"),
    }),
  ];
}

function detail(m, item) {
  const L = m.L;
  const id = item.id;
  if (id.startsWith("vm_request:9d1c6b3e")) {
    return {
      item, output: null,
      error: [
        "TASK ERROR: vGPU profile nvidia-1436 (H200-4C) cannot be created on pve-gpu-01:",
        "  available framebuffer 3 GiB < required 4 GiB",
        "provision step 'start_vm' failed after 3 attempts (vmid=10377)",
      ].join("\n"),
      extra: {
        vmid: null, hostname: "cuda-training", resource_type: "vm", raw_status: "approved", cores: 8, memory: 32768,
        storage: "local-lvm", disk_size: 100, template_id: 9003, assigned_node: "pve-gpu-01",
        provisioning_status: "failed", start_at: ago(4 * H), end_at: later(30 * D),
        reason: L("Fine-tuning a small language model for the capstone project.", "畢業專題需要微調小型語言模型。"),
      },
    };
  }
  if (item.kind === "vm_request") {
    return {
      item, output: null, error: null,
      extra: {
        hostname: "docker-lab", resource_type: "lxc", raw_status: "approved", cores: 2, memory: 4096, storage: "local-lvm",
        rootfs_size: 16, ostemplate: "local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst", assigned_node: "pve-03",
        provisioning_status: "running", start_at: ago(30 * MIN), end_at: later(60 * D),
        reason: L("Docker and Compose exercises for the cloud computing course.", "雲端運算課程的 Docker 與 Compose 練習。"),
      },
    };
  }
  return { item, output: null, error: null, extra: { raw_status: item.status } };
}

function reminders(m) {
  const L = m.L;
  return [
    {
      id: "resource-expiry:10342:2026-10-14", kind: "resource_expiry", tone: "warning", icon: "schedule",
      title: L("flask-blog-lab is expiring soon", "flask-blog-lab 即將到期"),
      description: L("Expires in 2 days. If you want to keep it, take care of it ahead of time.", "將在 2 天後到期；需要保留請提前處理。"),
      time_label: "10/14", target: "/my-resources", occurred_at: later(2 * D),
    },
    {
      id: "request-review:5b2f8c1e:approved", kind: "request_review", tone: "success", icon: "check_circle",
      title: L("Resource request approved", "資源申請已通過"),
      description: L("docker-lab has been approved. You can find it in My Resources.", "docker-lab 已核准，可到我的資源查看。"),
      time_label: L("Today", "今天"), target: "/my-requests", occurred_at: ago(40 * MIN),
    },
    {
      id: "class-task:wk-6", kind: "class_task", tone: "info", icon: "assignment",
      title: L("Cloud Computing Practice: Docker Compose multi-service deployment", "雲端運算實務：Docker Compose 多服務部署"),
      description: L("Week 6 class task. Click to see the content and checklist.", "第 6 週課堂任務，點擊查看內容與檢查項目。"),
      time_label: L("Tomorrow", "明天"), target: "/courses", occurred_at: later(D),
    },
  ];
}

const ACTIVE = new Set(["pending", "running", "blocked"]);

function useJobMocks(m) {
  const all = jobs(m);
  const visible = () => {
    const u = m.currentUser();
    return u.role === "admin" ? all : all.filter((x) => x.user_email === u.email);
  };
  m.use([
    ["GET", /^\/api\/v1\/jobs\/$/, ({ query }) => {
      let items = visible();
      if (query.kinds) items = items.filter((x) => query.kinds.split(",").includes(x.kind));
      if (query.statuses) items = items.filter((x) => query.statuses.split(",").includes(x.status));
      if (query.active_only === "true") items = items.filter((x) => ACTIVE.has(x.status));
      return { items, total: items.length, active_count: visible().filter((x) => ACTIVE.has(x.status)).length, reminders: null };
    }],
    ["GET", /^\/api\/v1\/jobs\/([^/]+)$/, ({ mt }) => {
      const id = decodeURIComponent(mt[1]);
      const item = all.find((x) => x.id === id);
      return item ? detail(m, item) : { __status: 404, __body: { detail: "Job not found" } };
    }],
    ["GET", /^\/api\/v1\/courses\/reminders$/, () => (m.currentUser().role === "student" ? reminders(m) : [])],
    ["GET", /^\/api\/v1\/push\/vapid-public-key$/, () => ({ enabled: true, public_key: "BKx7d0b5c2YbWq3Jm1y0l3v8Q9nYw6pXr4tSgk2HcN1aVfE7uZoLmR5iT0qD8sJ3bG6wC9xP2yK4nF1vM7hA0e" })],
    ["POST", /^\/api\/v1\/push\/subscriptions$/, () => ({ message: "ok" })],
    ["POST", /^\/api\/v1\/push\/test$/, () => ({ message: "ok", sent: 1 })],
    ["DELETE", /^\/api\/v1\/push\/subscriptions$/, () => ({ message: "ok" })],
  ]);
}

// Headless Chrome has no push service: fake the service worker's PushManager so
// subscribing succeeds like it does in a real browser over https.
const FAKE_PUSH = `(() => {
  try {
    if (localStorage.getItem("jobs:desktopNotifications") === null) localStorage.setItem("jobs:desktopNotifications", "0");
  } catch {}
  const sub = {
    endpoint: "https://fcm.googleapis.com/fcm/send/eJx7-manual-shot",
    options: { applicationServerKey: null },
    toJSON() { return { endpoint: this.endpoint, keys: { p256dh: "BNc1", auth: "k9Q" } }; },
    unsubscribe: async () => { window.__pushSub = null; return true; },
  };
  const reg = { pushManager: {
    getSubscription: async () => window.__pushSub || null,
    subscribe: async () => { window.__pushSub = sub; return sub; },
  } };
  if (window.ServiceWorkerContainer) {
    ServiceWorkerContainer.prototype.getRegistration = async () => reg;
    ServiceWorkerContainer.prototype.register = async () => reg;
  }
})();`;

const sidebarBell = (s) => s.page.locator(`aside button[aria-label="${s.t("Background Jobs", "背景任務")}"]`).last();
const popover = (s) => s.page.locator('[class*="popover"]').first();

async function openPopover(s) {
  await s.click(sidebarBell(s), { wait: 600 });
}

export default {
  page: "jobs",
  role: "admin",
  mocks(m) {
    useJobMocks(m);
  },
  sections: [
    // s01 Follow your tasks
    async (s) => {
      // The Applicant column only fits next to the sidebar on a wider screen.
      await s.page.setViewportSize({ width: 1760, height: 900 });
      await s.go("/jobs", { wait: 800 });
      const table = s.page.locator('[data-guide="jobs-table"]');
      await s.shot(1, table, { crop: "viewport", scroll: false });
      await s.shot(2, table.locator("tbody tr").first(), { crop: "viewport", scroll: false });
      await s.shot(3, s.page.locator('[data-guide="jobs-stats"]'), { crop: "viewport", scroll: false });
    },
    // s02 Filter the list
    async (s) => {
      await s.go("/jobs", { wait: 800 });
      const kindBar = s.page.locator('[data-guide="jobs-kind-filter"]');
      const statusBar = s.page.locator('[data-guide="jobs-status-filter"]');
      // One screen: a type and a status picked, nothing matches, Clear filters shown.
      await s.click(kindBar.getByRole("button", { name: s.t("Restore backup", "還原備份") }), { wait: 500 });
      await s.click(statusBar.getByRole("button", { name: s.t("In Progress", "進行中") }), { wait: 700 });
      await s.shot(1, kindBar.locator('[role="group"]'), { crop: "viewport" });
      await s.shot(2, statusBar.locator('[role="group"]'), { crop: "viewport" });
      await s.shot(3, s.page.getByRole("button", { name: s.t("Clear filters", "清除篩選") }), { crop: "viewport" });
    },
    // s03 Open the details of a task
    async (s) => {
      await s.go("/jobs", { wait: 800 });
      await s.click(s.page.locator("tbody tr", { hasText: "cuda-training" }).first(), { wait: 900 });
      const dialog = s.page.getByRole("dialog");
      const dlgClass = (part) => dialog.locator(`[class*="${part}"]`).first();
      await s.shot(1, [dialog.locator("h2"), dlgClass("dialogJobId")]);
      await s.shot(2, dlgClass("dialogStatusRow"));
      await s.shot(3, dlgClass("dialogTimes"));
      const sections = dialog.locator('[class*="dialogBody"] > div');
      await s.shot(4, [sections.nth(2), sections.nth(3), sections.nth(4)]);
      await s.click(dialog.getByRole("button", { name: s.t("Close", "關閉") }), { wait: 500 });
      await s.click(s.page.locator("tbody tr", { hasText: "docker-lab" }).first(), { wait: 900 });
      await s.shot(5, [
        s.page.getByRole("dialog").locator('[class*="dialogStatusRow"]').first(),
        s.page.getByRole("dialog").getByRole("button", { name: s.t("Close", "關閉") }),
      ]);
    },
    // s04 Use the quick view in the sidebar
    {
      role: "student",
      run: async (s) => {
        await s.go("/jobs", { wait: 900 });
        await openPopover(s);
        const pop = popover(s);
        await s.shot(1, sidebarBell(s), { min: [1000, 640] });
        await s.shot(2, pop.getByRole("button", { name: /docker-lab/ }).first(), { min: [1000, 640] });
        await s.click(pop.getByRole("button", { name: s.t("Reminders", "提醒") }), { wait: 500 });
        // The tab switch changes too few pixels to split frames on its own.
        await s.shot(3, [
          popover(s).getByRole("button", { name: /expiring soon|即將到期/ }).first(),
          popover(s).getByRole("button", { name: s.t("Mark all as read", "全部標為已讀") }),
        ], { min: [1000, 640], newFrame: true });
        await s.shot(4, popover(s).getByRole("link", { name: s.t("View all jobs and history", "查看全部任務與歷史") }), { min: [1000, 640] });
      },
    },
    // s05 Turn on desktop notifications
    {
      role: "student",
      run: async (s) => {
        await s.page.context().grantPermissions(["notifications"]);
        await s.page.addInitScript({ content: FAKE_PUSH });
        await s.go("/jobs", { wait: 900 });
        await openPopover(s);
        const settings = s.page.getByRole("button", { name: s.t("Notification settings", "通知設定") });
        await s.shot(1, settings, { crop: { x: 40, y: 260, width: 1000, height: 640 } });
        await s.click(settings, { wait: 500 });
        const desktop = s.page.locator("label", { hasText: s.t("Desktop notifications", "桌面通知") });
        await s.click(desktop.locator('input[type="checkbox"]'), { wait: 1200 });
        await s.page.evaluate(() => document.querySelectorAll("[data-sonner-toast]").forEach((t) => t.remove()));
        await s.shot(2, desktop, { min: [1000, 640], newFrame: true });
        await s.shot(3, s.page.getByText(s.t("Background push is on", "已啟用背景推播")), { min: [1000, 640] });
        // Same settings view as an administrator.
        s.m.state.role = "admin";
        await s.go("/jobs", { wait: 900 });
        await openPopover(s);
        await s.click(s.page.getByRole("button", { name: s.t("Notification settings", "通知設定") }), { wait: 500 });
        await s.page.mouse.move(1430, 10);
        await s.settle(200);
        await s.shot(4, s.page.locator("label", { hasText: s.t("Only notify my jobs", "只通知「我的」任務") }), { min: [1000, 640] });
      },
    },
  ],
};
