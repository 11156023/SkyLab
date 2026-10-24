// Manual page: manual/admin-users.md (User Management, /admin, admin)
import { around, center, content, useAccounts, useUserGroups } from "../mocks/system.mjs";

const row = (s, email) => s.page.locator('[data-guide="admin-user-row"]').filter({ hasText: email });
const editBtn = (s, email) => row(s, email).locator('[data-guide="admin-user-edit"]');
const deleteBtn = (s, email) => row(s, email).getByRole("button", { name: s.t("Delete", "刪除") });
const dialog = (s) => s.page.getByRole("dialog");
const dlgField = (s, en, zh) => dialog(s).locator("label").filter({ has: s.page.getByText(s.t(en, zh), { exact: true }) }).first();
const regPanel = (s) => s.page.locator('[data-guide="admin-registration"]');
const pendPanel = (s) => s.page.locator('[data-guide="admin-pending"]');
const toast = (s) => s.page.locator("[data-sonner-toast]").first();
const confirmBox = (s) => s.page.getByRole("alertdialog");
const saveBtn = (s) => dialog(s).getByRole("button", { name: s.t("Save", "儲存"), exact: true });
const totpBox = (s) => dialog(s).getByRole("checkbox", { name: s.t("Require two-factor authentication", "強制兩步驟驗證") });
const activeBox = (s) => dialog(s).getByRole("checkbox", { name: s.t("Enable account", "啟用帳戶") });
const checkRow = (box) => box.locator("xpath=..");
const groupsBtn = (s) => s.page.getByRole("button", { name: s.t("Groups", "群組管理"), exact: true }).first();
// A row of the Groups dialog; its buttons are, in order, import CSV, rename and delete.
const groupRow = (s, en, zh) => dialog(s).locator("li").filter({ hasText: s.t(en, zh) }).first();

// A user row with a couple of neighbours, across the content column.
async function rowCrop(s, email) {
  const b = await row(s, email).boundingBox();
  return content(b.y - 100, b.height + 200);
}

// Marks the pencil of a user (own frame), then opens the edit dialog.
async function openEdit(s, email, step = 1) {
  await center(row(s, email));
  await s.shot(step, editBtn(s, email), { crop: await rowCrop(s, email), scroll: false });
  await s.click(editBtn(s, email), { wait: 600 });
}

// Clicks a registration switch and marks it together with the "saved" toast.
// The crop runs from the top (where the toast shows) to the bottom of the settings panel.
async function toggleRegistration(s, index) {
  await s.go("/admin");
  const opt = regPanel(s).locator("label").nth(index);
  await opt.locator("input").click();
  await toast(s).waitFor();
  await s.page.waitForTimeout(400);
  const panel = await regPanel(s).boundingBox();
  const crop = content(0, Math.ceil(panel.y + panel.height + 16));
  await s.shot(1, opt, { crop, scroll: false });
  await s.shot(2, toast(s), { crop, scroll: false });
}

