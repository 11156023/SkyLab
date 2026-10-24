// Manual page: manual/ai-monitoring.md
import { monitoringMocks } from "../mocks/ai.mjs";

const rangeGroup = (s) => s.page.getByRole("group", { name: s.t("Monitoring time range", "監控時間範圍") });
const detailTab = (s, key) => s.page.locator(`[data-guide-tab="ai-monitoring-${key}"]`);
const detail = (s) => s.page.locator('[data-guide="ai-monitoring-detail"]');
const search = (s) => detail(s).locator("input");

// Scrolls the call details to the top of the viewport and returns a crop of the visible part.
async function detailView(s) {
  await detail(s).evaluate((el) => el.scrollIntoView({ block: "start" }));
  await s.settle(300);
  return detail(s);
}

export default {
  page: "ai-monitoring",
  role: "admin",
  mocks(m) {
    monitoringMocks(m);
  },
  sections: [
    // s01 Choose the time range
    async (s) => {
      await s.go("/ai-monitoring", { wait: 900 });
      await s.click(rangeGroup(s).getByRole("button", { name: s.t("Last 30 days", "近 30 天") }), { wait: 900 });
      await s.shot(1, rangeGroup(s), { crop: "viewport" });
      await s.shot(2, [s.page.locator('[data-guide="ai-monitoring-metrics"]'), s.page.locator('[data-guide="ai-monitoring-trend"]'), detail(s).locator('[class*="detailHeader"] + *')], { crop: "viewport", scroll: false });
      await s.shot(3, s.page.getByText(s.t("Last updated", "最後更新")), { crop: "viewport" });
    },
    // s02 Read the summary metrics
    async (s) => {
      await s.go("/ai-monitoring", { wait: 900 });
      await s.shot(0, s.page.locator('[data-guide="ai-monitoring-metrics"]'), { crop: { x: 336, y: 0, width: 1068, height: 222 } });
    },
    // s03 Read the trend chart
    async (s) => {
      await s.go("/ai-monitoring", { wait: 900 });
      const trend = s.page.locator('[data-guide="ai-monitoring-trend"]');
      const metrics = trend.getByRole("group");
      await s.click(metrics.getByRole("button", { name: "Tokens" }), { wait: 600 });
      const chart = trend.locator(".recharts-wrapper");
      const b = await chart.boundingBox();
      await s.page.mouse.move(b.x + b.width * 0.62, b.y + b.height * 0.5);
      await s.settle(500);
      const tip = trend.locator(".recharts-tooltip-wrapper");
      await s.shot(1, metrics, { crop: trend, scroll: false });
      await s.shot(2, tip, { crop: trend, scroll: false });
    },
    // s04 Act on problems in System status
    {
      mocks(m) {
        monitoringMocks(m, { degraded: true, errorSpike: true });
      },
      run: async (s) => {
        await s.go("/ai-monitoring", { wait: 900 });
        await s.shot(0, s.page.locator('[data-guide="ai-monitoring-health"]'), { crop: s.page.locator('[data-guide="ai-monitoring-health"]').locator("xpath=..") });
      },
    },
    // s05 Check model status
    {
      mocks(m) {
        monitoringMocks(m, { degraded: true });
      },
      run: async (s) => {
        await s.page.setViewportSize({ width: 1840, height: 960 });
        await s.go("/ai-monitoring", { wait: 900 });
        const crop = await detailView(s);
        const llama = detail(s).locator("tbody tr", { hasText: "llama-3.3-70b" });
        await s.shot(1, detailTab(s, "models"), { crop, scroll: false });
        await s.shot(2, [llama.locator("td").nth(1), llama.locator("td").nth(2)], { crop, scroll: false });
        const qwen = detail(s).locator("tbody tr", { hasText: "qwen3-32b" }).getByRole("button");
        await s.shot(3, qwen, { crop, scroll: false });
        await s.click(qwen, { wait: 900 });
        await detailView(s);
        const b = await detail(s).boundingBox();
        await s.shot(3, [detailTab(s, "proxy"), search(s)], { crop: { x: b.x - 24, y: b.y - 24, width: b.width + 48, height: 450 }, scroll: false });
      },
    },
    // s06 Inspect individual calls
    async (s) => {
      // The call table has eleven columns; a wider window keeps the model names on one line.
      await s.page.setViewportSize({ width: 1840, height: 960 });
      await s.go("/ai-monitoring", { wait: 900 });
      await s.click(detailTab(s, "proxy"), { wait: 500 });
      await s.fill(search(s), "llama", { wait: 500 });
      const crop = await detailView(s);
      await s.shot(1, [detailTab(s, "proxy"), detailTab(s, "template")], { crop, scroll: false });
      await s.shot(2, detail(s).getByRole("group"), { crop, scroll: false });
      await s.shot(3, search(s), { crop, scroll: false });
      await s.shot(4, detail(s).locator("tbody tr").nth(1), { crop, scroll: false });
    },
    // s07 Find the heaviest users
    async (s) => {
      await s.go("/ai-monitoring", { wait: 900 });
      await s.click(detailTab(s, "users"), { wait: 500 });
      const crop = await detailView(s);
      const first = detail(s).locator("tbody tr").first();
      await s.shot(1, detailTab(s, "users"), { crop, scroll: false });
      await s.shot(2, [first.locator("td").nth(1), first.locator("td").nth(2), first.locator("td").nth(3), first.locator("td").nth(4)], { crop, scroll: false });
      await s.shot(3, search(s), { crop, scroll: false });
    },
    // s08 Open the Grafana dashboard
    {
      mocks(m) {
        monitoringMocks(m, { grafana: true });
      },
      run: async (s) => {
        await s.go("/ai-monitoring", { wait: 900 });
        await s.shot(0, s.page.getByRole("link", { name: s.t("View details in Grafana", "在 Grafana 查看詳細") }), { crop: { x: 336, y: 0, width: 1068, height: 222 } });
      },
    },
  ],
};
