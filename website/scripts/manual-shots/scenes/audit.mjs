// Manual page: manual/audit.md (Audit Log, /audit, admin)
import { around, content, useAudit } from "../mocks/system.mjs";

const filters = (s) => s.page.locator('[data-guide="audit-filters"]');
const summary = (s) => s.page.locator('[data-guide="audit-summary"]');
const searchBox = (s) => s.page.getByPlaceholder(s.t("Search action details, IP...", "搜尋操作內容、IP..."));
const selects = (s) => filters(s).locator("select");
const startDate = (s) => s.page.getByLabel(s.t("Start date", "起始日期"));
const endDate = (s) => s.page.getByLabel(s.t("End date", "結束日期"));
const table = (s) => s.page.locator('[data-guide="audit-table"]');
const exportBtn = (s) => s.page.locator('[data-guide="audit-export"]');
const confirmBox = (s) => s.page.getByRole("alertdialog");

// Top of the page: header, summary, filter bar and the first table rows.
const top = (h = 560) => content(0, h);
const searchField = (s) => searchBox(s).locator("xpath=.."); // the visible field, icon included

// The table is wider than its scrolling wrapper (the IP column runs past the right
// edge): outline only the part of `loc` that is visible inside the wrapper.
const clipped = (loc, wrap) => ({
  scrollIntoViewIfNeeded: () => loc.scrollIntoViewIfNeeded(),
  boundingBox: async () => {
    const [b, w] = [await loc.boundingBox(), await wrap.boundingBox()];
    const x = Math.max(b.x, w.x);
    return { x, y: b.y, width: Math.min(b.x + b.width, w.x + w.width) - x, height: b.height };
  },
});

export default {
  page: "audit",
  role: "admin",
  mocks(m) {
    useAudit(m);
  },
  sections: [
    // s01 Read the summary for a period
    async (s) => {
      await s.go("/audit");
      await s.shot(1, await summary(s).locator("> div").all(), { crop: top(200), scroll: false });
      await s.fill(startDate(s), "2026-10-05", { wait: 500 });
      await s.fill(endDate(s), "2026-10-12", { wait: 500 });
      await endDate(s).evaluate((el) => el.blur());
      // With a date set, a Clear button wraps onto a second line of the filter bar.
      const fb = await filters(s).boundingBox();
      await s.shot(2, [startDate(s), endDate(s), summary(s)], { crop: top(Math.round(fb.y + fb.height + 14)), scroll: false, newFrame: true });
    },
    // s02 Find specific records
    async (s) => {
      await s.go("/audit");
      await s.fill(searchBox(s), "flask-blog-lab", { wait: 700 });
      await selects(s).nth(0).selectOption("snapshot_create");
      await s.settle(400);
      await selects(s).nth(1).selectOption({ label: s.t("Alex Wang（11156001@ntub.edu.tw）", "王小明（11156001@ntub.edu.tw）") });
      await s.settle(400);
      await s.fill(startDate(s), "2026-10-01", { wait: 400 });
      await s.fill(endDate(s), "2026-10-12", { wait: 500 });
      await endDate(s).evaluate((el) => el.blur());
      const clear = filters(s).getByRole("button", { name: s.t("Clear", "清除") });
      const crop = wide(await around([summary(s), table(s)], 16));
      await s.shot(1, searchField(s), { crop, scroll: false });
      await s.shot(2, selects(s).nth(0), { crop, scroll: false });
      await s.shot(3, selects(s).nth(1), { crop, scroll: false });
      await s.shot(4, [startDate(s), endDate(s)], { crop, scroll: false });
      await s.shot(5, table(s), { crop, scroll: false });
      await s.shot(6, clear, { crop, scroll: false });
    },
    // s03 Read the records
    async (s) => {
      await s.go("/audit");
      const head = table(s).locator("thead tr");
      const sysCell = table(s).locator("tbody tr").nth(1).locator("td").nth(1);
      const longCell = table(s).locator("tbody tr").nth(4).locator("td").nth(3);
      await longCell.hover();
      // Headless Chrome does not paint native title tooltips: draw one like it.
      const text = await longCell.getAttribute("title");
      const b = await longCell.boundingBox();
      await s.page.evaluate(({ text, x, y }) => {
        const d = document.createElement("div");
        d.id = "__audit_tip";
        d.textContent = text;
        d.style.cssText = `position:fixed;left:${x}px;top:${y}px;max-width:440px;padding:4px 8px;background:#fff;color:#222;` +
          "border:1px solid #767676;box-shadow:1px 2px 4px rgba(0,0,0,.25);font:12px 'Segoe UI','Microsoft JhengHei',sans-serif;z-index:99999;white-space:normal";
        document.body.appendChild(d);
      }, { text, x: b.x - 60, y: b.y + b.height + 2 });
      const tip = s.page.locator("#__audit_tip");
      // From the table's top edge down past the tooltip (the filter bar is not part of this section).
      const tb = await table(s).boundingBox();
      const tipBox = await tip.boundingBox();
      const crop = content(tb.y - 12, tipBox.y + tipBox.height + 16 - (tb.y - 12));
      await s.shot(1, clipped(head, table(s)), { crop, scroll: false });
      await s.shot(2, sysCell, { crop, scroll: false });
      await s.shot(3, [longCell, tip], { crop, scroll: false });
      await s.page.evaluate(() => document.getElementById("__audit_tip")?.remove());
      // At the end of the page the floating AI button sits on the pager's outline; it has no part here.
      await s.page.addStyleTag({ content: '[class*="_fab_"]{visibility:hidden !important}' });
      const pager = s.page.getByRole("button", { name: s.t("Next", "下一頁") }).locator("xpath=../..");
      await pager.evaluate((el) => el.scrollIntoView({ block: "center" }));
      await s.settle(200);
      await s.shot(4, pager, { crop: wide(await around([pager], 120)), scroll: false });
    },
    // s04 Export to CSV
    async (s) => {
      await s.go("/audit");
      await s.shot(1, filters(s), { crop: top(264), scroll: false });
      await s.shot(2, exportBtn(s), { crop: top(264), scroll: false });
      await s.click(exportBtn(s), { wait: 500 });
      await s.shot(3, confirmBox(s).getByRole("button", { name: s.t("Export CSV", "匯出 CSV") }));
      await confirmBox(s).getByRole("button", { name: s.t("Export CSV", "匯出 CSV") }).click();
      const toast = s.page.locator("[data-sonner-toast]").first();
      await toast.waitFor();
      await s.page.waitForTimeout(400);
      await s.shot(4, toast, { crop: top(200), scroll: false });
    },
  ],
};

function wide(r) {
  return { ...r, x: 340, width: 1100 };
}
