// Manual page: manual/resource-detail.md
import { D, H, MIN, NOW, ago, iso, later } from "../lib/mock.mjs";
import { BACKUP_VM, NEW_PUB, OWN_PUB, VM, registerResources } from "../mocks/resources.mjs";

const tab = (s, key) => s.page.locator(`[data-guide-tab="resource-${key}"]`);
const confirmBtn = (s) => s.page.getByRole("alertdialog").getByRole("button").last();
const dialog = (s) => s.page.getByRole("dialog");
const section = (s, title) => s.page.locator("section").filter({ has: s.page.getByRole("heading", { name: title }) });
const toolbar = (s) => s.page.locator('[data-guide="resource-detail-tabs"] > div').last();
const pageBox = (s) => s.page.locator('[data-guide="resource-detail-tabs"]').locator("..");
const P = (s) => ({ scroll: false, crop: pageBox(s), pad: 12 });
const C = (card) => ({ scroll: false, crop: card, pad: 12 });
// Page title down to the first table.
const T = async (s) => ({ scroll: false, crop: await pageTo(s, s.page.locator("table").first()) });
const btn = (scope, name) => scope.getByRole("button", { name, exact: true });
const toast = (s) => s.page.locator("[data-sonner-toast]").first().waitFor({ state: "detached", timeout: 8000 }).catch(() => {});

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
// Crop from the page title down to the bottom of `loc` (no empty page below a short table).
const pageTo = (s, loc) => area([pageBox(s).locator("> *").first(), loc]).then(async (r) => {
  const p = await pageBox(s).boundingBox();
  return { ...r, x: p.x - 12, width: p.width + 24 };
});
// Target whose outline sits dy px inside the element, for rows that touch each other.
const inset = (loc, dy = 8) => ({
  scrollIntoViewIfNeeded: () => loc.scrollIntoViewIfNeeded(),
  evaluateHandle: (fn) => loc.evaluateHandle(fn),
  boundingBox: async () => {
    const b = await loc.boundingBox();
    return b && { x: b.x, y: b.y + dy, width: b.width, height: b.height - 2 * dy };
  },
  toString: () => String(loc),
});

async function open(s, vmid = VM, tabKey = null) {
  await s.go(`/my-resources/${vmid}`, { wait: 700 });
  await s.page.locator("h2").first().waitFor();
  if (tabKey) await s.click(tab(s, tabKey), { wait: 700 });
}

const specRequest = (o) => ({
  id: "scr-9001", vmid: VM, change_type: "combined", status: "approved", apply_status: "ready", apply_error: null,
  current_cpu: 4, requested_cpu: 6, current_memory: 8192, requested_memory: 12288, current_disk: 32, requested_disk: null,
  reason: "", created_at: ago(D), ...o,
});

