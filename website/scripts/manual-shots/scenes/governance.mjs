// Manual page: manual/governance.md (Resource Policies, /governance, admin)
import { around, center, useGovernance } from "../mocks/system.mjs";

const card = (s, en, zh) => s.page.locator("form > div").filter({ has: s.page.getByRole("heading", { name: s.t(en, zh), exact: true }) });
// A toggle row or number field inside a card, by its label text.
const item = (c, s, en, zh) => c.locator("label").filter({ has: s.page.getByText(s.t(en, zh), { exact: true }) }).first();
const saveBar = (s) => s.page.locator('[data-guide="governance-save"]');
const saveBtn = (s) => saveBar(s).getByRole("button", { name: s.t("Save Governance Settings", "儲存治理設定") });
const globalCard = (s) => s.page.locator('[data-guide="governance-quota-global"]');
const overrides = (s) => s.page.locator('[data-guide="governance-quota-overrides"]');
const dialog = (s) => s.page.getByRole("dialog");
const confirmBox = (s) => s.page.getByRole("alertdialog");
const wide = (r) => ({ ...r, x: 340, width: 1100 });

async function setNum(c, s, en, zh, v) {
  const f = item(c, s, en, zh);
  await s.fill(f.locator("input"), String(v));
  await f.locator("input").evaluate((el) => el.blur()); // no focus ring or spinner in the picture
  return f;
}

// The floating AI button sits over the bottom-right corner; at the end of the page it
// lands inside these frames, cut off at their edge. It has no part in these steps.
const hideFab = (s) => s.page.addStyleTag({ content: '[class*="_fab_"]{visibility:hidden !important}' });

// One governance card: run `edit` (which marks the card steps), then mark the save button.
function govSection(config, edit, saveStep) {
  return {
    mocks(m) { useGovernance(m, { config }); },
    run: async (s) => {
      await s.go("/governance");
      await edit(s);
      await center(saveBar(s));
      await s.settle(200);
      await s.shot(saveStep, saveBtn(s), { crop: wide(await around([saveBar(s)], 14)), scroll: false });
    },
  };
}

// Marks steps on a card that has been scrolled to the middle of the screen.
async function onCard(s, c, marks) {
  await center(c);
  await s.settle(200);
  for (const [n, target] of marks) await s.shot(n, target, { crop: c, pad: 14, scroll: false });
}