export default {
  page: "admin-users",
  role: "admin",
  mocks(m) {
    useAccounts(m);
    useUserGroups(m);
  },
  sections: [
    // s01 Turn self-registration on or off
    async (s) => toggleRegistration(s, 0),
    // s02 Require approval for new accounts
    {
      mocks(m) { useAccounts(m, { policy: { signup_enabled: true, approval_required: false, google_signup_enabled: false, student_email_domain: "school.edu.tw" } }); },
      run: async (s) => toggleRegistration(s, 1),
    },
    // s03 Allow or block account creation on first Google login
    async (s) => toggleRegistration(s, 2),
    // s04 Approve or reject a pending registration
    async (s) => {
      await s.go("/admin");
      const panel = pendPanel(s);
      const first = panel.locator("> div").first();
      const crop = await around([panel], 16);
      await s.shot(1, panel.locator("h2"), { crop, scroll: false });
      await s.shot(2, [first.getByRole("button", { name: s.t("Reject", "拒絕") }), first.getByRole("button", { name: s.t("Approve", "核准") })], { crop, scroll: false });
      for (let i = 0; i < 3; i++) {
        await s.click(panel.getByRole("button", { name: s.t("Approve", "核准") }).first(), { wait: 400 });
      }
      await s.page.evaluate(() => document.querySelectorAll("[data-sonner-toast]").forEach((t) => t.remove()));
      await s.shot(3, panel.locator("p"), { crop: await around([panel], 16), scroll: false, newFrame: true });
    },
    // s05 Add a user
    async (s) => {
      await s.go("/admin");
      const add = s.page.getByRole("button", { name: s.t("Add User", "新增使用者") }).first();
      await s.shot(1, add, { crop: content(0, 96), scroll: false });
      await s.click(add, { wait: 600 });
      await s.fill(dlgField(s, "Email", "Email").locator("input"), "11356041@ntub.edu.tw");
      await s.fill(dlgField(s, "Name", "姓名").locator("input"), s.t("Nina Lu", "呂宜蓁"));
      await s.fill(dlgField(s, "Password", "密碼").locator("input"), "Skylab#2026lab");
      await dlgField(s, "Role", "角色").locator("select").selectOption("teacher");
      await s.click(totpBox(s), { wait: 300 });
      await s.shot(2, [dlgField(s, "Email", "Email"), dlgField(s, "Name", "姓名"), dlgField(s, "Password", "密碼")]);
      await s.shot(3, dlgField(s, "Role", "角色").locator("select"));
      await s.shot(4, checkRow(activeBox(s)));
      await s.shot(5, checkRow(totpBox(s)));
      await s.shot(6, saveBtn(s));
    },
    // s06 Edit a user
    async (s) => {
      await s.go("/admin");
      await openEdit(s, "11256003@ntub.edu.tw");
      await s.fill(dlgField(s, "Name", "姓名").locator("input"), s.t("Emily Chang (TA)", "張雅婷（助教）"));
      await s.fill(dlgField(s, "New Password", "新密碼").locator("input"), "Cloud#Lab2026");
      await s.shot(2, [dlgField(s, "Email", "Email"), dlgField(s, "Name", "姓名"), dlgField(s, "Role", "角色"), dialog(s).locator('[data-guide="admin-user-security"]')]);
      await s.shot(3, dlgField(s, "New Password", "新密碼").locator("input"));
      await s.shot(4, saveBtn(s));
    },
    // s07 Disable or enable an account
    async (s) => {
      await s.go("/admin");
      await openEdit(s, "11256052@ntub.edu.tw");
      await s.click(activeBox(s), { wait: 300 });
      await s.shot(2, checkRow(activeBox(s)));
      await s.shot(3, saveBtn(s));
    },
    // s08 Enable, disable or delete several accounts at once
    {
      mocks(m) { m.state.patchDelay = 2500; },
      run: async (s) => {
        await s.go("/admin");
        const names = [s.t("Jason Lin", "林冠廷"), s.t("Sophia Tsai", "蔡宜蓁"), s.t("Daniel Hsu", "許家豪")];
        const rows = ["11256052@ntub.edu.tw", "11156067@ntub.edu.tw", "11256078@ntub.edu.tw"].map((e) => row(s, e));
        const boxes = names.map((n) => s.page.getByRole("checkbox", { name: s.t(`Select ${n}`, `選取 ${n}`) }));
        for (const b of boxes) await b.click();
        await s.settle(300);
        const bar = s.page.getByRole("region", { name: s.t("3 selected", "已選 3 位") });
        await row(s, "11256045@ntub.edu.tw").evaluate((el) => el.scrollIntoView({ block: "start" }));
        await s.settle(200);
        const enable = bar.getByRole("button", { name: s.t("Enable", "啟用"), exact: true });
        const disable = bar.getByRole("button", { name: s.t("Disable", "停用"), exact: true });
        const remove = bar.getByRole("button", { name: s.t("Delete", "刪除"), exact: true });
        // The selection bar sticks to the top of the window; start the crop at its edge so
        // the row scrolled under it does not peek out above.
        const barBox = await bar.boundingBox();
        const listCrop = async () => {
          const r = await around(rows, 16);
          return content(barBox.y - 6, r.y + r.height - (barBox.y - 6));
        };
        await s.shot(1, [...boxes, bar.getByText(s.t("3 selected", "已選 3 位"))], { crop: await listCrop(), scroll: false });
        await s.shot(2, [enable, disable, remove], { crop: await listCrop(), scroll: false });
        await s.click(disable, { wait: 500 });
        const ok = confirmBox(s).getByRole("button", { name: s.t("Disable", "停用"), exact: true });
        await s.shot(3, [confirmBox(s).locator("p").first(), ok]);
        await ok.click();
        await s.page.waitForTimeout(700);
        const status = s.page.getByRole("status").filter({ hasText: s.t("Disable", "停用") });
        await s.shot(4, status, { crop: content(barBox.y - 6, barBox.height + 26), scroll: false });
        await s.page.waitForTimeout(3000);
      },
    },
    // s09 Create, rename or delete groups
    async (s) => {
      await s.go("/admin");
      await s.shot(1, groupsBtn(s), { crop: content(0, 96), scroll: false });
      await s.click(groupsBtn(s), { wait: 600 });
      const d = dialog(s);
      const name = d.getByRole("textbox", { name: s.t("New group", "新增群組") });
      await s.fill(name, s.t("IM Year 3 Class A", "資管三甲"));
      const add = d.getByRole("button", { name: s.t("Add", "新增"), exact: true });
      await s.shot(2, [name.locator("xpath=.."), add]);
      await s.click(add, { wait: 700 });
      // While it is being renamed the row has no name text, so hold on to it by position.
      const list = d.locator("li");
      const at = await list.evaluateAll((els, label) => els.findIndex((el) => el.textContent.includes(label)), s.t("Course TAs", "課程助教"));
      const tas = list.nth(at);
      await s.click(tas.getByRole("button").nth(1), { wait: 300 });
      await s.fill(tas.locator("input"), s.t("Networking course TAs", "網路管理課程助教"));
      await s.shot(3, tas.locator("form"));
      await tas.locator("input").press("Enter");
      await s.page.waitForTimeout(700);
      const trash = groupRow(s, "IM Year 3 Class A", "資管三甲").getByRole("button").nth(2);
      await s.shot(4, trash);
      await s.click(trash, { wait: 500 });
      await s.shot(4, [confirmBox(s).locator("p").first(), confirmBox(s).getByRole("button", { name: s.t("Delete", "刪除"), exact: true })]);
    },
    // s10 Add group members from a CSV file
    async (s) => {
      await s.go("/admin");
      await s.click(groupsBtn(s), { wait: 600 });
      const d = dialog(s);
      const tas = groupRow(s, "Course TAs", "課程助教");
      await s.shot(1, tas.getByRole("button").first());
      await s.shot(2, d.locator('[class*="_fieldHint_"]'));
      // The upload button remembers which group to fill, then opens the file chooser.
      const [chooser] = await Promise.all([s.page.waitForEvent("filechooser"), tas.getByRole("button").first().click()]);
      await chooser.setFiles({ name: "tas.csv", mimeType: "text/csv", buffer: Buffer.from("學號\n11256045\n11156034\n11256999\n") });
      await toast(s).waitFor();
      await s.page.waitForTimeout(500);
      await s.shot(3, [toast(s), tas.locator("small")]);
    },
    // s11 Add or remove selected accounts from a group
    async (s) => {
      await s.go("/admin");
      const names = [s.t("Hannah Ho", "何思妤"), s.t("Leo Chiang", "江宥廷")];
      const rows = ["11156083@ntub.edu.tw", "11256094@ntub.edu.tw"].map((e) => row(s, e));
      const boxes = names.map((n) => s.page.getByRole("checkbox", { name: s.t(`Select ${n}`, `選取 ${n}`) }));
      for (const b of boxes) await b.click();
      await s.settle(300);
      const bar = s.page.getByRole("region", { name: s.t("2 selected", "已選 2 位") });
      await row(s, "11256078@ntub.edu.tw").evaluate((el) => el.scrollIntoView({ block: "start" }));
      await s.settle(200);
      const addTo = bar.getByRole("button", { name: s.t("Add to group", "加入群組"), exact: true });
      const removeFrom = bar.getByRole("button", { name: s.t("Remove from group", "移出群組"), exact: true });
      // Same crop as the batch section: from the sticky selection bar down to the ticked rows.
      const barBox = await bar.boundingBox();
      const r = await around(rows, 16);
      const crop = content(barBox.y - 6, r.y + r.height - (barBox.y - 6));
      await s.shot(1, [...boxes, bar.getByText(s.t("2 selected", "已選 2 位"))], { crop, scroll: false });
      await s.shot(2, [addTo, removeFrom], { crop, scroll: false });
      await s.click(addTo, { wait: 600 });
      const d = dialog(s);
      await d.locator("select").selectOption({ label: s.t("IM Year 4 Class A", "資管四甲") });
      await s.shot(3, [d.locator("select"), d.getByRole("button", { name: s.t("Add", "加入"), exact: true })]);
    },
    // s12 Require two-factor authentication for an account
    async (s) => {
      await s.go("/admin");
      await openEdit(s, "11256021@ntub.edu.tw");
      await s.click(totpBox(s), { wait: 300 });
      await s.shot(2, checkRow(totpBox(s)));
      await s.shot(3, saveBtn(s));
    },
    // s13 Reset a user's two-factor authentication
    async (s) => {
      await s.go("/admin");
      await openEdit(s, "lin@ntub.edu.tw");
      const reset = dialog(s).getByRole("button", { name: s.t("Reset two-factor authentication", "重設兩步驟驗證") });
      await s.shot(1, reset.locator("xpath=..").locator("div").first());
      await s.shot(2, reset);
      await s.click(reset, { wait: 500 });
      await s.shot(3, confirmBox(s).getByRole("button", { name: s.t("Reset two-factor authentication", "重設兩步驟驗證") }));
    },
    // s14 Delete a user
    async (s) => {
      await s.go("/admin");
      const email = "11256094@ntub.edu.tw";
      await center(row(s, email));
      await s.shot(1, deleteBtn(s, email), { crop: await rowCrop(s, email), scroll: false });
      await s.click(deleteBtn(s, email), { wait: 500 });
      await s.shot(2, confirmBox(s).getByRole("button", { name: s.t("Delete", "刪除"), exact: true }));
    },
    // s15 Find a user
    async (s) => {
      await s.go("/admin");
      const search = s.page.getByRole("textbox", { name: s.t("Search by name, email, or role", "搜尋姓名、Email 或角色") });
      const searchBox = search.locator("xpath=.."); // the visible field, icon included
      // The short result list ends at the bottom of the window, where the floating AI
      // button would be cut off at the frame's edge; it plays no part in this section.
      await s.page.addStyleTag({ content: '[class*="_fab_"]{visibility:hidden !important}' });
      await s.fill(search, s.t("Chen", "陳"), { wait: 400 });
      await search.evaluate((el) => el.scrollIntoView({ block: "start" }));
      await s.settle(200);
      const rows = await s.page.locator('[data-guide="admin-user-row"]').all();
      await s.shot(1, searchBox, { crop: await around([searchBox, ...rows]), scroll: false });
      await s.shot(2, rows, { crop: await around([searchBox, ...rows]), scroll: false });
      await s.fill(search, "11456", { wait: 400 });
      await search.evaluate((el) => el.scrollIntoView({ block: "start" }));
      const clear = s.page.getByRole("button", { name: s.t("Clear search", "清除搜尋") });
      const r = await around([search, clear], 24);
      await s.shot(2, clear, { crop: { ...r, x: 340, width: 1100 }, scroll: false, newFrame: true });
    },
  ],
};
