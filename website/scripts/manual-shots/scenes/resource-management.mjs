// Manual page: manual/resource-management.md
import { registerResources, termServer } from "../mocks/resources.mjs";
import { frame, useResourceMgmt, useTemplates } from "../mocks/resadmin.mjs";

// The table is wider than the 1440 px viewport, so this page is shot at 1880 px
// (the crop leaves out the sidebar).
const WIDE = { x: 340, y: 24, width: 1520, height: 852 };
const MAIN = WIDE;

const C = { crop: MAIN };
const CS = { crop: MAIN, scroll: false };
const band = (height, y = 24) => ({ crop: { ...MAIN, y, height }, scroll: false });

const row = (s, name) => s.page.locator('tr[data-guide="resource-mgmt-row"]', { hasText: name }).first();
const cell = (s, name, i) => row(s, name).locator("td").nth(i);
const badge = (s, name) => cell(s, name, 4).locator("span").first();
const menuBtn = (s, name) => row(s, name).getByRole("button", { name: s.t("Power Control", "電源控制") });
const menu = (s) => s.page.locator('[data-guide="resource-power-menu"]');
const menuItem = (s, en, zh) => menu(s).getByRole("button", { name: s.t(en, zh), exact: true });
const group = (s, title) => s.page.locator('tr[data-guide="resource-mgmt-env-group"]', { hasText: title }).first();
const search = (s) => s.page.getByRole("textbox", { name: s.t("Search name, VMID, IP or owner", "搜尋名稱、VMID、IP 或擁有者") });
const statusGroup = (s) => s.page.getByRole("group", { name: s.t("Filter by status", "依狀態篩選") });
const bar = (s) => s.page.locator('[class*="_batchBar_"]');
const toast = (s) => s.page.locator("[data-sonner-toast]").first();
const LINUX = ["Linux System Administration (IM 2A)", "Linux 系統管理（資管二甲）"];

// Crop around several elements (pad px around their union), full content width.
async function area(locs, pad = 16) {
  const bs = [];
  for (const l of locs) {
    const b = await l.boundingBox();
    if (b) bs.push(b);
  }
  const y = Math.min(...bs.map((b) => b.y)) - pad;
  return { x: MAIN.x, y, width: MAIN.width, height: Math.max(...bs.map((b) => b.y + b.height)) + pad - y };
}
// From the page title down to the bottom of `loc`.
const downTo = async (s, loc, pad = 16) => {
  const b = await loc.boundingBox();
  return { crop: { ...MAIN, height: Math.min(b.y + b.height + pad, 900) - MAIN.y }, scroll: false };
};
// Target whose outline sits dx px inside the element, for table cells that touch each other.
const insetX = (loc, dx = 8) => ({
  scrollIntoViewIfNeeded: () => loc.scrollIntoViewIfNeeded(),
  evaluateHandle: (fn) => loc.evaluateHandle(fn),
  boundingBox: async () => {
    const b = await loc.boundingBox();
    return b && { x: b.x + dx, y: b.y, width: b.width - 2 * dx, height: b.height };
  },
  toString: () => String(loc),
});
// Park the mouse in the empty top-right corner, away from rows and the AI button.
const park = (s) => s.page.mouse.move(1872, 6);

function base(m) {
  registerResources(m);
  useResourceMgmt(m);
  useTemplates(m);
  m.ws = (path, ws) => {
    if (path.startsWith("/ws/terminal/")) termServer(ws, { name: "jupyter-ml-lab" });
  };
}

async function start(s, { collapse = true } = {}) {
  await s.page.setViewportSize({ width: 1880, height: 900 });
  await s.go("/resource-mgmt", { wait: 800 });
  if (collapse) {
    const open = s.page.locator('tr[data-guide="resource-mgmt-env-group"] button[aria-expanded="true"]');
    for (let i = 0; i < 5 && (await open.count()) > 0; i++) await open.first().click();
    await s.settle(300);
  }
  await park(s);
}