export default {
  page: "governance",
  role: "admin",
  mocks(m) {
    useGovernance(m);
  },
  sections: [
    // s01 Configure resource alerts
    govSection({ alerts_enabled: false, alert_email_enabled: false }, async (s) => {
      const c = card(s, "Resource Alerts", "資源警告");
      await s.click(item(c, s, "Enable Alerts", "啟用警告").locator("input"));
      const cpu = await setNum(c, s, "CPU Threshold (%)", "CPU 閾值（%）", 85);
      const mem = item(c, s, "Memory Threshold (%)", "記憶體閾值（%）");
      const disk = await setNum(c, s, "Disk Threshold (%)", "磁碟閾值（%）", 80);
      const cool = await setNum(c, s, "Cooldown (minutes)", "冷卻期（分鐘）", 60);
      const intv = item(c, s, "Check Interval (seconds)", "檢查間隔（秒）");
      await s.click(item(c, s, "Email Notifications", "Email 通知").locator("input"));
      await onCard(s, c, [
        [1, item(c, s, "Enable Alerts", "啟用警告")],
        [2, [cpu, mem, disk]],
        [3, [cool, intv]],
        [4, item(c, s, "Email Notifications", "Email 通知")],
      ]);
    }, 5),
    // s02 Configure expiry reclamation (TTL lifecycle)
    govSection({ ttl_enabled: false }, async (s) => {
      const c = card(s, "TTL Lifecycle", "TTL 生命週期");
      await s.click(item(c, s, "Enable TTL Reclamation", "啟用 TTL 回收").locator("input"));
      const warn = await setNum(c, s, "Warn Before Expiry (days)", "到期前通知（天）", 5);
      const grace = await setNum(c, s, "Deletion Grace Period (days)", "刪除寬限期（天）", 14);
      await onCard(s, c, [[1, item(c, s, "Enable TTL Reclamation", "啟用 TTL 回收")], [2, [warn, grace]]]);
    }, 3),
    // s03 Shut down idle machines automatically
    govSection({ idle_detection_enabled: false }, async (s) => {
      const c = card(s, "Idle Detection", "閒置偵測");
      await s.click(item(c, s, "Enable Idle Detection", "啟用閒置偵測").locator("input"));
      await setNum(c, s, "Observation Window (hours)", "觀察視窗（小時）", 72);
      await onCard(s, c, [[1, item(c, s, "Enable Idle Detection", "啟用閒置偵測")], [2, c.locator("> div").last()]]);
    }, 3),
    // s04 Turn VM / LXC auto-detection on or off
    govSection({}, async (s) => {
      const c = card(s, "VM / LXC Auto-Detection", "VM / LXC 自動判斷");
      await s.click(item(c, s, "Enable Auto-Detection", "啟用自動判斷").locator("input"));
      await onCard(s, c, [[1, item(c, s, "Enable Auto-Detection", "啟用自動判斷")]]);
    }, 2),
    // s05 Configure anti-mining detection
    govSection({ mining_detection_enabled: false }, async (s) => {
      const c = card(s, "Anti-Mining Detection", "反挖礦偵測");
      await s.click(item(c, s, "Enable Mining Detection", "啟用挖礦偵測").locator("input"));
      const win = await setNum(c, s, "Observation Window (hours)", "觀察視窗（小時）", 4);
      await onCard(s, c, [
        [1, item(c, s, "Enable Mining Detection", "啟用挖礦偵測")],
        [2, [item(c, s, "CPU Threshold (%)", "CPU 閾值（%）"), win, item(c, s, "Resources Scanned per Round", "每輪掃描台數")]],
        [3, item(c, s, "Auto Snapshot & Suspend", "自動存證並暫停")],
      ]);
    }, 4),
    // s06 Configure snapshot governance
    govSection({ snapshot_cleanup_enabled: false }, async (s) => {
      const c = card(s, "Snapshot Governance", "快照治理");
      await s.click(item(c, s, "Enable Automatic Snapshot Cleanup", "啟用快照自動清理").locator("input"));
      const ret = await setNum(c, s, "Retention Days", "保留天數", 14);
      await onCard(s, c, [
        [1, item(c, s, "Enable Automatic Snapshot Cleanup", "啟用快照自動清理")],
        [2, [ret, item(c, s, "Max Student Snapshots", "學生快照上限"), item(c, s, "Max Student Backups", "學生備份上限")]],
      ]);
    }, 3),
    // s07 Limit clone concurrency (the card sits right above the save bar)
    {
      run: async (s) => {
        await s.go("/governance");
        const c = card(s, "Clone Concurrency", "克隆併發");
        const f = await setNum(c, s, "Concurrency Limit", "併發上限", 6);
        await center(c);
        await s.settle(200);
        const crop = wide(await around([c, saveBar(s)], 16));
        await s.shot(1, f, { crop, scroll: false });
        await s.shot(2, saveBtn(s), { crop, scroll: false });
      },
    },
    // s08 Discard unsaved governance changes
    {
      run: async (s) => {
        await s.go("/governance");
        const c = card(s, "Clone Concurrency", "克隆併發");
        await setNum(c, s, "Concurrency Limit", "併發上限", 12);
        await center(c);
        await s.settle(200);
        const crop = wide(await around([c, saveBar(s)], 16));
        await s.shot(1, saveBar(s).getByRole("button", { name: s.t("Restore", "還原") }), { crop, scroll: false });
        await s.click(saveBar(s).getByRole("button", { name: s.t("Restore", "還原") }));
        await s.shot(2, [item(c, s, "Concurrency Limit", "併發上限"), saveBtn(s)], { crop: wide(await around([c, saveBar(s)], 16)), scroll: false, newFrame: true });
      },
    },
    // s09 Change the global default quota
    async (s) => {
      await s.go("/governance");
      const g = globalCard(s);
      await center(g);
      await s.fill(s.page.locator("#global-max_cpu_cores"), "12");
      await s.fill(s.page.locator("#global-max_memory_mb"), "24576");
      await s.click(s.page.locator("#global-max_instances").locator("xpath=..").locator("label").last().locator("input"));
      const fieldBox = (id) => s.page.locator(`#global-${id}`).locator("xpath=..");
      const crop = wide(await around([g], 16));
      await s.shot(1, ["max_cpu_cores", "max_memory_mb", "max_disk_gb", "max_instances"].map((id) => s.page.locator(`#global-${id}`)), { crop, scroll: false });
      await s.shot(2, fieldBox("max_instances").locator("label").last(), { crop, scroll: false });
      await s.shot(3, [g.getByRole("button", { name: s.t("Save", "儲存"), exact: true }), g.getByRole("button", { name: s.t("Restore", "還原") })], { crop, scroll: false });
    },
    // s10 Give a user their own quota
    async (s) => {
      await s.go("/governance");
      await hideFab(s);
      const o = overrides(s);
      await center(o);
      await s.settle(200);
      const add = o.getByRole("button", { name: s.t("Add Quota", "新增配額") });
      await s.shot(1, add, { crop: wide(await around([o], 16)), scroll: false });
      await s.click(add, { wait: 600 });
      const user = s.page.locator("#quota-user");
      await user.click();
      await user.pressSequentially(s.t("Grace", "李佳"), { delay: 30 });
      await s.settle(300);
      const option = dialog(s).getByRole("button", { name: /11256021@ntub\.edu\.tw/ });
      await s.shot(2, [user, option]);
      await s.click(option);
      await s.fill(s.page.locator("#quota-max_cpu_cores"), "12");
      await s.fill(s.page.locator("#quota-max_memory_mb"), "24576");
      await s.page.locator("#quota-max_memory_mb").evaluate((el) => el.blur());
      await s.shot(3,["max_cpu_cores", "max_memory_mb", "max_disk_gb", "max_instances"].map((id) => s.page.locator(`#quota-${id}`)), { newFrame: true });
      await s.shot(4, dialog(s).getByRole("button", { name: s.t("Create", "建立"), exact: true }));
    },
    // s11 Edit or delete a user's quota
    async (s) => {
      await s.go("/governance");
      await hideFab(s);
      const o = overrides(s);
      await center(o);
      await s.settle(200);
      const r = o.locator("tr", { hasText: "11156012@ntub.edu.tw" });
      const edit = r.getByRole("button", { name: s.t("Edit Quota", "編輯配額") });
      const del = r.getByRole("button", { name: s.t("Delete Quota", "刪除配額") });
      const crop = wide(await around([o], 16));
      await s.shot(1, edit, { crop, scroll: false });
      await s.shot(2, del, { crop, scroll: false });
      await s.click(edit, { wait: 600 });
      await s.fill(s.page.locator("#quota-max_cpu_cores"), "20");
      await s.shot(1, [s.page.locator("#quota-max_cpu_cores"), dialog(s).getByRole("button", { name: s.t("Save", "儲存"), exact: true })]);
      await s.click(dialog(s).getByRole("button", { name: s.t("Save", "儲存"), exact: true }), { wait: 600 });
      await s.page.evaluate(() => document.querySelectorAll("[data-sonner-toast]").forEach((t) => t.remove()));
      await s.click(del, { wait: 500 });
      await s.shot(2, confirmBox(s).getByRole("button", { name: s.t("Delete", "刪除"), exact: true }));
    },
  ],
};
