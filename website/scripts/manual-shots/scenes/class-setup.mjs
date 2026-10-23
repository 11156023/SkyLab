// Manual page: manual/class-setup.md
import { CLASS_IDS, installClasses } from "../mocks/class.mjs";

const MAIN = { x: 330, y: 0, width: 1110, height: 900 };
const cls = (name) => `[class*="_${name}_"]`;
const toast = (s) => s.page.locator("[data-sonner-toast]").first();
const setup = (key, step) => `/class-setup?classId=${CLASS_IDS[key]}&step=${step}`;
const btn = (s, en, zh) => s.page.getByRole("button", { name: s.t(en, zh), exact: true }).first();
const field = (s, en, zh) => s.page.locator("label").filter({ has: s.page.locator("span", { hasText: new RegExp(`^${s.t(en, zh)}$`) }) }).first();

const WEB_WEEKS = {
  1: { title: ["HTML structure and semantic tags", "HTML 結構與語意標籤"], status: "published" },
  2: { title: ["CSS layout with Flexbox and Grid", "CSS 版面：Flexbox 與 Grid"], status: "published" },
  3: { title: ["JavaScript basics and the DOM", "JavaScript 基礎與 DOM"], status: "published" },
};

export default {
  page: "class-setup",
  role: "teacher",
  mocks(m) {
    installClasses(m);
  },
  sections: [
    // s01 Move through the wizard
    async (s) => {
      await s.go(setup("web", 4), { wait: 800 });
      await s.shot(1, s.page.locator('[data-guide="class-setup-stepper"] nav'), { crop: MAIN });
      await s.shot(2, [btn(s, "Previous", "上一步"), s.page.locator('[data-guide="class-setup-next"]')], { crop: MAIN });
      await s.shot(5, s.page.locator('[data-guide="class-setup-draft"]'), { crop: MAIN });
      await s.shot(6, btn(s, "Back to class management", "返回班級管理"), { crop: MAIN });
      // A class without students: the later steps are locked.
      await s.go(setup("iot", 2), { wait: 800 });
      const steps = s.page.locator('[data-guide="class-setup-stepper"] li button');
      await s.shot(3, [steps.nth(2), steps.nth(3), steps.nth(4)], { crop: { x: 330, y: 0, width: 1110, height: 160 } });
      await s.go(setup("web", 4), { wait: 800 });
      await s.fill(s.page.locator('[data-guide="class-setup-weeks"] input').first(), s.t("HTML structure and semantic tags", "HTML 結構與語意標籤"));
      await s.click(btn(s, "Previous", "上一步"), { wait: 400 });
      const dlg = s.page.getByRole("alertdialog").or(s.page.getByRole("dialog")).first();
      await s.shot(4, dlg.getByRole("button", { name: s.t("Discard and leave", "捨棄變更並離開") }));
    },
    // s02 Step 1: Class and schedule
    async (s) => {
      await s.go("/class-setup", { wait: 800 });
      await s.fill(field(s, "Class name", "班級名稱").locator("input"), s.t("Cloud Native Application Development (IM 3B)", "雲端原生應用開發（資管三乙）"));
      await s.fill(field(s, "Class location", "上課地點").locator("input"), s.t("Information Building B306", "資訊大樓 B306"));
      await s.fill(field(s, "Start date", "開始日期").locator("input"), "2026-10-13");
      await s.fill(field(s, "End date", "結束日期").locator("input"), "2027-01-12");
      await field(s, "Weekly class day", "每週上課").locator("select").selectOption("1");
      const times = field(s, "Class time", "上課時間").locator("input");
      await s.fill(times.nth(0), "09:10");
      await s.fill(times.nth(1), "12:00");
      await s.click(s.page.locator('[data-guide="class-setup-advanced"] summary'));
      await field(s, "Boot lead time", "提前開機").locator("select").selectOption("15");
      await s.settle(200);
      const crop = MAIN;
      await s.shot(1, field(s, "Class name", "班級名稱").locator("input"), { crop });
      await s.shot(2, field(s, "Class location", "上課地點").locator("input"), { crop });
      await s.shot(3, [field(s, "Start date", "開始日期"), field(s, "End date", "結束日期"), field(s, "Weekly class day", "每週上課"), field(s, "Class time", "上課時間")], { crop });
      await s.shot(4, s.page.locator('[data-guide="class-setup-advanced"]'), { crop });
      await s.shot(5, s.page.locator('[data-guide="class-setup-next"]'), { crop });
    },
    // s03 Step 2: Student list
    async (s) => {
      await s.go(setup("iot", 2), { wait: 800 });
      await s.page.locator('input[type="file"]').setInputFiles({ name: "iot-roster.csv", mimeType: "text/csv", buffer: Buffer.from("學號\n11156201\n11156202\n") });
      await s.settle(800);
      await s.fill(s.page.locator('[data-guide="class-setup-emails"] textarea'), "11156229@ntub.edu.tw\n11156230@ntub.edu.tw, 11156231@ntub.edu.tw");
      await s.shot(1, s.page.locator('[data-guide="class-setup-emails"]'), { crop: MAIN });
      // The CSV notice (still on screen) shows the import happened right away.
      await s.shot(2, [s.page.locator('[data-guide="class-setup-csv"]'), toast(s)], { crop: MAIN });
      await s.shot(3, s.page.locator('[data-guide="class-setup-group-import"]'), { crop: MAIN });
      await s.shot(4, s.page.locator(`[data-guide="class-setup-roster"] ${cls("rosterHead")}`), { crop: MAIN });
      const removeIcon = s.page.locator(`[data-guide="class-setup-roster"] li button`).nth(1);
      await s.shot(5, removeIcon, { crop: MAIN });
      await s.click(removeIcon, { wait: 400 });
      const dlg = s.page.getByRole("alertdialog");
      await s.shot(5, dlg.getByRole("button", { name: s.t("Remove", "移除"), exact: true }));
      await s.click(dlg.getByRole("button", { name: s.t("Cancel", "取消") }), { wait: 400 });
      await s.fill(s.page.locator('[data-guide="class-setup-emails"] textarea'), "11156229@ntub.edu.tw\n11156230@ntub.edu.tw\nkevin.chen99@gmail.com\nchen@ntub.edu.tw");
      await s.page.locator('[data-guide="class-setup-next"]').click();
      const rejected = s.page.locator("[data-sonner-toast]").filter({ hasNotText: "CSV" }).first();
      await rejected.waitFor();
      await s.page.waitForTimeout(300);
      // The rejected addresses stay in the box with a red border, so the box itself is not outlined.
      await s.shot(6, [s.page.locator('[data-guide="class-setup-next"]'), rejected], { crop: MAIN });
    },
    // s04 Step 3: Teaching environment
    async (s) => {
      await s.go(setup("ds", 3), { wait: 800 });
      const cards = s.page.locator('[data-guide="class-setup-env-list"] > button');
      // Frames: the list with the disabled button (1, 2) / the "no suitable template" box (3) /
      // back from the editor with the new environment selected (4) and "Continue later" (5).
      await s.shot(1, s.page.locator('[data-guide="class-setup-env-list"]'), { crop: MAIN });
      await s.shot(2, [s.page.locator('[data-guide="class-setup-next"]'), s.page.locator(cls("footerHint"))], { crop: MAIN });
      const pause = s.page.locator('[data-guide="class-setup-template-pause"]');
      await s.shot(3, pause.getByRole("button").nth(0), { crop: pause, newFrame: true });
      // Back from the environment editor: the router state carries the new environment.
      await s.page.evaluate(() => window.history.replaceState({ usr: { createdTemplateId: "env-k8s" }, key: "back", idx: 0 }, ""));
      await s.page.reload();
      await toast(s).waitFor({ timeout: 15000 });
      await s.page.waitForTimeout(500);
      await s.shot(4, [cards.nth(2), toast(s)], { newFrame: true, crop: MAIN });
      await s.shot(5, pause.getByRole("button").nth(1), { crop: MAIN });
    },
    // s05 Step 4: Weekly tasks
    async (s) => {
      await s.go(setup("web", 4), { wait: 800 });
      const inputs = s.page.locator('[data-guide="class-setup-weeks"] input');
      for (let i = 0; i < 3; i++) await s.fill(inputs.nth(i), s.lang === "en" ? WEB_WEEKS[i + 1].title[0] : WEB_WEEKS[i + 1].title[1]);
      const rows = s.page.locator('[data-guide="class-setup-weeks"] > label');
      // Step 2 first: its box ends where row 1 starts, and marks drawn later sit on top,
      // so the "1" badge is not crossed by the step-2 outline.
      await s.shot(2, s.page.locator('[data-guide="class-setup-publish-weeks"]'), { crop: MAIN });
      await s.shot(1, [rows.nth(0), s.page.locator(`${cls("sectionHeader")} em`)], { crop: MAIN });
      await s.shot(3, s.page.locator('[data-guide="class-setup-next"]'), { crop: MAIN });
    },
    // s06 Step 5: Confirm and submit
    {
      mocks(m) { m.state.classes.web.weeks = WEB_WEEKS; },
      run: async (s) => {
        await s.go(setup("web", 5), { wait: 1200 });
        await s.shot(1, s.page.locator('[data-guide="class-setup-readiness"]'), { crop: MAIN });
        await s.shot(2, s.page.locator('[data-guide="class-setup-summary"]'), { crop: MAIN });
        await s.shot(3, s.page.locator('[data-guide="class-setup-provision"]'), { crop: MAIN });
        await s.click(s.page.locator('[data-guide="class-setup-provision"]'), { wait: 1200 });
        await s.shot(4, s.page.locator('[data-guide="class-overview-status"]'), { crop: { x: 330, y: 0, width: 1110, height: 560 } });
      },
    },
    // s07 Use the AI Assistant
    {
      mocks(m) {
        m.state.classes.web.weeks = WEB_WEEKS;
        // The backend's screen list names this page (app/ai/contextual_help/surfaces.py, id "class-setup");
        // without it the panel says it is viewing "SkyLab".
        m.use([["GET", /^\/api\/v1\/ai\/contextual-help\/surfaces$/, () => [{
          id: "class-setup", path: "/class-setup", title: m.L("Class setup", "建立班級"),
          purpose: m.L("Work through the schedule, students, environment and weekly tasks; each step is saved to the class.",
            "依序完成課表、學生、環境與每週任務，每一步都會保存到正式班級。"),
          when_to_use: null, has_fields: true,
        }]]]);
      },
      run: async (s) => {
        await s.go(setup("web", 5), { wait: 1200 });
        await s.click(s.page.locator('[data-guide="request-ai-helper-button"]'), { wait: 600 });
        const panel = s.page.locator('[data-guide="request-ai-helper-panel"]');
        await panel.locator("textarea").fill(s.t("What should I do next?", "接下來該做什麼？"));
        await s.press("Enter", { wait: 1500 });
        await s.shot(0, panel, { crop: "viewport" });
      },
    },
  ],
};