export default {
  page: "resource-management",
  role: "admin",
  mocks: base,
  sections: [
    // s01 Find a machine
    async (s) => {
      await start(s);
      await frame(s);
      await s.shot(1, statusGroup(s), band(420));
      await s.fill(search(s), s.t("Alex 10.20", "王小明 10.20"), { wait: 400 });
      await frame(s);
      await s.shot(2, [search(s), row(s, "ubuntu-desktop")], band(260));
      await s.fill(search(s), s.t("Alex 10.30", "王小明 10.30"), { wait: 400 });
      await frame(s);
      await s.shot(3, s.page.getByRole("button", { name: s.t("Clear search", "清除搜尋") }), band(560));
    },
    // s02 Read the table
    async (s) => {
      await start(s);
      await s.settle(1000);
      const r = "ubuntu-desktop";
      await frame(s);
      await s.shot(1, insetX(cell(s, r, 1)), CS);
      await s.shot(2, insetX(cell(s, r, 2)), CS);
      await s.shot(3, insetX(cell(s, r, 3)), CS);
      await s.shot(4, badge(s, r), CS);
      await s.shot(5, insetX(cell(s, r, 5)), CS);
      await s.shot(6, [insetX(cell(s, r, 6)), insetX(cell(s, "lin-demo-server", 6))], CS);
      await s.shot(7, insetX(cell(s, r, 7)), CS);
      await s.shot(8, [insetX(cell(s, r, 8)), insetX(cell(s, "cuda-yolo-train", 8))], CS);
    },
    // s03 Work with course and quick practice environments
    async (s) => {
      await start(s, { collapse: false });
      const g = group(s, s.t(...LINUX));
      await g.evaluate((el) => { el.scrollIntoView({ block: "start" }); window.scrollBy(0, -96); });
      await s.settle(300);
      await park(s);
      await frame(s);
      const last = s.page.locator("tr", { hasText: "linux-lab-11256005" }).first();
      const crop = { crop: await area([s.page.locator("thead").first(), g, last], 8), scroll: false };
      await s.shot(1, [insetX(g.locator("td").nth(0), 4), insetX(g.locator("td").nth(1), 4)], crop);
      await s.shot(2, g.locator("td").nth(4).locator("span"), crop);
      const child = s.page.locator("tr", { hasText: "linux-lab-11256001" }).first();
      await s.shot(4, [child.getByRole("button", { name: s.t("Terminal", "終端機") }), child.getByRole("button", { name: s.t("Power Control", "電源控制") }), insetX(child.locator("td").nth(6))], crop);
      const groupBtn = g.getByRole("button", { name: s.t("Group power", "整組電源") });
      await s.click(groupBtn, { wait: 400 });
      await frame(s);
      await s.shot(3, [groupBtn, menu(s)], { crop: await area([s.page.locator("thead").first(), menu(s)]), scroll: false });
    },
    // s04 Open a machine's console
    async (s) => {
      await start(s);
      await frame(s);
      await s.shot(1, badge(s, "jupyter-ml-lab"), CS);
      await s.shot(2, row(s, "jupyter-ml-lab").getByRole("button", { name: s.t("Terminal", "終端機") }), CS);
      await s.shot(3, [badge(s, "thesis-gpu"), row(s, "thesis-gpu").getByRole("button", { name: s.t("Console", "控制台") })], CS);
      await s.click(row(s, "jupyter-ml-lab").getByRole("button", { name: s.t("Terminal", "終端機") }), { wait: 1500 });
      await frame(s);
      await s.shot(2, s.page.getByRole("dialog").locator('[class*="_header_"]').first(), { crop: { x: 340, y: 0, width: 1520, height: 900 } });
    },
    // s05 Power a single machine on or off
    async (s) => {
      await start(s);
      await s.click(menuBtn(s, "rag-chatbot"), { wait: 400 });
      await frame(s);
      const open = await downTo(s, menu(s));
      await s.shot(1, menuBtn(s, "rag-chatbot"), open);
      await s.shot(2, menu(s), open);
      await s.click(menuItem(s, "Shutdown", "關機"), { wait: 600 });
      await park(s);
      await frame(s);
      await s.shot(3, [toast(s), badge(s, "rag-chatbot")], await downTo(s, row(s, "rag-chatbot")));
    },
    // s06 Act on several machines at once
    {
      mocks(m) {
        m.use([["POST", /^\/api\/v1\/resources\/batch$/, () => ({ __delay: 30000, __body: { results: [] } })]]);
      },
      run: async (s) => {
        await start(s);
        const names = ["ubuntu-desktop", "lin-demo-server", "jupyter-ml-lab", "docker-lab", "rag-chatbot", "mysql-replica"];
        for (const n of names) await s.click(cell(s, n, 0).locator("label"), { wait: 120 });
        await s.click(bar(s).getByRole("button", { name: s.t("More", "更多") }), { wait: 400 });
        await park(s);
        await frame(s);
        await s.shot(1, [...names.map((n) => cell(s, n, 0).locator("label")), s.page.locator("thead th").first().locator("label")], CS);
        await s.shot(2, bar(s).locator('[class*="_batchCount_"]'), CS);
        await s.shot(3, [bar(s).getByRole("button", { name: s.t("Delete", "刪除"), exact: true }), menu(s)], CS);
        await s.shot(4, [bar(s).getByRole("button", { name: s.t("Start", "啟動"), exact: true }), bar(s).getByRole("button", { name: s.t("Shutdown", "關機"), exact: true })], CS);
        await s.shot(7, bar(s).getByRole("button", { name: s.t("Clear selection", "取消選取") }), CS);
        // Toggle the menu closed (Esc while it is open also clears the selection).
        await s.click(bar(s).getByRole("button", { name: s.t("More", "更多") }), { wait: 400 });
        await s.click(bar(s).getByRole("button", { name: s.t("Delete", "刪除"), exact: true }), { wait: 500 });
        const dlg = s.page.getByRole("alertdialog");
        await frame(s);
        await s.shot(5, dlg.getByRole("button", { name: s.t("Confirm Delete", "確認刪除") }));
        await s.click(dlg.getByRole("button", { name: s.t("Cancel", "取消") }), { wait: 400 });
        await bar(s).getByRole("button", { name: s.t("Shutdown", "關機"), exact: true }).click();
        await s.page.waitForTimeout(800);
        await frame(s);
        await s.shot(6, bar(s).locator('[role="status"]'), band(260));
      },
    },
    // s07 Delete a machine
    async (s) => {
      await start(s);
      await s.click(menuBtn(s, "docker-lab"), { wait: 400 });
      await frame(s);
      await s.shot(1, [menuBtn(s, "docker-lab"), menuItem(s, "Delete", "刪除")], CS);
      await s.click(menuItem(s, "Delete", "刪除"), { wait: 500 });
      const dlg = s.page.getByRole("alertdialog");
      await frame(s);
      await s.shot(2, dlg.getByRole("button", { name: s.t("Delete", "刪除"), exact: true }));
      await s.click(dlg.getByRole("button", { name: s.t("Delete", "刪除"), exact: true }), { wait: 600 });
      await park(s);
      await frame(s);
      await s.shot(3, toast(s), await downTo(s, row(s, "rag-chatbot")));
    },
    // s08 Convert a machine to a template
    async (s) => {
      await start(s);
      await s.click(menuBtn(s, "rag-chatbot"), { wait: 400 });
      await frame(s);
      await s.shot(1, [menuBtn(s, "rag-chatbot"), menuItem(s, "Convert to template", "轉成範本")], CS);
      await s.click(menuItem(s, "Convert to template", "轉成範本"), { wait: 600 });
      const dlg = s.page.getByRole("dialog");
      await s.fill(dlg.locator("#tpl-desc"), s.t("Ubuntu 24.04 with Ollama, LangChain and a sample RAG chatbot. For the Generative AI course.", "Ubuntu 24.04，內含 Ollama、LangChain 與 RAG 聊天機器人範例，供生成式 AI 課程使用。"));
      await frame(s);
      await s.shot(2, [dlg.locator("#tpl-source"), dlg.locator("#tpl-name")]);
      await s.click(dlg.getByRole("button", { name: s.t("Start Conversion", "開始轉換") }), { wait: 500 });
      await s.click(s.page.getByRole("alertdialog").getByRole("button", { name: s.t("Shut Down and Convert", "關機並轉換") }), { wait: 1500 });
      await frame(s);
      await s.shot(3, s.page.locator("tbody tr", { hasText: "rag-chatbot" }).first().locator('[data-guide="templates-status"]'), band(420));
    },
    // s09 Open a machine's detail page
    async (s) => {
      await start(s);
      await frame(s);
      await s.shot(1, cell(s, "ubuntu-desktop", 3), band(400));
      await s.click(cell(s, "ubuntu-desktop", 3), { wait: 1500 });
      await frame(s);
      const hero = await downTo(s, s.page.locator("section").first());
      await s.shot(2, s.page.locator('[data-guide="resource-detail-tabs"] [role="group"]'), hero);
      await s.shot(3, s.page.getByRole("button", { name: s.t("Back to list", "返回列表") }).first(), hero);
    },
    // s10 Create a machine
    {
      // Create Resource opens the request form directly; the form loads the OS lists and the availability calendar.
      mocks(m) {
        m.use([
          ["GET", /^\/api\/v1\/lxc\/templates$/, () => [
            { volid: "local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst" },
            { volid: "local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst" },
          ]],
          ["GET", /^\/api\/v1\/vm\/templates$/, () => [
            { vmid: 9004, name: "ubuntu-24.04-server", node: "pve-02", is_windows: false, cores: 2, memory_mb: 2048, disk_gb: 20 },
          ]],
          ["POST", /^\/api\/v1\/vm-requests\/availability$/, () => ({ days: [], summary: "", timezone: "Asia/Taipei" })],
        ]);
      },
      run: async (s) => {
        await start(s);
        await frame(s);
        const create = s.page.getByRole("button", { name: s.t("Create Resource", "建立資源") });
        await s.shot(1, create, band(300));
        await s.click(create, { wait: 1500 });
        await frame(s);
        const form = s.page.getByRole("heading", { level: 1, name: s.t("Request VM / Container", "申請虛擬機 / 容器") });
        await s.shot(2, form, { ...band(620), newFrame: true });
      },
    },
  ],
};
