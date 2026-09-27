// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import KpiCard from "./KpiCard";
import styles from "./KpiCard.module.scss";

vi.mock("react-i18next", async (original) => ({ ...await original(),
  useTranslation: () => ({ t: (key, options) => (options ? `${key}:${JSON.stringify(options)}` : key) }),
}));

let host, root;
const card = { icon: "memory", label: "CPU", value: 2, unit: "核心", caption: "目前使用" };
async function render(props) {
  await act(async () => root.render(<KpiCard {...card} {...props} />));
}
const bar = () => host.querySelector('[role="progressbar"]');

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

it("cuts the bar into the given units and leaves it whole otherwise", async () => {
  await render({ pct: 40, segments: 4 });
  expect(bar().style.getPropertyValue("--segments")).toBe("4");
  expect(bar().getAttribute("aria-valuenow")).toBe("40");
  await render({ pct: 40, segments: 0 });
  expect(bar().style.getPropertyValue("--segments")).toBe("");
});

it("holds the peak once the reading falls well below it", async () => {
  await render({ pct: 20, trackPeak: true });
  expect(host.textContent).not.toContain("OverviewTab.peak");
  await render({ pct: 80, trackPeak: true });
  await render({ pct: 30, trackPeak: true });
  expect(host.textContent).toContain('OverviewTab.peak:{"pct":80}');
  expect(bar().querySelectorAll("span")).toHaveLength(1); // the tick
  /* 關機後沒有讀數，峰值跟著清掉 */
  await render({ pct: null, trackPeak: true });
  await render({ pct: 30, trackPeak: true });
  expect(host.textContent).not.toContain("OverviewTab.peak");
});

it("does not track a peak unless asked", async () => {
  await render({ pct: 80 });
  await render({ pct: 20 });
  expect(host.textContent).not.toContain("OverviewTab.peak");
});

it("shows the live dot only for live readings and restarts it for each new sample", async () => {
  /* 沒有峰值刻痕時，圖示以外唯一 aria-hidden 的 span 就是綠點 */
  const dot = () => host.querySelector('span[aria-hidden="true"]:not(.material-icons-outlined)');
  await render({ pct: 20, live: false });
  expect(dot()).toBeNull();
  await render({ pct: 20, live: true, sample: { cpu: 0.2 } });
  const first = dot();
  await render({ pct: 21, live: true, sample: { cpu: 0.21 } });
  expect(dot()).not.toBeNull();
  expect(dot()).not.toBe(first); // remounted, so the blink plays again
});

it("turns the card to its warning look at 90%", async () => {
  await render({ pct: 89 });
  expect(host.firstElementChild.className).not.toContain(styles.danger);
  await render({ pct: 92 });
  expect(host.firstElementChild.className).toContain(styles.danger);
});

it("without a reading, shows the allocated units as empty segments rather than a 0% bar", async () => {
  await render({ pct: null, caption: "已配置", segments: 8 });
  expect(bar()).toBeNull(); // not announced as a progress bar
  const track = host.querySelector('[style*="--segments"]');
  expect(track.style.getPropertyValue("--segments")).toBe("8");
  expect(track.getAttribute("aria-hidden")).toBe("true");
  expect(track.children).toHaveLength(0); // nothing filled
  expect(host.textContent).toContain("已配置");
});

it("draws nothing at all without a reading or units to show", async () => {
  await render({ pct: null, caption: "已配置", segments: 0 });
  expect(host.querySelector('[style*="--segments"]')).toBeNull();
  expect(bar()).toBeNull();
});
