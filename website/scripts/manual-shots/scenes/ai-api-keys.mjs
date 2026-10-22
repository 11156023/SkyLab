// Manual page: manual/ai-api-keys.md
import { adminCredentials, adminKeyMocks } from "../mocks/ai.mjs";

const statusGroup = (s) => s.page.locator('[data-guide="ai-keys-admin-status"]').getByRole("group");
const tabBtn = (s, en, zh) => statusGroup(s).getByRole("button", { name: new RegExp(`^${s.t(en, zh)}`) });
const row = (s, text) => s.page.locator("tr", { hasText: text }).first();
// The top of the page down to just below the fifth table row.
const TOP = { x: 0, y: 0, width: 1440, height: 535 };

export default {
  page: "ai-api-keys",
  role: "admin",
  mocks(m) {
    adminKeyMocks(m);
  },
  sections: [
    // s01 Switch between active and disabled keys
    async (s) => {
      await s.go("/ai-api-keys");
      await s.shot(1, tabBtn(s, "Active", "啟用中"), { crop: TOP });
      await s.click(tabBtn(s, "Disabled", "已停用"), { wait: 500 });
      await s.shot(2, [tabBtn(s, "Disabled", "已停用"), tabBtn(s, "All", "全部")], { crop: TOP });
      await s.fill(s.page.locator('[data-guide="ai-keys-admin-search"] input'), s.t("Thesis", "專題"), { wait: 700 });
      await s.shot(3, [statusGroup(s), s.page.locator('[data-guide="ai-keys-admin-search"] input')], { crop: TOP, newFrame: true });
    },
    // s02 Filter by role and creation time
    async (s) => {
      await s.go("/ai-api-keys");
      const btn = s.page.locator('[data-guide="ai-keys-admin-filter"] > button');
      await s.shot(1, btn, { crop: TOP });
      await s.click(btn, { wait: 400 });
      const pop = s.page.locator('[data-guide="ai-keys-admin-filter"] [role="dialog"]');
      await s.click(pop.getByLabel(s.t("Student", "學生")), { wait: 400 });
      await s.click(pop.getByLabel(s.t("Teacher", "教師")), { wait: 400 });
      await s.click(pop.getByLabel(s.t("Last 30 days", "30 天內")), { wait: 600 });
      const pb = await pop.boundingBox();
      const crop = { x: 0, y: 0, width: 1440, height: Math.round(pb.y + pb.height + 24) };
      await s.shot(2, pop.locator("fieldset").nth(0), { crop });
      await s.shot(3, pop.locator("fieldset").nth(1), { crop });
      await s.shot(4, btn, { crop });
      await s.shot(5, pop.getByRole("button", { name: s.t("Clear filters", "清除篩選") }), { crop });
    },
    // s03 Find a specific key
    async (s) => {
      await s.go("/ai-api-keys");
      const input = s.page.locator('[data-guide="ai-keys-admin-search"] input');
      await s.fill(input, "ccai_Mf2", { wait: 700 });
      await s.shot(1, [input, row(s, adminCredentials(s.m)[1].api_key_name)], { min: [1440, 560] });
      await s.shot(2, s.page.getByRole("button", { name: s.t("Clear search", "清除搜尋") }), { min: [1440, 560] });
    },
    // s04 Check how a key is used
    async (s) => {
      await s.go("/ai-api-keys");
      const cells = s.page.locator('[data-guide="ai-keys-admin-activity"]');
      await s.shot(0, [cells.nth(0), cells.nth(1), cells.nth(2), cells.nth(3), cells.nth(4)], { crop: "viewport" });
    },
    // s05 Delete a key
    async (s) => {
      await s.go("/ai-api-keys");
      const name = adminCredentials(s.m)[5].api_key_name;
      await s.shot(1, row(s, name).locator('[data-guide="ai-keys-admin-revoke"]'), { crop: { x: 0, y: 0, width: 1440, height: 680 } });
      await s.click(row(s, name).locator('[data-guide="ai-keys-admin-revoke"]'), { wait: 600 });
      await s.shot(2, s.page.locator('[role="alertdialog"]').getByRole("button", { name: s.t("Confirm delete", "確認刪除") }));
    },
    // s06 Page through a long list
    {
      mocks(m) {
        adminKeyMocks(m, { fill: 137 });
      },
      run: async (s) => {
        await s.go("/ai-api-keys");
        // The floating AI assistant button sits on top of the Next button.
        await s.page.getByRole("button", { name: s.t("Open AI assistant", "開啟 AI 助手") }).evaluate((el) => { el.style.visibility = "hidden"; });
        const next = s.page.getByRole("button", { name: s.t("Next", "下一頁") });
        await next.scrollIntoViewIfNeeded();
        const pager = next.locator("xpath=../..");
        await s.shot(0, pager, { crop: "viewport" });
      },
    },
  ],
};
