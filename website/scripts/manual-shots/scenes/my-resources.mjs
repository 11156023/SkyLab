// Manual page: manual/my-resources.md
import { D, H, MIN, ago, later } from "../lib/mock.mjs";
import {
  LXC, VM, VNC_FIX, desktopJpeg, groupResources, quickSession, registerResources, studentMachines,
  teacherMachines, termServer, vncServer,
} from "../mocks/resources.mjs";

const row = (s, name) => s.page.locator("tbody tr", { hasText: name }).first();
const cell = (s, name, i) => row(s, name).locator("td").nth(i);
const moreBtn = (s, name) => row(s, name).getByRole("button", { name: s.t("More actions", "更多操作") });
const powerMenu = (s) => s.page.locator('[data-guide="resource-power-menu"]');
const menuItem = (s, en, zh) => powerMenu(s).getByRole("button", { name: s.t(en, zh), exact: true });
const confirmDialog = (s) => s.page.getByRole("alertdialog");
const confirmBtn = (s) => confirmDialog(s).getByRole("button").last();

// The machine table scrolls sideways at 1440 px: show the left or right end.
const scrollTable = (s, side) => s.page.evaluate((side) => { const w = document.querySelector("table")?.parentElement; if (w) w.scrollLeft = side === "right" ? 99999 : 0; }, side);

const tableBox = (s) => s.page.locator("table").first().locator("..");
const T = (s, extra = {}) => ({ scroll: false, crop: tableBox(s), pad: 12, ...extra });

// Crop around several elements (pad px around their union).
async function area(locs, pad = 12) {
  const bs = [];
  for (const l of locs) {
    const b = await l.boundingBox();
    if (b) bs.push(b);
  }
  const x = Math.min(...bs.map((b) => b.x)) - pad;
  const y = Math.min(...bs.map((b) => b.y)) - pad;
  return { x, y, width: Math.max(...bs.map((b) => b.x + b.width)) + pad - x, height: Math.max(...bs.map((b) => b.y + b.height)) + pad - y };
}
// The visible part of an element inside the sideways-scrolling table (a row is wider than the view),
// dx px narrower on each side so outlines of neighbouring cells do not overlap.
const inView = (s, loc, dx = 0) => ({
  scrollIntoViewIfNeeded: () => loc.scrollIntoViewIfNeeded(),
  evaluateHandle: (fn) => loc.evaluateHandle(fn),
  boundingBox: async () => {
    const b = await loc.boundingBox();
    const v = await tableBox(s).boundingBox();
    if (!b || !v) return b;
    const x = Math.max(b.x, v.x) + dx;
    return { x, y: b.y, width: Math.min(b.x + b.width, v.x + v.width) - dx - x, height: b.height };
  },
  toString: () => String(loc),
});
// The page header (title and buttons) and the quota card under it.
const headerCrop = async (s) => ({ scroll: false, crop: await area([s.page.locator("h1").first(), s.page.getByRole("button", { name: s.t("Request Resource", "申請資源") }), s.page.locator('[data-guide="resource-quota"]')], 24) });

async function openList(s) {
  await s.go("/my-resources", { wait: 700 });
  await row(s, "ubuntu-desktop").waitFor().catch(() => {});
}

// Requests still being created: one per stage of the "Creating" row.
function creatingRequests(m) {
  const base = { resource_type: "lxc", storage: "local-lvm", gpu_mapping_id: null, desired_node: null, requested_mode: "manual", review_comment: null, vmid: null, created_at: ago(D), end_at: later(90 * D), reason: "" };
  return [
    { ...base, id: "req-11", hostname: "react-todo-app", status: "pending", provisioning_status: null, cores: 2, memory: 2048, start_at: later(D), assigned_node: null, os_info: "Ubuntu 24.04 (LXC)" },
    { ...base, id: "req-12", hostname: "redis-cache-lab", status: "approved", provisioning_status: "pending", cores: 1, memory: 1024, start_at: later(2 * D + 3 * H), assigned_node: "pve-03", os_info: "Debian 12 (LXC)" },
    { ...base, id: "req-13", hostname: "spring-api-lab", status: "approved", provisioning_status: "running", cores: 2, memory: 4096, start_at: ago(4 * MIN), assigned_node: "pve-01", os_info: "Ubuntu 24.04 (LXC)" },
    { ...base, id: "req-14", hostname: "nodejs-chat", status: "approved", provisioning_status: "pending", cores: 2, memory: 2048, start_at: ago(25 * MIN), assigned_node: "pve-03", os_info: "Ubuntu 24.04 (LXC)" },
    { ...base, id: "req-15", hostname: "kali-security-lab", resource_type: "vm", status: "approved", provisioning_status: "failed", cores: 4, memory: 8192, start_at: ago(2 * H), assigned_node: "pve-02", os_info: "Kali Linux 2024.3" },
  ];
}

