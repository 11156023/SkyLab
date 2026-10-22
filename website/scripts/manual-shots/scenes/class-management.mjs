// Manual page: manual/class-management.md
import { installClasses } from "../mocks/class.mjs";

const nameOf = (s, key) => s.m.state.classes[key].name[s.lang === "en" ? 0 : 1];
const ticket = (s, key) => s.page.locator('[data-guide="class-mgmt-ticket"]').filter({ hasText: nameOf(s, key) }).first();
const cls = (name) => `[class*="_${name}_"]`;
const MAIN = { x: 330, y: 0, width: 1110, height: 900 };
const TOP = { x: 330, y: 0, width: 1110, height: 470 };
const HEADER = { x: 330, y: 0, width: 1110, height: 168 };
const toast = (s) => s.page.locator("[data-sonner-toast]").first();
// The content column across the row of tickets that holds `t` (tickets in a row differ in height).
async function rowAround(t) {
  const b = await t.boundingBox();
  const row = (await t.page().locator('[data-guide="class-mgmt-ticket"] > button').evaluateAll((els) => els.map((e) => e.getBoundingClientRect())
    .map((r) => ({ y: r.y, bottom: r.bottom })))).filter((r) => Math.abs(r.y - b.y) < 4);
  const bottom = Math.max(...row.map((r) => r.bottom));
  return { x: 330, y: b.y - 16, width: 1110, height: bottom - b.y + 32 };
}

export default {
  page: "class-management",
  role: "teacher",
  mocks(m) {
    installClasses(m);
  },
  sections: [
    // s01 Create a class
    async (s) => {
      await s.go("/class-management");
      const create = s.page.getByRole("button", { name: s.t("Create class", "建立班級") }).first();
      await s.shot(1, create, { crop: HEADER });
      await s.click(create, { wait: 700 });
      await s.shot(2, s.page.locator('[data-guide="class-setup-stepper"]'), { crop: TOP });
      // Back in the list: the draft class waits in Planning.
      await s.go("/class-management");
      const t = ticket(s, "web");
      await s.shot(3, [t.locator(cls("status")), t.locator(cls("stub"))], { crop: await rowAround(t), newFrame: true });
    },
    // s02 Filter and search
    async (s) => {
      await s.go("/class-management");
      await s.click(s.page.locator('[data-guide="class-mgmt-archived"] input'));
      await s.fill(s.page.getByPlaceholder(s.t("Search classes", "搜尋班級")), s.t("Programming", "程式"), { wait: 300 });
      await s.shot(1, s.page.getByRole("group", { name: s.t("Class status filter", "班級狀態篩選") }).first(), { crop: { x: 330, y: 0, width: 1110, height: 460 } });
      await s.shot(2, s.page.getByPlaceholder(s.t("Search classes", "搜尋班級")), { crop: { x: 330, y: 0, width: 1110, height: 460 } });
      await s.shot(3, s.page.locator('[data-guide="class-mgmt-archived"]'), { crop: { x: 330, y: 0, width: 1110, height: 460 } });
    },
    // s03 Read a class ticket
    async (s) => {
      await s.go("/class-management");
      const t = ticket(s, "ml");
      const crop = s.page.locator('[data-guide="class-mgmt-ticket"]').filter({ hasText: nameOf(s, "ml") }).first();
      // Two frames: on one the badges would sit on the status and on the "Some machines failed" note.
      await s.shot(1, [t.locator(cls("status")), t.locator(cls("term"))], { crop, pad: 40 });
      await s.shot(2, [t.locator(cls("title")), t.locator(cls("hint"))], { crop, pad: 40 });
      await s.shot(3, t.locator(cls("fields")), { crop, pad: 40, newFrame: true });
      await s.shot(4, t.locator(cls("stub")), { crop, pad: 40 });
    },
    // s04 Open a class
    async (s) => {
      await s.go("/class-management");
      await s.shot(1, [ticket(s, "web"), ticket(s, "cloud")], { crop: MAIN });
      await s.click(ticket(s, "web").locator("button").first(), { wait: 800 });
      await s.shot(2, s.page.locator('[data-guide="class-setup-stepper"] [aria-current="step"]'), { crop: TOP });
      await s.go("/class-management");
      await s.click(ticket(s, "cloud").locator("button").first(), { wait: 1200 });
      // The workspace's sections: students, machines, weekly content, monitoring, checks.
      await s.shot(3, s.page.getByRole("group", { name: s.t("Class management workflow", "班級管理流程") }), { crop: { x: 330, y: 0, width: 1110, height: 620 } });
    },
    // s05 Archive and reclaim classes in bulk
    {
      mocks(m) { m.state.archiveDelay = 2500; },
      run: async (s) => {
        await s.go("/class-management");
        const box = (key) => s.page.getByRole("checkbox", { name: s.t(`Select ${nameOf(s, key)}`, `選取 ${nameOf(s, key)}`) });
        await box("linux").check();
        await box("net").check();
        await s.settle(300);
        await s.shot(1, [box("linux"), box("net")], { crop: MAIN });
        const bar = s.page.locator('[data-guide="class-mgmt-toolbar"]');
        await s.shot(2, bar, { crop: MAIN });
        const archive = s.page.getByRole("button", { name: s.t("Archive and reclaim", "封存並回收") }).first();
        await s.shot(3, archive, { crop: MAIN });
        await s.click(archive, { wait: 400 });
        // Three frames at most: the list, the confirmation (class names and machine total; Confirm),
        // the result. The "Archiving… N of M" progress in between would be a fourth frame.
        const dlg = s.page.getByRole("alertdialog");
        await s.shot(3, dlg.locator("p").first());
        await s.shot(4, dlg.getByRole("button", { name: s.t("Archive and reclaim", "封存並回收") }));
        await dlg.getByRole("button", { name: s.t("Archive and reclaim", "封存並回收") }).click();
        await toast(s).waitFor({ timeout: 8000 });
        await s.page.waitForTimeout(400);
        await s.shot(5, toast(s), { crop: HEADER });
      },
    },
  ],
};
