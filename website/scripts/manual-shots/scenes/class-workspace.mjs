// Manual page: manual/class-workspace.md
import { CLASS_IDS, brokenEnvironment, desktopFrame, installClasses, patchWebSocket } from "../mocks/class.mjs";
import { useUserGroups } from "../mocks/system.mjs";

const MAIN = { x: 330, y: 0, width: 1110, height: 900 };
const UPPER = { x: 330, y: 0, width: 1110, height: 620 };
const cls = (name) => `[class*="_${name}_"]`;
const toast = (s) => s.page.locator("[data-sonner-toast]").first();
const at = (key, section) => `/class-management/${CLASS_IDS[key]}/${section}`;
const btn = (s, en, zh, scope) => (scope || s.page).getByRole("button", { name: s.t(en, zh), exact: true }).first();
const menuBtn = (s) => s.page.getByRole("button", { name: s.t("More class actions", "更多班級操作") });
const menuItem = (s, en, zh) => s.page.getByRole("menuitem", { name: s.t(en, zh) });
const dialog = (s) => s.page.getByRole("dialog").or(s.page.getByRole("alertdialog")).last();
const csv = (text) => ({ name: "students.csv", mimeType: "text/csv", buffer: Buffer.from(text) });

export default {
  page: "class-workspace",
  role: "teacher",
  mocks(m) {
    installClasses(m, { extraEnvs: { sec: brokenEnvironment(m) } });
    useUserGroups(m);
  },
  sections: [
    // s01 Read the class overview
    async (s) => {
      await s.go(at("cloud", "overview"), { wait: 800 });
      await s.click(s.page.locator('[data-guide="class-overview-info"] summary'));
      await s.shot(1, s.page.locator('[data-guide-tab="class-section"][aria-pressed="true"]'), { crop: UPPER });
      await s.shot(2, s.page.locator('[data-guide="class-overview-status"]'), { crop: UPPER });
      await s.shot(3, s.page.locator('[data-guide="class-overview-week"]'), { crop: UPPER });
      await s.shot(4, s.page.locator('[data-guide="class-overview-machines"]'), { crop: UPPER });
      await s.shot(5, s.page.locator('[data-guide="class-overview-info"]'), { crop: UPPER });
      await s.go(at("ds", "overview"), { wait: 800 });
      await s.shot(2, s.page.locator('[data-guide="class-overview-status"]'), { crop: { x: 330, y: 0, width: 1110, height: 520 } });
    },
    // s02 Edit the class and schedule
    async (s) => {
      await s.go(at("ds", "overview"), { wait: 800 });
      await s.click(menuBtn(s));
      await s.shot(1, menuItem(s, "Edit class & schedule", "編輯班級與課表"), { crop: { x: 330, y: 0, width: 1110, height: 420 } });
      await s.click(menuItem(s, "Edit class & schedule", "編輯班級與課表"), { wait: 500 });
      const d = dialog(s);
      await s.fill(d.getByLabel(s.t("Class location", "上課地點")), s.t("Information Building B402", "資訊大樓 B402"));
      await d.getByLabel(s.t("Boot lead time", "提前開機")).selectOption("15");
      await s.settle(200);
      await s.shot(2, [d.getByLabel(s.t("Class location", "上課地點")), d.getByLabel(s.t("Boot lead time", "提前開機"))]);
      await s.shot(3, btn(s, "Save changes", "儲存變更", d));
    },
    // s03 Add students
    {
      mocks(m) { m.state.csvRejects = { notFound: ["11156288@ntub.edu.tw"], invalid: ["chen@ntub.edu.tw"] }; },
      run: async (s) => {
        await s.go(at("iot", "students"), { wait: 800 });
        await s.shot(1, s.page.locator('[data-guide="class-students-list"] button'), { crop: { x: 330, y: 0, width: 1110, height: 560 } });
        await s.click(s.page.locator('[data-guide="class-students-list"] button'), { wait: 500 });
        const d = dialog(s);
        await s.fill(d.locator("textarea"), "11156201@ntub.edu.tw, 11156202@ntub.edu.tw\n11156203@ntub.edu.tw\nkevin.chen99@gmail.com\nwu@ntub.edu.tw");
        await s.shot(2, d.locator("textarea"));
        const add = d.getByRole("button", { name: s.t("Add students", "加入學生") }).last();
        await s.shot(3, add);
        await add.click();
        await toast(s).waitFor();
        // Hovering the toaster pauses sonner's 4 s timers and expands the stack, so the
        // paste notice is still there next to the CSV notice in the last frame.
        await toast(s).hover();
        await s.page.locator('[data-guide="class-students-actions"] input[type="file"]').setInputFiles(csv("學號\n11156204\n11156205\n"));
        const toasts = s.page.locator("[data-sonner-toast]");
        await toasts.filter({ hasText: "CSV" }).first().waitFor({ timeout: 8000 });
        await s.page.waitForTimeout(600);
        const o = { crop: MAIN, scroll: false };
        await s.shot(3, toasts.filter({ hasNotText: "CSV" }).first(), { ...o, newFrame: true });
        await s.shot(4, s.page.locator('[data-guide="class-students-actions"] button').first(), o);
        await s.shot(5, s.page.locator(`[data-guide="class-students-list"] ${cls("memberRow")}`).nth(4), o);
        await s.shot(6, toasts.filter({ hasText: "CSV" }).first(), o);
      },
    },
    // s04 Import students from a group
    async (s) => {
      await s.go(at("iot", "students"), { wait: 800 });
      const open = btn(s, "Import from group", "從群組匯入");
      await s.shot(1, open, { crop: UPPER });
      await s.click(open, { wait: 600 });
      const d = dialog(s);
      await s.shot(2, d.locator("select"));
      const go = btn(s, "Import", "匯入", d);
      await s.shot(3, go);
      await go.click();
      await toast(s).waitFor();
      await s.page.waitForTimeout(600);
      await s.shot(3, toast(s), { crop: UPPER, scroll: false, newFrame: true });
    },
    // s05 Find and remove students
    async (s) => {
      await s.go(at("ds", "students"), { wait: 800 });
      const search = s.page.getByPlaceholder(s.t("Search name or email", "搜尋姓名或 Email"));
      await s.fill(search, s.t("Chen", "陳"), { wait: 300 });
      // Down to the end of the filtered list ("Chen" also finds "Cheng" in English).
      const lb = await s.page.locator('[data-guide="class-students-list"]').boundingBox();
      const top = { x: 330, y: 0, width: 1110, height: Math.min(900, Math.round(lb.y + lb.height + 20)) };
      await s.shot(1, search, { crop: top });
      const rm = s.page.locator('[data-guide="class-students-remove"]').first();
      await s.shot(2, rm, { crop: top });
      await s.click(rm, { wait: 400 });
      await s.shot(2, btn(s, "Remove", "移除", dialog(s)));
      await s.click(btn(s, "Cancel", "取消", dialog(s)), { wait: 400 });
      await s.fill(search, "", { wait: 300 });
      const boxes = s.page.locator(`[data-guide="class-students-list"] ${cls("memberRow")} input[type="checkbox"]`);
      for (const i of [1, 3, 4]) await boxes.nth(i).check();
      await s.settle(300);
      await s.shot(3, [s.page.getByRole("button", { name: s.t("Remove", "移除"), exact: true }).first(), boxes.nth(1), boxes.nth(3), boxes.nth(4)], { crop: MAIN });
    },
    // s06 Choose the teaching environment
    async (s) => {
      await s.go(at("ds", "machines"), { wait: 800 });
      const choose = s.page.locator('[data-guide="class-machines-choose"]');
      await s.shot(1, choose, { crop: UPPER });
      const cards = choose.locator(`${cls("envChoices")} > button`);
      await s.click(cards.filter({ hasText: s.t("Web Full-Stack Lab", "Web 全端開發環境") }), { wait: 600 });
      const LIST = { x: 330, y: 0, width: 1110, height: 625 };
      await s.shot(2, cards.filter({ hasText: s.t("Web Full-Stack Lab", "Web 全端開發環境") }), { newFrame: true, crop: LIST });
      await s.page.evaluate(() => window.scrollTo(0, 0));
      await cards.filter({ hasText: s.t("Network Security Lab", "網路安全攻防環境") }).click();
      await toast(s).waitFor();
      await s.page.waitForTimeout(300);
      await s.shot(3, [cards.filter({ hasText: s.t("Network Security Lab", "網路安全攻防環境") }), toast(s)], { crop: LIST, scroll: false });
      // Back from the course environment editor: the router state marks the new environment.
      await s.page.evaluate(() => window.history.replaceState({ usr: { createdTemplateId: "env-k8s" }, key: "back", idx: 0 }, ""));
      await s.page.reload();
      await toast(s).waitFor({ timeout: 15000 });
      await s.page.waitForTimeout(500);
      await s.shot(4, [s.page.locator('[data-guide="class-machines-create"] button'), cards.filter({ hasText: s.t("Docker & Kubernetes Lab", "Docker 與 Kubernetes 實作環境") }), toast(s)], { newFrame: true, crop: MAIN, scroll: false });
      await s.shot(5, s.page.locator(cls("topologySummary")), { crop: MAIN, scroll: false });
    },
    // s07 Read the applied environment (no numbered steps)
    async (s) => {
      await s.go(at("cloud", "machines"), { wait: 1200 });
      await s.shot(0, s.page.locator('[data-guide="class-machines-applied"]'), { crop: MAIN, scroll: false });
    },
    // s08 Submit for provisioning
    async (s) => {
      await s.go(at("web", "overview"), { wait: 1200 });
      const card = s.page.locator('[data-guide="class-overview-status"]');
      // The heading with the capacity figures under it (machines, CPU, memory, disk, IPs).
      await s.shot(1, card.locator(cls("setupSummary")).locator("h2").locator("xpath=.."), { crop: { x: 330, y: 0, width: 1110, height: 560 } });
      await s.shot(2, card.getByRole("button", { name: s.t("Confirm and submit for provisioning", "確認並送出建機") }), { crop: { x: 330, y: 0, width: 1110, height: 560 } });
      await s.click(card.getByRole("button", { name: s.t("Confirm and submit for provisioning", "確認並送出建機") }), { wait: 400 });
      await s.shot(3, btn(s, "Submit", "送出", dialog(s)));
      await s.go(at("net", "overview"), { wait: 1000 });
      await s.shot(4, s.page.locator('[data-guide="class-overview-status"]'), { crop: { x: 330, y: 0, width: 1110, height: 560 } });
    },
    // s09 Recover from a partly failed build
    async (s) => {
      await s.go(at("ml", "overview"), { wait: 1000 });
      await s.shot(1, s.page.getByRole("button", { name: s.t("Retry failed items", "重試失敗項目") }), { crop: { x: 330, y: 0, width: 1110, height: 560 } });
      s.m.state.classes.ml.allFailed = true;
      await s.go(at("ml", "overview"), { wait: 1000 });
      const unlock = s.page.getByRole("button", { name: s.t("Unlock and return to editing", "解除鎖定並返回編輯") });
      await s.shot(2, unlock, { newFrame: true, crop: { x: 330, y: 0, width: 1110, height: 560 } });
      await s.click(unlock, { wait: 400 });
      await s.shot(2, btn(s, "Release and return", "釋放並返回", dialog(s)));
    },
    // s10 Edit weekly content
    async (s) => {
      await s.go(at("cloud", "weekly"), { wait: 1000 });
      const rows = s.page.locator('[data-guide="class-weekly-row"]');
      await s.shot(1, s.page.locator(cls("weekVisibleCount")), { crop: { x: 330, y: 0, width: 1110, height: 420 }, scroll: false });
      await s.fill(rows.nth(8).locator("input").first(), s.t("Kubernetes basics", "Kubernetes 入門"));
      // The Publish button of a week without a topic is aria-disabled, so Playwright would wait forever.
      await rows.nth(9).getByRole("button", { name: s.t("Publish", "發布"), exact: true }).dispatchEvent("click");
      await toast(s).waitFor();
      // The rejected publish focuses (and scrolls to) the empty topic; put week 6 back under the toast.
      await s.page.waitForTimeout(300);
      await rows.nth(5).evaluate((el) => window.scrollBy(0, el.getBoundingClientRect().top - 250));
      await s.page.waitForTimeout(300);
      await s.shot(3, rows.nth(5).locator('[data-guide="class-weekly-files"]'), { crop: MAIN, scroll: false });
      await s.shot(2, rows.nth(8).locator("input").first(), { crop: MAIN, scroll: false });
      await s.shot(4, [rows.nth(9).locator('[data-guide="class-weekly-visibility"]'), toast(s)], { crop: MAIN, scroll: false });
      await s.shot(5, s.page.locator('[data-guide="class-weekly-save"] button'), { min: [1110, 300] });
    },
    // s11 Monitor the class
    async (s) => {
      await s.go(at("cloud", "progress"), { wait: 1500 });
      await s.page.evaluate(() => window.scrollTo(0, 260));
      const o = { crop: MAIN, scroll: false };
      await s.shot(1, s.page.locator(cls("liveBadge")), o);
      await s.shot(2, [s.page.locator(cls("machineTabs")), s.page.locator(cls("metricSwitch"))], o);
      await s.shot(3, s.page.locator(`${cls("heatmapSummary")} dl`), o);
      await s.shot(4, s.page.locator('[data-guide="class-progress-tiles"]'), o);
      await s.shot(5, s.page.locator('[data-guide="class-progress-attention"]'), o);
    },
    // s12 Open a student's machine
    async (s) => {
      s.m.state.vncFrame = await desktopFrame(s.browser, s.lang);
      await s.go(at("cloud", "progress"), { wait: 1500 });
      await patchWebSocket(s.page);
      await s.page.evaluate(() => window.scrollTo(0, 260));
      const tiles = s.page.locator('[data-guide="class-progress-tiles"] > button');
      await s.shot(1, [tiles.nth(2), s.page.locator('[data-guide="class-progress-attention"] li button').first()], { crop: MAIN, scroll: false });
      await s.click(tiles.nth(2), { wait: 800 });
      const d = dialog(s);
      await d.getByText(s.t("Connected", "已連線"), { exact: true }).waitFor({ timeout: 10000 });
      await s.page.waitForTimeout(600);
      // Not clicked: the frame keeps the last screenshot, which would relabel the button "Release control".
      await s.shot(2, btn(s, "Take control", "接管", d), { newFrame: true, crop: "viewport" });
      await s.shot(4, d.getByRole("button", { name: s.t("Close", "關閉") }), { crop: "viewport" });
      await s.click(d.getByRole("button", { name: s.t("Close", "關閉") }), { wait: 600 });
      await s.click(s.page.locator(`${cls("machineTabs")} button`).nth(1), { wait: 800 });
      await s.click(tiles.nth(4), { wait: 1500 });
      // The terminal fills the screen; its output is in the top part, so frame that.
      const term = dialog(s);
      const tb = await term.boundingBox();
      await s.shot(3, term.locator(cls("header")), { newFrame: true, crop: { x: tb.x - 16, y: Math.max(0, tb.y - 16), width: tb.width + 32, height: 440 } });
    },
    // s13 Broadcast a demo to the class
    async (s) => {
      await s.go(at("cloud", "progress"), { wait: 1500 });
      const row = s.page.locator('[data-guide="class-progress-broadcast"]');
      const crop = { x: 330, y: 0, width: 1110, height: 480 };
      await s.shot(1, row.locator("select"), { crop, scroll: false });
      await row.locator("select").selectOption("30090");
      await s.settle(800);
      await s.shot(2, row, { newFrame: true, crop, scroll: false });
      await s.shot(3, btn(s, "End broadcast", "結束直播", row), { crop, scroll: false });
    },
    // s14 Start or shut down the whole class
    {
      mocks(m) {
        // Start all is quick and two machines are locked by a backup; shutdown is slow enough to catch the progress.
        const LOCKED = [30141, 30177];
        m.use([["POST", /^\/api\/v1\/resources\/batch$/, ({ body }) => {
          const start = body?.action === "start";
          const results = (body?.vmids || []).map((vmid) => (start && LOCKED.includes(vmid)
            ? { vmid, success: false, message: "VM is locked (backup)" } : { vmid, success: true, message: "ok" }));
          return { __delay: start ? 60 : 900, __body: { results } };
        }]]);
      },
      run: async (s) => {
        await s.go(at("cloud", "progress"), { wait: 1500 });
        const row = s.page.locator('[data-guide="class-progress-power"]');
        const crop = { x: 330, y: 0, width: 1110, height: 480 };
        await s.shot(1, [btn(s, "Start all", "全班開機", row), btn(s, "Shut down all", "全班關機", row)], { crop, scroll: false });
        // Start all first, so its result notice is still on screen next to the shutdown progress in the last frame.
        await btn(s, "Start all", "全班開機", row).click();
        await toast(s).waitFor({ timeout: 8000 });
        await s.click(btn(s, "Shut down all", "全班關機", row), { wait: 400 });
        await s.shot(2, btn(s, "Shut down all", "全班關機", dialog(s)));
        await btn(s, "Shut down all", "全班關機", dialog(s)).click();
        await s.page.waitForTimeout(1500);
        await s.shot(3, [row.locator(cls("broadcastControls")).locator("span").last(), toast(s)], { crop, scroll: false });
      },
    },
    // s15 Extend the class
    async (s) => {
      await s.go(at("cloud", "overview"), { wait: 1000 });
      await s.click(menuBtn(s));
      await s.shot(1, menuItem(s, "Extend", "延長"), { crop: { x: 330, y: 0, width: 1110, height: 420 } });
      await s.click(menuItem(s, "Extend", "延長"), { wait: 500 });
      const d = dialog(s);
      await s.fill(d.locator('input[type="date"]'), "2027-01-25");
      await d.locator('input[type="date"]').evaluate((el) => el.blur());
      await s.shot(2, d.locator('input[type="date"]'));
      await s.shot(3, btn(s, "Extend", "延長", d));
    },
    // s16 Archive the class and reclaim machines
    {
      mocks(m) { m.state.reclaimFailures = 3; },
      run: async (s) => {
        await s.go(at("cloud", "overview"), { wait: 1000 });
        await s.click(menuBtn(s));
        await s.shot(1, menuItem(s, "Archive and reclaim", "封存並回收"), { crop: { x: 330, y: 0, width: 1110, height: 420 } });
        await s.click(menuItem(s, "Archive and reclaim", "封存並回收"), { wait: 500 });
        await s.shot(2, btn(s, "Archive and reclaim", "封存並回收", dialog(s)));
        await btn(s, "Archive and reclaim", "封存並回收", dialog(s)).click();
        await toast(s).waitFor();
        await s.page.waitForTimeout(400);
        await s.shot(3, [s.page.getByRole("button", { name: s.t("Retry reclamation", "重試回收") }), toast(s)], { crop: { x: 330, y: 0, width: 1110, height: 560 } });
      },
    },
  ],
};
