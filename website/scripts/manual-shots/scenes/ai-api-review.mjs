// Manual page: manual/ai-api-review.md
import { reviewMocks, reviewRequests } from "../mocks/ai.mjs";

const tabs = (s) => s.page.locator('[data-guide="ai-review-tabs"]').getByRole("group");
const tabBtn = (s, en, zh) => tabs(s).getByRole("button", { name: new RegExp(`^${s.t(en, zh)}`) });
const rowOf = (s, i) => s.page.locator("tbody tr", { hasText: reviewRequests(s.m)[i].api_key_name }).first();
// Title, tabs and the three pending requests (viewport is 1760 px wide here).
const TOP = { x: 0, y: 0, width: 1760, height: 420 };
const dialog = (s) => s.page.locator('[role="dialog"]').last();

// The review table has seven columns; at 1440 px the Actions column scrolls out of view.
async function open(s) {
  await s.page.setViewportSize({ width: 1760, height: 900 });
  await s.go("/ai-api-review");
}

async function review(s, i, en, zh, comment, confirmEn, confirmZh, numbers) {
  await open(s);
  const btn = rowOf(s, i).getByRole("button", { name: s.t(en, zh) });
  await s.shot(numbers[0], btn, { crop: s.page.locator("table") });
  await s.click(btn, { wait: 600 });
  const d = dialog(s);
  const text = d.locator("textarea");
  await s.fill(text, comment);
  const steps = numbers.slice(1);
  if (steps.length === 3) await s.shot(steps.shift(), text.locator("xpath=preceding-sibling::div[1]"));
  await s.shot(steps.shift(), text);
  await s.shot(steps.shift(), d.getByRole("button", { name: s.t(confirmEn, confirmZh) }));
}

export default {
  page: "ai-api-review",
  role: "admin",
  mocks(m) {
    reviewMocks(m);
  },
  sections: [
    // s01 Find requests that need a decision
    async (s) => {
      await open(s);
      await s.shot(1, tabBtn(s, "Pending", "待審核"), { crop: TOP });
      await s.shot(2, [tabBtn(s, "Approved", "已通過"), tabBtn(s, "Rejected", "已拒絕"), tabBtn(s, "All", "全部")], { crop: TOP });
      await s.shot(3, rowOf(s, 0), { crop: TOP });
    },
    // s02 Read a request
    async (s) => {
      await open(s);
      await s.shot(0, [rowOf(s, 0), rowOf(s, 0).locator('[data-guide="ai-review-purpose"]')], { crop: TOP });
    },
    // s03 Approve a request
    async (s) => {
      await review(s, 0, "Approve", "通過",
        s.t("Approved for the graduation project. The key expires in 30 days; submit a new request if you need longer.", "專題用途核准，金鑰 30 天後到期，需要延長請再申請。"),
        "Confirm Approval", "確認通過", [1, 2, 3, 4]);
    },
    // s04 Reject a request
    async (s) => {
      await review(s, 1, "Reject", "拒絕",
        s.t("Please name the course and the dataset you will label, and how many calls you expect.", "請寫明課程名稱、要標註的資料集，以及預計的呼叫次數。"),
        "Confirm Rejection", "確認拒絕", [1, 2, 3]);
    },
  ],
};
