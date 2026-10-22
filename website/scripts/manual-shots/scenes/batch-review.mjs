// Manual page: manual/batch-review.md
import { MAIN, frame, useBatchReview } from "../mocks/resadmin.mjs";

const TABS = {
  pending: ["Pending", "待審核"],
  approved: ["Approved", "已通過"],
  rejected: ["Rejected", "已拒絕"],
  all: ["All", "全部"],
};

const tabGroup = (s) => s.page.getByRole("group", { name: s.t("Filter reviews by status", "審核狀態篩選") });
const tab = (s, key) => tabGroup(s).locator("button", { hasText: new RegExp(`^${s.t(...TABS[key])}\\s*\\d*$`) });
const row = (s, text) => s.page.locator('[data-guide="batch-review-row"]', { hasText: text }).first();
const list = (s) => s.page.locator('[data-guide="batch-review-list"]');
const detail = (s) => s.page.locator('[data-guide="batch-review-detail"]');
const box = (s, en, zh) => detail(s).locator('[class*="_reasonBox_"]', { hasText: s.t(en, zh) }).first();
const decision = (s) => s.page.locator('[data-guide="batch-review-decision"]');
const notes = (s) => decision(s).locator("textarea");
const btn = (s, en, zh) => decision(s).getByRole("button", { name: s.t(en, zh), exact: true });
const band = (height) => ({ crop: { ...MAIN, height }, scroll: false });
const C = { crop: MAIN };
const CS = { crop: MAIN, scroll: false };
// Target whose outline sits dy px inside the element, for list rows that touch each other.
const inset = (loc, dy = 5) => ({
  scrollIntoViewIfNeeded: () => loc.scrollIntoViewIfNeeded(),
  evaluateHandle: (fn) => loc.evaluateHandle(fn),
  boundingBox: async () => {
    const b = await loc.boundingBox();
    return b && { x: b.x, y: b.y + dy, width: b.width, height: b.height - 2 * dy };
  },
  toString: () => String(loc),
});

const WEB = ["Web Programming (IM 3A)", "網頁程式設計（資管三甲）"];
// A class with a single machine node is listed under its hostname prefix.
const DB = ["dbms", "dbms"];
const LINUX = ["linux-lab", "linux-lab"];
const NET = ["net-lab", "net-lab"];
const PY = ["py-lab", "py-lab"];

async function open(s, text) {
  await s.click(row(s, text), { wait: 600 });
}

