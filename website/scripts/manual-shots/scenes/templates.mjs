// Manual page: manual/templates.md
import { registerResources, teacherMachines } from "../mocks/resources.mjs";
import { MAIN, frame, res, useTemplates } from "../mocks/resadmin.mjs";

const C = { crop: MAIN };
const CS = { crop: MAIN, scroll: false };
const band = (height) => ({ crop: { ...MAIN, height }, scroll: false });
// From the top of the page down to the bottom of `loc` (a menu that opens low on the page).
const downTo = async (loc, pad = 16) => {
  const b = await loc.boundingBox();
  return { crop: { ...MAIN, height: Math.max(500, Math.min(b.y + b.height + pad, 900) - MAIN.y) }, scroll: false };
};
// Crop around several elements, full content width.
async function area(locs, pad = 16) {
  const bs = [];
  for (const l of locs) {
    const b = await l.boundingBox();
    if (b) bs.push(b);
  }
  const y = Math.min(...bs.map((b) => b.y)) - pad;
  return { crop: { x: MAIN.x, y, width: MAIN.width, height: Math.max(...bs.map((b) => b.y + b.height)) + pad - y }, scroll: false };
}

const tplRow = (s, name) => s.page.locator("tbody tr", { hasText: name }).first();
const menuBtn = (s, name) => tplRow(s, name).locator('[data-guide="templates-menu"]');
const rowMenu = (s) => s.page.locator('[class*="_rowMenu_"]').first();
const item = (s, en, zh) => rowMenu(s).getByRole("button", { name: s.t(en, zh) });
const dialog = (s) => s.page.getByRole("dialog");
const confirmDlg = (s) => s.page.getByRole("alertdialog");
const field = (s, id) => dialog(s).locator(`#${id}`).locator("xpath=..");

const DOCKER = ["Docker Lab (Ubuntu 24.04)", "Docker 實驗環境（Ubuntu 24.04）"];
const K8S = ["Kubernetes Lab", "Kubernetes 實驗環境"];
const MYSQL = ["MySQL 8.4 Lab", "MySQL 8.4 實驗環境"];

function base(m) {
  registerResources(m);
  m.state.list = teacherMachines(m);
  useTemplates(m);
}

async function openMenu(s, name) {
  await s.click(menuBtn(s, name), { wait: 400 });
}

// Sets a range input through React's value setter.
async function slide(locator, value) {
  await locator.evaluate((el, v) => {
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    set.call(el, String(v));
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }, value);
}