// Failed placeholder rows: creation failed (no vmid) and start failed (vmid present).
function failedRows(m) {
  const ph = (o) => ({
    request_id: null, teaching_class_id: null, allocation_scope: "personal", is_placeholder: true, status: "failed",
    can_control: false, can_delete: true, access_role: "owner", can_manage: true, machine_kind: "personal",
    environment_type: m.L("Resource request", "資源申請"), expiry_date: null, public_urls: [], ip_address: null, ...o,
  });
  return [
    ph({ vmid: null, request_id: "req-21", name: "postgres-lab", type: "lxc", node: "pve-03", os_info: "Debian 12 (LXC)" }),
    ph({ vmid: 10533, request_id: "req-22", name: "win-server-lab", type: "qemu", node: "pve-02", os_info: "Windows Server 2022" }),
  ];
}

async function startVnc(s) {
  const jpg = await desktopJpeg(s);
  s.m.ws = (p, ws) => { if (p.startsWith("/ws/vnc/")) vncServer(ws, jpg); };
  await openList(s);
  await s.page.evaluate(VNC_FIX);
  await s.click(row(s, "ubuntu-desktop").getByRole("button", { name: s.t("Console", "控制台") }), { wait: 400 });
  await s.page.locator('[role="dialog"] canvas').first().waitFor({ timeout: 10000 });
  await s.page.getByRole("dialog").getByText(s.t("Connected", "已連接"), { exact: true }).waitFor({ timeout: 10000 });
  await s.page.waitForTimeout(1200);
}

async function openTerminal(s) {
  await s.click(row(s, "flask-blog-lab").getByRole("button", { name: s.t("Terminal", "終端機") }), { wait: 400 });
  await s.page.getByRole("dialog").locator(".xterm").first().waitFor();
  await s.page.waitForTimeout(900);
}