export default {
  page: "resource-detail",
  role: "student",
  mocks(m) {
    registerResources(m);
  },
  sections: [
    // s01 Move between tabs
    async (s) => {
      await open(s, VM, "snapshots");
      const crop = { scroll: false, crop: await pageTo(s, s.page.locator("table").first()) };
      await s.shot(1, s.page.locator('[data-guide="resource-detail-tabs"] [role="group"]'), crop);
      await s.shot(2, toolbar(s), crop);
      await s.shot(3, btn(s.page, s.t("Back to list", "返回列表")), crop);
    },
    // s02 Read the Overview
    async (s) => {
      await open(s);
      const hero = s.page.locator("section").first();
      const manual = section(s, s.t("User Manual", "使用手冊"));
      const top = { scroll: false, crop: await pageTo(s, manual) };
      await s.shot(1, [hero.locator("h2"), hero.locator("h2").locator("xpath=following-sibling::div[1]"), hero.locator("xpath=div[1]/div[2]")], top);
      await s.shot(2, hero.locator("xpath=div[2]"), top);
      await s.shot(3, hero.locator("xpath=following-sibling::div[1]"), top);
      await s.shot(4, manual, top);
      const env = section(s, s.t("Environment Information", "環境資訊"));
      const conn = section(s, s.t("Connection & Credentials", "連線與憑證"));
      await env.scrollIntoViewIfNeeded();
      await s.page.mouse.wheel(0, 200);
      await s.page.waitForTimeout(300);
      const cards = { scroll: false, crop: await area([env, conn]) };
      await s.shot(5, env, cards);
      await s.shot(6, conn, cards);
    },
    // s03 Find the login username and password
    async (s) => {
      await open(s);
      const card = section(s, s.t("Connection & Credentials", "連線與憑證"));
      await card.evaluate((el) => window.scrollBy(0, el.getBoundingClientRect().top - 60));
      await s.page.waitForTimeout(500);
      const label = (t) => card.getByText(t, { exact: true });
      await s.shot(1, inset(label(s.t("Username", "帳號")).locator("..")), C(card));
      await s.shot(2, inset(label(s.t("Password", "密碼")).locator("../..")), C(card));
      await s.shot(3, [inset(label(s.t("Public Key", "公鑰")).locator("../..")), inset(label(s.t("Private Key", "私鑰")).locator("../.."))], C(card));
    },
    // s04 Watch usage on the Monitoring tab
    async (s) => {
      await open(s, VM, "monitoring");
      await s.page.getByText(s.t("Historical Data", "歷史資料")).waitFor();
      const chartTabs = s.page.getByRole("group", { name: s.t("Trend chart metric", "趨勢圖指標切換") });
      await s.page.evaluate(() => window.scrollTo(0, 0));
      await s.page.waitForTimeout(300);
      await s.shot(1, s.page.getByRole("group", { name: s.t("Trend timeframe", "趨勢時間範圍") }), P(s));
      await s.shot(2, s.page.getByText(s.t("CPU Usage", "CPU 用量")).locator("xpath=ancestor::div[4]"), P(s));
      await s.shot(3, chartTabs, P(s));
    },
    // s05 Request a spec change
    async (s) => {
      await open(s, VM, "specifications");
      const field = (id) => s.page.locator(`#${id}`).locator("xpath=ancestor::div[2]");
      const num = (id) => field(id).locator('input:not([type="range"])').first();
      await num("spec-cores").fill("6");
      await s.press("Tab");
      await num("spec-memory").fill("12");
      await s.press("Tab");
      await s.fill(s.page.locator("#spec-reason"), s.t(
        "Week 6 lab compiles the Linux kernel; 4 cores and 8 GB take over 40 minutes per build.",
        "第 6 週實驗要編譯 Linux 核心，目前 4 核 8 GB 每次編譯超過 40 分鐘。",
      ));
      const delta = (id) => field(id).locator("span", { hasText: s.t("vs. current", "較目前") });
      await s.page.evaluate(() => window.scrollTo(0, 0));
      await s.page.waitForTimeout(300);
      const submit = btn(s.page, s.t("Submit Request", "送出申請"));
      const form = { scroll: false, crop: await pageTo(s, submit) };
      await s.shot(1, s.page.locator("#spec-cores").locator("xpath=ancestor::div[3]"), form);
      await s.shot(2, [delta("spec-cores"), delta("spec-memory")], form);
      await s.shot(3, s.page.locator("#spec-reason"), form);
      await s.shot(4, submit, form);
      await s.click(submit, { wait: 600 });
      await s.page.evaluate(() => window.scrollTo(0, 0));
      const notice = s.page.getByText(s.t("Spec change request · Pending Review", "規格調整申請 · 審核中")).locator("..");
      await notice.waitFor();
      await toast(s);
      const bar = s.page.locator('[data-guide="resource-detail-tabs"]');
      const pending = await area([bar, notice]);
      await s.shot(4, notice, { scroll: false, crop: pending, newFrame: true });
      await s.shot(5, notice.getByText(s.t("waiting for administrator review", "等待管理員審核")), { scroll: false, crop: pending });
      // Approved: apply it.
      s.m.state.specRequests = [specRequest({})];
      await open(s, VM, "specifications");
      const ready = s.page.getByText(s.t("Spec change request · Approved · Ready to Apply", "規格調整申請 · 已核准 · 待套用")).locator("..");
      await ready.waitFor();
      const apply = btn(s.page, s.t("Apply New Specs", "套用新規格"));
      await s.shot(6, [ready, apply], { scroll: false, crop: await area([bar, ready]), newFrame: true });
      await s.click(apply, { wait: 400 });
      await s.shot(6, confirmBtn(s));
      await s.click(confirmBtn(s), { wait: 600 });
      const applying = s.page.getByText(s.t("Spec change request · Applying", "規格調整申請 · 套用中")).locator("..");
      await applying.waitFor();
      await toast(s);
      await s.page.waitForTimeout(400);
      await s.shot(7, applying, { scroll: false, crop: await area([bar, applying]) });
    },
    // s06 Take and restore snapshots
    async (s) => {
      await open(s, VM, "snapshots");
      await s.click(btn(toolbar(s), s.t("Create Snapshot", "建立快照")), { wait: 400 });
      await s.fill(dialog(s).locator("#snap-name"), "before-kernel-build");
      await s.fill(dialog(s).locator("#snap-desc"), s.t("Before building the 6.11 kernel for week 6", "第 6 週編譯 6.11 核心前"));
      await s.shot(1, [dialog(s).locator("#snap-name"), btn(dialog(s), s.t("Create", "建立"))]);
      await s.click(btn(dialog(s), s.t("Cancel", "取消")), { wait: 400 });
      const row = s.page.locator("tbody tr", { hasText: "week5-done" });
      const rows = await T(s);
      await s.shot(2, btn(row, s.t("Restore", "還原")), rows);
      await s.shot(3, btn(row, s.t("Delete", "刪除")), rows);
      await s.click(btn(row, s.t("Delete", "刪除")), { wait: 400 });
      await s.shot(3, confirmBtn(s));
    },
    // s07 Reset the machine to its initial state
    async (s) => {
      await open(s, VM, "snapshots");
      await s.shot(1, btn(toolbar(s), s.t("One-Click Reset", "一鍵重置")), await T(s));
      await s.click(btn(toolbar(s), s.t("One-Click Reset", "一鍵重置")), { wait: 400 });
      await s.shot(2, confirmBtn(s));
    },
    // s08 Back up and restore (when snapshots are not available)
    async (s) => {
      await open(s, BACKUP_VM, "backups");
      await s.click(btn(toolbar(s), s.t("Create Backup", "建立備份")), { wait: 400 });
      await s.fill(dialog(s).locator("#backup-desc"), s.t("Before upgrading pandas to 2.2", "升級 pandas 2.2 前"));
      await s.shot(1, [dialog(s).locator("#backup-desc"), btn(dialog(s), s.t("Create", "建立"))]);
      await s.click(btn(dialog(s), s.t("Cancel", "取消")), { wait: 400 });
      const row = s.page.locator("tbody tr").first();
      const rows = await T(s);
      await s.shot(2, btn(row, s.t("Restore", "還原")), rows);
      await s.shot(3, btn(row, s.t("Delete", "刪除")), rows);
      await s.click(btn(row, s.t("Delete", "刪除")), { wait: 400 });
      await s.shot(3, confirmBtn(s));
    },
    // s09 Review the Audit Log
    async (s) => {
      await open(s, VM, "auditLogs");
      await s.page.locator("tbody tr").first().waitFor();
      const crop = await T(s);
      await s.shot(1, tab(s, "auditLogs"), crop);
      await s.shot(2, s.page.locator("table").first(), crop);
    },
    // s10 Request an expiry extension
    {
      mocks(m) {
        m.state.patch = { [VM]: { auto_stop_at: later(55 * MIN), auto_stop_reason: "idle" } };
      },
      run: async (s) => {
        await open(s, VM, "advanced");
        const card = s.page.locator('[data-guide="resource-setting-lifecycle"]');
        await card.locator("h2").waitFor();
        const req = btn(card, s.t("Request extension", "申請延長到期日"));
        await s.shot(1, card.locator("xpath=div/div[2]"), C(card));
        await s.shot(2, req, C(card));
        await s.click(req, { wait: 400 });
        await s.fill(dialog(s).locator("#ext-date"), "2027-02-28");
        await s.fill(dialog(s).locator("#ext-reason"), s.t(
          "My capstone project demo moved to February; I still need this VM to run the OS lab server.",
          "專題成果展延到明年二月，展示前仍需要這台虛擬機執行作業系統實驗伺服器。",
        ));
        await s.shot(3, [dialog(s).locator("#ext-date"), dialog(s).locator("#ext-reason")]);
        await s.shot(4, btn(dialog(s), s.t("Submit request", "送出申請")));
        await s.click(btn(dialog(s), s.t("Submit request", "送出申請")), { wait: 600 });
        const note = card.getByText(s.t("Extension request under review", "延期申請審核中")).locator("..");
        await note.waitFor();
        await toast(s);
        await s.shot(4, note, C(card));
        // Approved by an administrator: the new date is in place.
        s.m.state.specRequests = [];
        s.m.state.patch = { [VM]: { ...s.m.state.patch[VM], expiry_date: "2027-02-28", window_end_at: "2027-02-28T15:00:00Z" } };
        await open(s, VM, "advanced");
        await card.locator("h2").waitFor();
        await s.shot(5, card.getByText(s.t("Expiry date", "到期日"), { exact: true }).locator(".."), C(card));
      },
    },
    // s11 Manage the machine's firewall
    async (s) => {
      await open(s, VM, "advanced");
      const card = s.page.locator('[data-guide="resource-setting-firewall"]');
      await card.locator("table").waitFor();
      await card.scrollIntoViewIfNeeded();
      await s.page.evaluate(() => {
        const el = document.querySelector('[data-guide="resource-setting-firewall"]');
        window.scrollBy(0, el.getBoundingClientRect().top - 16);
      });
      await s.page.waitForTimeout(500);
      const head = card.locator("h2").locator("xpath=../..");
      await s.shot(1, [head.locator("span", { hasText: s.t("Firewall on", "防火牆已啟用") }), head.locator("span", { hasText: s.t("Default:", "預設：") }), card.locator(".react-flow").first()], C(card));
      await s.shot(2, card.locator("thead"), C(card));
      const add = btn(card, s.t("Add rule", "新增規則"));
      await s.shot(3, add, C(card));
      const own = card.locator("tbody tr", { hasText: s.t("SSH from campus", "校內 SSH") });
      await s.shot(4, [own.getByRole("switch"), own.getByRole("button")], C(card));
      await s.click(add, { wait: 800 });
      // The dialog opens on "Write a rule myself"; "Change connection type" shows the four choices.
      await s.click(dialog(s).locator('[data-guide="connection-dialog-endpoints"]'), { wait: 500 });
      await s.shot(3, dialog(s).locator('[data-guide="connection-dialog-endpoints"]'));
    },
    // s12 Change boot options (VMs only)
    async (s) => {
      await open(s, VM, "advanced");
      const card = s.page.locator('[data-guide="resource-setting-boot"]');
      await card.locator("select").waitFor();
      await s.page.evaluate(() => {
        const el = document.querySelector('[data-guide="resource-setting-boot"]');
        window.scrollBy(0, el.getBoundingClientRect().top - 16);
      });
      await s.page.waitForTimeout(300);
      const items = card.locator("code").first().locator("xpath=ancestor::div[2]");
      // Move the CD-ROM above the disk, as when booting an installer.
      await s.click(card.getByTitle(s.t("Move up", "往上移")).nth(1), { wait: 300 });
      await card.locator("select").selectOption("iso-nfs:iso/systemrescue-11.02-amd64.iso");
      await s.page.waitForTimeout(300);
      await s.shot(1, items, C(card));
      await s.shot(2, btn(card, s.t("Save boot order", "儲存開機順序")), C(card));
      await s.shot(3, [card.locator("select"), btn(card, s.t("Mount", "掛載"))], C(card));
      await s.click(btn(card, s.t("Mount", "掛載")), { wait: 600 });
      await toast(s);
      const eject = btn(card, s.t("Eject", "退出"));
      const iso = await area([card.getByText(s.t("ISO image", "ISO 光碟"), { exact: true }), eject]);
      const cb = await card.boundingBox();
      await s.shot(4, eject, { scroll: false, newFrame: true, crop: { ...iso, x: cb.x - 12, width: cb.width + 24 } });
      await s.click(eject, { wait: 400 });
      await s.shot(4, confirmBtn(s));
    },
    // s13 Reset the login password
    async (s) => {
      await open(s, VM, "advanced");
      const card = s.page.locator('[data-guide="resource-setting-credentials"]');
      await card.locator("textarea").waitFor();
      await s.page.evaluate(() => {
        const el = document.querySelector('[data-guide="resource-setting-credentials"]');
        window.scrollBy(0, el.getBoundingClientRect().top - 16);
      });
      await s.page.waitForTimeout(300);
      const reset = btn(card, s.t("Reset password", "重設密碼"));
      await s.shot(1, reset, C(card));
      await s.click(reset, { wait: 400 });
      await dialog(s).locator('input[type="checkbox"]').check();
      await s.fill(dialog(s).locator("#cred-pw"), "OsLab-2026!wk6");
      await s.shot(2, [dialog(s).locator("label", { has: s.page.locator('input[type="checkbox"]') }), dialog(s).locator("#cred-pw")]);
      await s.shot(3, [dialog(s).locator("p").filter({ hasText: s.t("reboot", "重新開機") }), dialog(s).locator('button[type="submit"]')]);
      await s.click(dialog(s).locator('button[type="submit"]'), { wait: 800 });
      const secret = card.getByText(s.t("New password (shown only once)", "新密碼（只顯示這一次）")).locator("..");
      await secret.waitFor();
      await s.page.locator("[data-sonner-toast]").first().waitFor({ state: "detached", timeout: 8000 }).catch(() => {});
      await s.shot(4, [secret, btn(secret, s.t("Copy", "複製"))], C(card));
    },
    // s14 Manage SSH keys
    async (s) => {
      await open(s, VM, "advanced");
      const card = s.page.locator('[data-guide="resource-setting-credentials"]');
      await card.locator("textarea").waitFor();
      await s.click(btn(card, s.t("Regenerate key", "重新產生金鑰")), { wait: 400 });
      await confirmBtn(s).click();
      const secret = card.getByText(s.t("New private key (shown only once; download and keep it)", "新的私鑰（只顯示這一次，請下載保存）")).locator("..");
      await secret.waitFor();
      await s.page.locator("[data-sonner-toast]").first().waitFor({ state: "detached", timeout: 8000 }).catch(() => {});
      await s.fill(card.locator("textarea"), NEW_PUB);
      await s.page.evaluate(() => {
        const el = document.querySelector('[data-guide="resource-setting-credentials"]');
        window.scrollBy(0, el.getBoundingClientRect().top - 16);
      });
      await s.page.waitForTimeout(300);
      await s.shot(1, [btn(card, s.t("Regenerate key", "重新產生金鑰")), btn(secret, s.t("Download", "下載"))], C(card));
      await s.shot(2, [card.locator("textarea"), btn(card, s.t("Add key", "加入公鑰"))], C(card));
      const ownKey = card.getByTitle(OWN_PUB).locator("..");
      await s.shot(3, [ownKey.getByRole("button"), card.getByText(s.t("Platform key", "平台金鑰"), { exact: true })], C(card));
      await s.click(ownKey.getByRole("button"), { wait: 400 });
      await s.shot(3, confirmBtn(s));
    },
    // s15 Share the machine or transfer ownership
    async (s) => {
      await open(s, VM, "advanced");
      const card = s.page.locator('[data-guide="resource-setting-sharing"]');
      await card.locator('input[type="email"]').waitFor();
      await s.page.evaluate(() => {
        const el = document.querySelector('[data-guide="resource-setting-sharing"]');
        window.scrollBy(0, el.getBoundingClientRect().top - 16);
      });
      await s.page.waitForTimeout(300);
      await s.fill(card.locator('input[type="email"]'), "11256021@ntub.edu.tw");
      await s.shot(1, [card.locator('input[type="email"]'), btn(card, s.t("Share", "共享"))], C(card));
      await s.shot(2, card.getByTitle(s.t("Revoke", "取消共享")).first(), C(card));
      await s.shot(3, btn(card, s.t("Transfer", "轉移")), C(card));
      await s.click(card.getByTitle(s.t("Revoke", "取消共享")).first(), { wait: 400 });
      await s.shot(2, confirmBtn(s));
      await s.click(s.page.getByRole("alertdialog").getByRole("button").first(), { wait: 500 });
      await s.click(btn(card, s.t("Transfer", "轉移")), { wait: 400 });
      await s.fill(dialog(s).locator("#xfer-email"), "11156012@ntub.edu.tw");
      await s.fill(dialog(s).locator("#xfer-confirm"), "ubuntu-desktop");
      await s.shot(3, [dialog(s).locator("#xfer-email"), dialog(s).locator('input[type="checkbox"]').locator(".."), dialog(s).locator("#xfer-confirm"), dialog(s).locator('button[type="submit"]')]);
    },
  ],
};
