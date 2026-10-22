// Manual page: manual/request-review.md
import { MAIN, frame, useRequestReview } from "../mocks/resadmin.mjs";

const TABS = {
  pending: ["Pending", "待審核"],
  approved: ["Approved", "已通過"],
  rejected: ["Rejected", "已拒絕"],
  expired: ["Expired", "已過期"],
  all: ["All", "全部"],
};

const tabGroup = (s) => s.page.getByRole("group", { name: s.t("Filter requests by status", "申請狀態篩選") });
const tab = (s, key) => tabGroup(s).locator("button", { hasText: new RegExp(`^${s.t(...TABS[key])}\\s*\\d*$`) });
const row = (s, title) => s.page.locator('[data-guide="request-review-row"]', { hasText: title }).first();
const rowWrap = (s, title) => s.page.locator('[class*="_list_"] > div', { hasText: title }).first();
const list = (s) => s.page.locator('[data-guide="request-review-list"]');
const detail = (s) => s.page.locator('[data-guide="request-review-detail"]');
const info = (s, en, zh) => detail(s).locator('[class*="_infoRow_"]', { hasText: s.t(en, zh) }).first();
const decision = (s) => s.page.locator('[data-guide="request-review-decision"]');
const notes = (s) => decision(s).locator("textarea");
const approveBtn = (s) => decision(s).getByRole("button", { name: s.t("Approve", "核准"), exact: true });
const rejectBtn = (s) => decision(s).getByRole("button", { name: s.t("Reject", "拒絕"), exact: true });
const box = (s, en, zh) => detail(s).locator('[class*="_reasonBox_"]', { hasText: s.t(en, zh) }).first();
const spec = (s, name, vmid) => s.t(`${name} (VMID ${vmid}) Spec Change`, `${name}（VMID ${vmid}）規格調整`);
const rowExact = (s, title) => s.page.locator('[data-guide="request-review-row"]')
  .filter({ has: s.page.locator('[class*="_rowName_"]', { hasText: new RegExp(`^${title}$`) }) }).first();
const band = (height) => ({ crop: { ...MAIN, height }, scroll: false });
const C = { crop: MAIN };
const CS = { crop: MAIN, scroll: false };
// From the top of the page down to the bottom of `loc`.
const downTo = async (loc, pad = 16) => {
  const b = await loc.boundingBox();
  return { crop: { ...MAIN, height: Math.min(b.y + b.height + pad, 900) - MAIN.y }, scroll: false };
};
const toastGone = (s) => s.page.locator("[data-sonner-toast]").first().waitFor({ state: "detached", timeout: 8000 }).catch(() => {});

async function open(s, title) {
  await s.click(row(s, title), { wait: 600 });
}