export default {
  page: "my-resources",
  role: "student",
  mocks(m) {
    registerResources(m);
  },
  sections: [
    // s01 Check your quota
    {
      mocks(m) {
        m.state.quota = {
          used_cpu_cores: 15, used_memory_mb: 20480, used_disk_gb: 112, used_instances: 3,
          quota: { max_cpu_cores: 16, max_memory_mb: 32768, max_disk_gb: 0, max_instances: 5 },
        };
      },
      run: async (s) => {
        await openList(s);
        const card = s.page.locator('[data-guide="resource-quota"]');
        const meter = (label) => card.getByText(label, { exact: true }).locator("xpath=..");
        await s.shot(1, card, { scroll: false });
        await s.shot(2, [meter(s.t("Disk", "磁碟"))], { scroll: false });
        await s.shot(3, [meter("CPU"), card.locator("p")], { scroll: false });
      },
    },
    // s02 Read the machine list
    async (s) => {
      await openList(s);
      const n = "ubuntu-desktop";
      await s.shot(1, inView(s, cell(s, n, 0), 6), T(s));
      await s.shot(2, cell(s, n, 1).locator("span").first(), T(s));
      await s.shot(3, inView(s, cell(s, n, 2), 6), T(s));
      await s.shot(4, cell(s, n, 3).locator("span").first(), T(s));
      await s.shot(5, inView(s, cell(s, n, 4), 6), T(s));
      // The expiry column is cut off at the right edge until the table scrolls.
      await scrollTable(s, "right");
      await s.shot(6, [cell(s, n, 5), cell(s, "jupyter-ml-lab", 5), cell(s, "win11-office", 5)].map((c) => inView(s, c, 6)), T(s));
      await s.shot(7, inView(s, cell(s, n, 6), 6), T(s));
      await s.shot(8, inView(s, cell(s, n, 7), 6), T(s));
    },
    // s03 Open a machine's detail page
    async (s) => {
      await openList(s);
      await s.shot(1, inView(s, row(s, "flask-blog-lab")), T(s));
      await s.click(cell(s, "flask-blog-lab", 2), { wait: 900 });
      await s.page.getByRole("heading", { name: "flask-blog-lab" }).waitFor();
      await s.shot(2, s.page.locator("h1").first(), { crop: "viewport" });
    },
    // s04 Open the console or terminal
    {
      run: async (s) => {
        s.m.ws = (p, ws) => { if (p.startsWith("/ws/terminal/")) termServer(ws); };
        await openList(s);
        await scrollTable(s, "right");
        await s.shot(1, [cell(s, "flask-blog-lab", 3).locator("span").first(), cell(s, "ubuntu-desktop", 3).locator("span").first()], T(s));
        await s.shot(2, [
          row(s, "flask-blog-lab").getByRole("button", { name: s.t("Terminal", "終端機") }),
          row(s, "ubuntu-desktop").getByRole("button", { name: s.t("Console", "控制台") }),
        ], T(s));
        await openTerminal(s);
        await s.shot(3, s.page.getByRole("dialog").getByRole("button", { name: s.t("Close", "關閉") }));
      },
    },
    // s05 Use the VM console
    async (s) => {
      await startVnc(s);
      const dlg = s.page.getByRole("dialog");
      await s.click(dlg.getByRole("button", { name: s.t("Clipboard", "剪貼簿"), exact: true }), { wait: 300 });
      await s.fill(dlg.locator("textarea"), "git clone https://github.com/ntub-os-lab/scheduler-lab.git");
      await s.page.waitForTimeout(300);
      await s.shot(1, dlg.getByRole("button", { name: "Ctrl+Alt+Del" }));
      await s.shot(2, [dlg.getByRole("button", { name: s.t("Clipboard", "剪貼簿"), exact: true }), dlg.locator('[role="region"]')]);
      await s.shot(3, dlg.locator("button[title]").filter({ has: s.page.locator("span", { hasText: /^fullscreen$/ }) }));
    },
    // s06 Use the container terminal
    async (s) => {
      let drop = false;
      s.m.ws = (p, ws) => { if (p.startsWith("/ws/terminal/")) termServer(ws, { drop }); };
      await openList(s);
      await openTerminal(s);
      const dlg = s.page.getByRole("dialog");
      // Select the "Flask 3.0.3" line with the mouse, as a user would before copying.
      const screen = dlg.locator(".xterm-screen");
      const box = await screen.boundingBox();
      const lineH = 17;
      await s.page.mouse.move(box.x + 4, box.y + lineH * 4 + 8);
      await s.page.mouse.down();
      await s.page.mouse.move(box.x + 300, box.y + lineH * 5 + 8, { steps: 8 });
      await s.page.mouse.up();
      await s.page.waitForTimeout(300);
      await s.shot(1, screen);
      await s.shot(2, [dlg.getByRole("button", { name: s.t("Clear", "清除") }), dlg.getByRole("button", { name: s.t("Reset", "重置") })]);
      await s.shot(3, dlg.locator("button").filter({ has: s.page.locator("span", { hasText: /^fullscreen$/ }) }));
      // Reopen with a connection that drops.
      await s.click(dlg.getByRole("button", { name: s.t("Close", "關閉") }), { wait: 500 });
      drop = true;
      await s.click(row(s, "flask-blog-lab").getByRole("button", { name: s.t("Terminal", "終端機") }), { wait: 1500 });
      await s.page.getByRole("dialog").getByText(s.t("Connection Lost", "連線已中斷"), { exact: true }).waitFor();
      await s.shot(4, s.page.getByRole("dialog").getByText(s.t("Connection Lost", "連線已中斷"), { exact: true }).locator(".."));
    },
    // s07 Power a machine on or off
    async (s) => {
      await openList(s);
      await scrollTable(s, "right");
      await s.click(moreBtn(s, "flask-blog-lab"), { wait: 400 });
      await s.shot(1, moreBtn(s, "flask-blog-lab"), T(s));
      await s.shot(2, powerMenu(s), T(s));
      await s.click(menuItem(s, "Reboot", "重新啟動"), { wait: 600 });
      await s.page.mouse.move(700, 880);
      await s.shot(3, cell(s, "flask-blog-lab", 3).locator("span").first(), T(s, { newFrame: true }));
    },
    // s08 Delete a machine
    async (s) => {
      await openList(s);
      await scrollTable(s, "right");
      await s.click(moreBtn(s, "flask-blog-lab"), { wait: 400 });
      await s.shot(1, menuItem(s, "Delete", "刪除"), T(s));
      await s.click(menuItem(s, "Delete", "刪除"), { wait: 400 });
      await s.shot(2, confirmBtn(s));
      await s.click(confirmBtn(s), { wait: 300 });
      const toast = s.page.locator("[data-sonner-toast]").first();
      await toast.waitFor();
      await s.page.waitForTimeout(600);
      await s.shot(3, toast, { crop: { x: 330, y: 0, width: 1110, height: 420 }, scroll: false });
    },
    // s09 Convert a machine to a template (teachers and administrators)
    {
      role: "teacher",
      mocks(m) { m.state.list = teacherMachines(m); },
      run: async (s) => {
        await s.go("/my-resources", { wait: 700 });
        await row(s, "k8s-demo-master").waitFor();
        await scrollTable(s, "right");
        await s.click(moreBtn(s, "k8s-demo-master"), { wait: 400 });
        const menu = { scroll: false, crop: await area([tableBox(s), powerMenu(s)]) };
        await s.shot(1, moreBtn(s, "k8s-demo-master"), menu);
        await s.shot(2, menuItem(s, "Convert to template", "轉成範本"), menu);
        await s.click(menuItem(s, "Convert to template", "轉成範本"), { wait: 600 });
        const dlg = s.page.getByRole("dialog");
        const name = dlg.locator("input[type=text], input:not([type])").first();
        await s.fill(name, s.t("Kubernetes lab (Ubuntu 24.04)", "Kubernetes 實驗環境（Ubuntu 24.04）"));
        await s.shot(2, name);
      },
    },
    // s10 Follow a request that is being created
    {
      mocks(m) { m.state.requests = creatingRequests(m); },
      run: async (s) => {
        await openList(s);
        const badge = (n) => cell(s, n, 3).locator("span").first();
        await s.shot(1, badge("react-todo-app"), T(s));
        await s.shot(2, badge("redis-cache-lab"), T(s));
        await s.shot(3, badge("spring-api-lab"), T(s));
        await s.shot(4, badge("nodejs-chat"), T(s));
        await s.shot(5, badge("kali-security-lab"), T(s));
      },
    },
    // s11 Retry or remove a machine that failed
    {
      mocks(m) { m.state.list = [...failedRows(m), ...studentMachines(m).slice(0, 2)]; },
      run: async (s) => {
        await s.go("/my-resources", { wait: 700 });
        await row(s, "postgres-lab").waitFor();
        await scrollTable(s, "right");
        await s.click(moreBtn(s, "postgres-lab"), { wait: 400 });
        await s.shot(1, [cell(s, "postgres-lab", 3).locator("span").first(), menuItem(s, "Rebuild", "重建")], T(s));
        await s.shot(2, [menuItem(s, "Delete", "刪除"), cell(s, "win-server-lab", 3).locator("span").first()], T(s));
      },
    },
    // s12 Work with course and quick practice environments
    {
      mocks(m) {
        m.state.list = [...groupResources(m), ...studentMachines(m).slice(0, 2)];
        m.state.sessions = [quickSession(m)];
      },
      run: async (s) => {
        await s.go("/my-resources", { wait: 700 });
        const qp = s.t("Docker Compose practice", "Docker Compose 練習");
        await row(s, qp).waitFor();
        const grp = row(s, qp);
        await s.shot(1, [grp.locator("td").nth(0), grp.locator("td").nth(2)], T(s));
        await s.shot(2, grp.locator("td").nth(4), T(s));
        await scrollTable(s, "right");
        const menuBtn = grp.getByRole("button", { name: s.t("Group power", "整組電源") });
        await s.click(menuBtn, { wait: 400 });
        await s.shot(3, [menuBtn, powerMenu(s)], T(s));
        await s.click(menuItem(s, "End Practice", "結束練習"), { wait: 400 });
        await s.shot(4, confirmBtn(s));
      },
    },
    // s13 Download the connection app
    async (s) => {
      await openList(s);
      await s.shot(0, s.page.getByRole("link", { name: s.t("Download Connection App", "下載連線 App") }), await headerCrop(s));
    },
    // s14 Request a new resource
    async (s) => {
      await openList(s);
      await s.shot(0, s.page.getByRole("button", { name: s.t("Request Resource", "申請資源") }), await headerCrop(s));
    },
  ],
};