export default {
  page: "batch-review",
  role: "admin",
  mocks(m) {
    useBatchReview(m);
  },
  sections: [
    // s01 Pick a list
    async (s) => {
      await s.go("/batch-review");
      await frame(s);
      await s.shot(1, [tab(s, "pending"), ...(await list(s).locator('[data-guide="batch-review-row"]').all())], band(400));
      await s.shot(5, tab(s, "approved").locator('[class*="_segmentBadge_"]'), band(400));
      await s.click(tab(s, "approved"), { wait: 600 });
      await frame(s);
      await s.shot(2, await list(s).locator('[class*="_badge_"]').all(), band(450));
      await s.shot(3, tab(s, "rejected"), band(450));
      await s.shot(4, tab(s, "all"), band(450));
    },
    // s02 Find a batch
    async (s) => {
      await s.go("/batch-review");
      const search = s.page.locator('[data-guide="batch-review-search"] input');
      await s.fill(search, s.t("Database", "資料庫"));
      await frame(s);
      await s.shot(1, search, band(240));
      await s.fill(search, "");
      await frame(s);
      await s.shot(2, [row(s, s.t(...DB)).locator('[class*="_rowIcon_"]'), row(s, "ai-workshop").locator('[class*="_rowIcon_"]')], band(400));
      await s.shot(3, [row(s, s.t(...DB)).locator('[class*="_rowSide_"]'), row(s, s.t(...DB)).locator('[class*="_rowMeta_"]')], band(400));
      await s.shot(4, row(s, s.t(...WEB)).locator('[class*="_rowName_"]'), band(400));
    },
    // s03 Look at a batch
    async (s) => {
      await s.go("/batch-review");
      await s.click(tab(s, "approved"), { wait: 600 });
      await open(s, s.t(...LINUX));
      await frame(s);
      await s.shot(1, [row(s, s.t(...LINUX)), detail(s).locator('[class*="_infoGrid_"]'), box(s, "Provisioning Progress", "建立進度"), box(s, "Batch Members", "批次成員")], CS);
      await s.shot(2, detail(s).locator('[class*="_detailHeader_"]'), CS);
    },
    // s04 Preview a recurring schedule
    async (s) => {
      await s.go("/batch-review");
      await open(s, s.t(...DB));
      const rec = box(s, "Recurring Schedule", "週期排程");
      await s.click(rec.getByRole("button"), { wait: 900 });
      await frame(s);
      await s.shot(1, rec.locator('[class*="_ruleHuman_"]'), CS);
      await s.shot(2, [rec.getByRole("button"), rec.locator("ul")], CS);
      await open(s, "ai-workshop");
      const rec2 = box(s, "Recurring Schedule", "週期排程");
      await s.click(rec2.getByRole("button"), { wait: 900 });
      await frame(s);
      await s.shot(3, rec2.locator("ul li"), CS);
    },
    // s05 Approve a batch
    async (s) => {
      await s.go("/batch-review");
      await open(s, s.t(...WEB));
      await s.fill(notes(s), s.t("Approved for the Web Programming final project, weeks 8 to 16.", "網頁程式設計期末專題（第 8 至 16 週）使用，核准。"));
      await frame(s);
      await s.shot(1, row(s, s.t(...WEB)), CS);
      await s.shot(2, notes(s), CS);
      await s.shot(3, btn(s, "Approve", "核准"), CS);
      await s.click(btn(s, "Approve", "核准"), { wait: 500 });
      const dlg = s.page.getByRole("alertdialog");
      await s.settle(500);
      const ok = dlg.getByRole("button", { name: s.t("Approve", "核准"), exact: true });
      await frame(s);
      await s.shot(3, dlg.locator("p").first());
      await s.shot(4, ok);
      await s.click(ok, { wait: 600 });
      await s.click(tab(s, "approved"), { wait: 2500 });
      await frame(s);
      await s.shot(4, row(s, s.t(...WEB)).locator('[class*="_badge_"]'), band(640));
    },
    // s06 Reject a batch
    async (s) => {
      await s.go("/batch-review");
      await open(s, s.t(...DB));
      await s.fill(notes(s), s.t("Please move the sessions to the afternoon; the morning slots are full during the midterm exams.", "期中考週上午時段已滿，請改排下午時段後再送出。"));
      await frame(s);
      await s.shot(1, row(s, s.t(...DB)), CS);
      await s.shot(2, notes(s), CS);
      await s.shot(3, btn(s, "Reject", "駁回"), CS);
      await s.click(btn(s, "Reject", "駁回"), { wait: 500 });
      const dlg = s.page.getByRole("alertdialog");
      await s.settle(500);
      const ok = dlg.getByRole("button", { name: s.t("Reject", "駁回"), exact: true });
      await frame(s);
      await s.shot(3, ok);
      await s.click(ok, { wait: 600 });
      await s.click(tab(s, "rejected"), { wait: 2500 });
      await frame(s);
      await s.shot(4, row(s, s.t(...DB)), band(360));
    },
    // s07 Follow failures
    async (s) => {
      await s.go("/batch-review");
      await s.click(tab(s, "approved"), { wait: 600 });
      await open(s, s.t(...NET));
      // First frame shows the sidebar too, for the Background Jobs entry (step 4).
      const V = { crop: "viewport", scroll: false };
      await s.shot(1, [tab(s, "approved"), row(s, s.t(...NET))], V);
      await s.shot(2, box(s, "Provisioning Progress", "建立進度"), V);
      await s.shot(3, detail(s).locator('[class*="_failedNote_"]'), V);
      await s.shot(4, s.page.locator(`text="${s.t("Background Jobs", "背景任務")}" >> visible=true`).last(), V);
      const members = box(s, "Batch Members", "批次成員");
      await members.locator("li").nth(24).scrollIntoViewIfNeeded();
      await s.page.waitForTimeout(300);
      await frame(s);
      await s.shot(2, [members.locator("li").nth(22), members.locator("li").nth(23), members.locator("li").nth(24)].map((li) => inset(li)), CS);
    },
    // s08 Check a past decision
    async (s) => {
      await s.go("/batch-review");
      await s.click(tab(s, "approved"), { wait: 600 });
      await open(s, s.t(...PY));
      const notesBox = box(s, "Review Notes", "審核備註");
      await notesBox.scrollIntoViewIfNeeded();
      await frame(s);
      await s.shot(1, [tab(s, "approved"), row(s, s.t(...PY))], CS);
      await s.shot(2, notesBox, CS);
    },
  ],
};