export default {
  page: "request-review",
  role: "admin",
  mocks(m) {
    useRequestReview(m);
  },
  sections: [
    // s01 Pick a list
    async (s) => {
      await s.go("/request-review");
      await frame(s);
      await s.shot(1, [tab(s, "pending"), list(s)], C);
      await s.shot(2, [tab(s, "approved"), tab(s, "rejected"), tab(s, "expired"), tab(s, "all")], C);
      await s.shot(3, tab(s, "pending").locator('[class*="_segmentBadge_"]'), C);
      await s.shot(4, list(s), C);
    },
    // s02 Find a request
    async (s) => {
      await s.go("/request-review");
      const search = s.page.getByRole("textbox", { name: s.t("Search host, applicant or status", "搜尋主機、申請人或狀態") });
      await s.fill(search, s.t("Kevin", "陳冠宇"));
      await frame(s);
      await s.shot(1, search, band(240));
      await s.fill(search, "");
      await s.click(tab(s, "approved"), { wait: 600 });
      await frame(s);
      await s.shot(2, [
        rowExact(s, "rag-chatbot").locator('[class*="_rowIcon_"]'),
        rowExact(s, "docker-lab").locator('[class*="_rowIcon_"]'),
        row(s, spec(s, "jupyter-ml-lab", 10318)).locator('[class*="_rowIcon_"]'),
      ], band(790));
      await s.shot(3, await s.page.locator('[class*="_rowProgress_"]').all(), band(790));
    },
    // s03 Look at a request
    {
      mocks(m) {
        m.use([
          ["GET", /^\/api\/v1\/vm-requests\/5e1f0000-0000-4000-8000-000000000103\/review-context$/, () => ({
            __status: 502,
            __body: { detail: m.L("Cannot build the resource assessment: PVE connection \"Main cluster\" timed out.", "無法建立資源評估：PVE 連線「主叢集」逾時。") },
          })],
        ]);
      },
      run: async (s) => {
        await s.go("/request-review");
        await open(s, "flask-blog-lab");
        await frame(s);
        await s.shot(1, [rowWrap(s, "flask-blog-lab"), detail(s).locator('[class*="_infoGrid_"]'), box(s, "Reason / Notes", "申請原因 / 備註")], C);
        await s.shot(2, detail(s).locator('[class*="_detailHeader_"]'), C);
        await open(s, "mysql-dbms-hw");
        await frame(s);
        await s.shot(3, [detail(s).locator('[class*="_stateError_"]'), approveBtn(s)], CS);
      },
    },
    // s04 Approve a creation request
    async (s) => {
      await s.go("/request-review");
      await open(s, "cuda-yolo-train");
      await s.fill(notes(s), s.t("GPU project for the smart-campus course; the H200 has a free slot for this window.", "智慧校園課程的 GPU 專題，這個時段 H200 還有空位。"));
      await frame(s);
      await s.shot(1, rowWrap(s, "cuda-yolo-train"), C);
      await s.shot(2, notes(s), C);
      await s.shot(3, approveBtn(s), C);
      await s.shot(4, [info(s, "GPU", "GPU"), info(s, "Node / VMID", "節點 / VMID")], C);
      await s.click(approveBtn(s), { wait: 500 });
      await s.click(tab(s, "approved"), { wait: 2500 });
      await frame(s);
      await s.shot(5, row(s, "cuda-yolo-train").locator('[class*="_badge_"]'), band(300));
    },
    // s05 Approve a spec change
    async (s) => {
      await s.go("/request-review");
      const cpu = spec(s, "thesis-gpu", 10401);
      await open(s, cpu);
      await s.fill(notes(s), s.t("The thesis model needs 32 GB; Iris still has quota for it. Approved.", "論文模型需要 32 GB，配額還夠，核准。"));
      await frame(s);
      await s.shot(1, [rowWrap(s, cpu), notes(s)], C);
      await s.shot(2, approveBtn(s), C);
      await s.click(approveBtn(s), { wait: 600 });
      await s.click(tab(s, "approved"), { wait: 600 });
      await open(s, cpu);
      await toastGone(s);
      await frame(s);
      await s.shot(3, [rowWrap(s, cpu).locator('[class*="_rowProgress_"]'), box(s, "Apply Result", "套用結果")], CS);
      await s.click(tab(s, "pending"), { wait: 600 });
      const expiry = spec(s, "ubuntu-desktop", 10231);
      await open(s, expiry);
      await frame(s);
      await s.shot(4, [rowWrap(s, expiry), info(s, "Spec / Summary", "規格 / 摘要")], await downTo(rowWrap(s, expiry)));
      await open(s, spec(s, "old-web-lab", 10144));
      await frame(s);
      await s.shot(5, [decision(s).locator('[class*="_doneText_"]'), rejectBtn(s)], CS);
    },
    // s06 Reject one request
    async (s) => {
      await s.go("/request-review");
      await open(s, "ubuntu-desktop-iris");
      await s.fill(notes(s), s.t("The Operating Systems labs use class machines; please use yours from Class Workspace.", "作業系統實習使用班級機器，請到班級工作區使用你的機器。"));
      await frame(s);
      await s.shot(1, rowWrap(s, "ubuntu-desktop-iris"), C);
      await s.shot(2, notes(s), C);
      await s.shot(3, rejectBtn(s), C);
    },
    // s07 Reject several requests at once
    {
      mocks(m) {
        let n = 0;
        m.use([
          ["POST", /^\/api\/v1\/(vm|spec-change)-requests\/[^/]+\/review$/, () => (++n <= 3 ? { message: "ok" } : { __delay: 30000, __body: { message: "ok" } })],
        ]);
      },
      run: async (s) => {
        await s.go("/request-review");
        const titles = ["flask-blog-lab", "cuda-yolo-train", spec(s, "ubuntu-desktop", 10231), "mysql-dbms-hw", "ubuntu-desktop-iris", spec(s, "old-web-lab", 10144)];
        for (const t of titles) await s.click(rowWrap(s, t).locator('input[type="checkbox"]'), { wait: 150 });
        const bar = s.page.locator('[class*="_batchBar_"]');
        await frame(s);
        await s.shot(1, titles.map((t) => rowWrap(s, t).locator('[data-guide="request-review-row-select"]')), C);
        await s.shot(2, bar.getByRole("button", { name: s.t("Reject", "拒絕") }), C);
        await s.click(bar.getByRole("button", { name: s.t("Reject", "拒絕") }), { wait: 600 });
        const dlg = s.page.getByRole("alertdialog");
        await s.fill(dlg.locator("textarea"), s.t("Applications for this semester are closed. Please submit again next semester.", "本學期申請已截止，請下學期再提出。"));
        await frame(s);
        await s.shot(3, [dlg.locator("ul"), dlg.locator("textarea")]);
        await s.shot(4, dlg.getByRole("button", { name: s.t("Reject 6", "拒絕 6 筆") }));
        await dlg.getByRole("button", { name: s.t("Reject 6", "拒絕 6 筆") }).click();
        await s.page.waitForTimeout(900);
        await frame(s);
        await s.shot(4, bar.locator('[role="status"]'), { crop: { x: 340, y: 24, width: 1100, height: 300 } });
      },
    },
    // s08 Check a past decision
    async (s) => {
      await s.go("/request-review");
      await s.click(tab(s, "rejected"), { wait: 600 });
      await open(s, "minecraft-server");
      await frame(s);
      await s.shot(1, [tab(s, "rejected"), rowWrap(s, "minecraft-server")], C);
      await s.shot(2, box(s, "Review Notes", "審核備註"), C);
      await s.click(tab(s, "approved"), { wait: 600 });
      await open(s, spec(s, "ubuntu-desktop", 10231));
      await frame(s);
      await s.shot(3, box(s, "Apply Result", "套用結果"), C);
    },
  ],
};