export default {
  page: "templates",
  role: "teacher",
  mocks: base,
  sections: [
    // s01 Create a template from a VM
    async (s) => {
      await s.go("/templates", { wait: 800 });
      const createBtn = s.page.locator('[data-guide="templates-create"]').first();
      await s.shot(1, createBtn, band(150));
      await s.click(createBtn, { wait: 700 });
      const d = dialog(s);
      await d.locator("#tpl-source").selectOption("10512");
      await s.fill(d.locator("#tpl-name"), s.t("Kubernetes Lab v2 (k3s + Helm)", "Kubernetes 實驗環境 v2（k3s + Helm）"));
      await s.fill(d.locator("#tpl-desc"), s.t("Single-node k3s with kubectl, Helm and the course's sample charts. For Cloud Computing (IM 4A), weeks 9 to 12.", "k3s 單節點叢集，內含 kubectl、Helm 與課程範例 chart，供雲端運算（資管四甲）第 9 到 12 週使用。"));
      await s.click(d.getByRole("button", { name: s.t("Visible to Everyone", "全部可見") }), { wait: 200 });
      await frame(s);
      await s.shot(2, field(s, "tpl-source"));
      await s.shot(3, [field(s, "tpl-name"), field(s, "tpl-desc")]);
      await s.shot(4, d.getByRole("group", { name: s.t("Visibility", "可見範圍") }));
      // Step 5: GPU, default specs, attachments.
      await d.getByText(s.t("Set default CPU and memory", "自訂預設規格")).click();
      await s.page.waitForTimeout(200);
      await slide(d.locator("#tpl-cores"), 4);
      await slide(d.locator("#tpl-memory"), 8192);
      await d.locator('input[type="file"]').setInputFiles([
        { name: s.t("k8s-lab-guide.pdf", "k8s 實驗手冊.pdf"), mimeType: "application/pdf", buffer: Buffer.alloc(1_830_000, 1) },
      ]);
      await s.settle(300);
      const gpu = d.locator("label", { hasText: s.t("This template needs a GPU", "這個範本需要 GPU") });
      const spec = d.locator("label", { hasText: s.t("Set default CPU and memory", "自訂預設規格") });
      await d.locator('[class*="_attachList_"]').scrollIntoViewIfNeeded();
      await frame(s);
      await s.shot(5, [gpu, spec, d.locator("#tpl-cores"), d.locator("#tpl-memory"), d.locator('[class*="_attachList_"]')], { scroll: false });
      const start = d.getByRole("button", { name: s.t("Start Conversion", "開始轉換") });
      await s.shot(6, start, { scroll: false });
      await s.click(start, { wait: 500 });
      await frame(s);
      await s.shot(6, confirmDlg(s).getByRole("button", { name: s.t("Shut Down and Convert", "關機並轉換") }));
      await s.click(confirmDlg(s).getByRole("button", { name: s.t("Shut Down and Convert", "關機並轉換") }), { wait: 1200 });
      await frame(s);
      await s.shot(7, tplRow(s, s.t("Kubernetes Lab v2", "Kubernetes 實驗環境 v2")).locator('[data-guide="templates-status"] span'), band(500));
    },
    // s02 Clone machines from a template
    async (s) => {
      await s.go("/templates", { wait: 800 });
      await openMenu(s, "CUDA 12.6");
      await frame(s);
      await s.shot(1, [tplRow(s, "CUDA 12.6").locator('[data-guide="templates-status"] span'), item(s, "Clone", "克隆開通")], band(500));
      await s.click(item(s, "Clone", "克隆開通"), { wait: 1000 });
      const d = dialog(s);
      await s.fill(d.locator("#clone-hostname"), "yolo-train");
      await s.fill(d.locator("#clone-count"), "60");
      await slide(d.locator("#clone-cores"), 6);
      await slide(d.locator("#clone-memory"), 24576);
      await frame(s);
      await s.shot(2, field(s, "clone-hostname"));
      await s.shot(3, field(s, "clone-count"));
      await s.shot(4, [d.locator("#clone-cores"), d.locator("#clone-memory"), d.locator('[class*="_diskFixed_"]')]);
      await s.fill(d.locator("#clone-password"), "Yolo#2026lab");
      await d.locator("#clone-gpu").selectOption("h200-vgpu");
      await s.settle(300);
      await d.locator("#clone-gpu-profile").selectOption("nvidia-1062");
      await d.locator('[class*="_checkLine_"]').last().scrollIntoViewIfNeeded();
      await s.settle(200);
      await frame(s);
      await s.shot(5, field(s, "clone-password"), { scroll: false });
      await s.shot(6, [d.locator("#clone-gpu"), d.locator("#clone-gpu-profile")], { scroll: false });
      await s.shot(7, d.locator('[class*="_checkLine_"]').last(), { scroll: false });
      await s.shot(8, d.getByRole("button", { name: s.t("Start Cloning", "開始克隆") }), { scroll: false });
    },
    // s03 Edit a template or change its visibility
    async (s) => {
      await s.go("/templates", { wait: 800 });
      await openMenu(s, "Ubuntu 24.04 Desktop");
      await frame(s);
      await s.shot(1, item(s, "Edit / Visibility", "編輯 / 可見範圍"), band(500));
      await s.click(item(s, "Edit / Visibility", "編輯 / 可見範圍"), { wait: 900 });
      const d = dialog(s);
      await frame(s);
      await s.shot(2, [field(s, "tpl-name"), field(s, "tpl-desc"), d.getByRole("group", { name: s.t("Visibility", "可見範圍") })]);
      const dropzone = d.locator('input[type="file"]').locator("xpath=..");
      await dropzone.scrollIntoViewIfNeeded();
      await s.settle(200);
      await frame(s);
      await s.shot(3, [d.locator('[class*="_attachList_"]'), dropzone], { scroll: false });
      await s.shot(4, d.getByRole("button", { name: s.t("Save Changes", "儲存變更") }), { scroll: false });
    },
    // s04 Read or download a template's manual
    async (s) => {
      await s.go("/templates", { wait: 800 });
      await openMenu(s, "Ubuntu 24.04 Desktop");
      await frame(s);
      await s.shot(1, item(s, "Manual (2)", "使用手冊（2）"), band(500));
      await s.click(item(s, "Manual (2)", "使用手冊（2）"), { wait: 900 });
      await frame(s);
      await s.shot(2, await dialog(s).getByRole("button", { name: s.t("Download", "下載") }).all());
    },
    // s05 Update a template with an update cycle
    {
      mocks(m) {
        m.state.list = [
          ...teacherMachines(m),
          res({ vmid: 10530, name: "tpl-9102-edit", type: "lxc", node: "pve-01", status: "stopped", owner_name: m.users.teacher.full_name,
            environment_type: m.L("Template update", "範本更新"), os_info: "Ubuntu 24.04 (LXC)", ip_address: "10.20.5.30", cpu: 0, mem: 0, uptime: 0, access_role: "owner", expiry_date: null }),
        ];
      },
      run: async (s) => {
        await s.go("/templates", { wait: 800 });
        await openMenu(s, s.t(...DOCKER));
        await frame(s);
        await s.shot(1, item(s, "Start Update Cycle", "開始更新循環"), band(500));
        await s.go("/my-resources", { wait: 1200 });
        const staging = s.page.locator('tr[data-guide="resource-card"]', { hasText: "tpl-9102-edit" }).first();
        await staging.evaluate((el) => el.scrollIntoView({ block: "center" }));
        await s.settle(200);
        const thead = s.page.locator("thead").first();
        await frame(s);
        // The table scrolls sideways: the name and the action buttons are two frames.
        await s.shot(2, staging.locator('[data-guide="resource-open-detail"]'), await area([thead, staging]));
        await s.page.evaluate(() => { const w = document.querySelector("table")?.parentElement; if (w) w.scrollLeft = 99999; });
        await s.settle(200);
        await frame(s);
        await s.shot(3, [staging.locator('[data-guide="resource-more-actions"]'), staging.locator('[data-guide="resource-console"]')], await area([thead, staging]));
        await s.go("/templates", { wait: 800 });
        await openMenu(s, s.t(...K8S));
        await frame(s);
        await s.shot(4, [tplRow(s, s.t(...K8S)).locator('[data-guide="templates-status"] span'), item(s, "Finish Update (promote to new version)", "完成更新（轉為新版）")], await downTo(rowMenu(s)));
        await s.click(item(s, "Finish Update (promote to new version)", "完成更新（轉為新版）"), { wait: 500 });
        await frame(s);
        await s.shot(4, confirmDlg(s).getByRole("button", { name: s.t("Shut Down and Convert", "關機並轉換") }));
        await s.shot(5, confirmDlg(s).locator("p").first());
      },
    },
    // s06 Retry a failed conversion
    async (s) => {
      await s.go("/templates", { wait: 800 });
      await openMenu(s, s.t(...MYSQL));
      await frame(s);
      await s.shot(1, [tplRow(s, s.t(...MYSQL)).locator('[data-guide="templates-status"] span'), tplRow(s, s.t(...MYSQL)).locator('[class*="_nameError_"]')], band(500));
      await s.shot(2, tplRow(s, s.t(...MYSQL)).locator('[class*="_nameError_"]'), CS);
      await s.shot(3, item(s, "Retry Conversion", "重新轉換"), band(500));
      await s.click(item(s, "Retry Conversion", "重新轉換"), { wait: 500 });
      await frame(s);
      await s.shot(3, confirmDlg(s).getByRole("button", { name: s.t("Shut Down and Convert", "關機並轉換") }));
    },
    // s07 Delete a template
    async (s) => {
      await s.go("/templates", { wait: 800 });
      await openMenu(s, "CentOS 7");
      await frame(s);
      await s.shot(1, [menuBtn(s, "CentOS 7"), item(s, "Delete Template", "刪除範本")], await downTo(rowMenu(s)));
      await s.click(item(s, "Delete Template", "刪除範本"), { wait: 500 });
      await frame(s);
      await s.shot(2, confirmDlg(s).getByRole("button", { name: s.t("Confirm Delete", "確認刪除") }));
    },
  ],
};
