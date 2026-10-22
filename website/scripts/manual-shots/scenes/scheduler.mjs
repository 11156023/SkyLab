// Manual page: manual/scheduler.md (Resource Scheduling, /scheduler, admin)
import { useScheduler } from "../mocks/system.mjs";

// A labelled number field (label > span + input [+ hint]).
const field = (s, en, zh) => s.page.locator("label").filter({ has: s.page.getByText(s.t(en, zh), { exact: true }) }).first();
const card = (s, guide) => s.page.locator(`[data-guide="${guide}"]`);
const saveBar = (s) => s.page.locator('[data-guide="scheduler-save"]');
const saveBtn = (s) => s.page.getByRole("button", { name: s.t("Save Scheduling Settings", "儲存排程設定") });

const center = (loc) => loc.evaluate((el) => el.scrollIntoView({ block: "center" }));

// Crop rectangle covering several elements.
async function around(locs, pad = 20) {
  const bs = [];
  for (const l of locs) bs.push(await l.boundingBox());
  const x = Math.min(...bs.map((b) => b.x)) - pad;
  const y = Math.min(...bs.map((b) => b.y)) - pad;
  return { x, y, width: Math.max(...bs.map((b) => b.x + b.width)) + pad - x, height: Math.max(...bs.map((b) => b.y + b.height)) + pad - y };
}

async function setField(s, en, zh, value) {
  const f = field(s, en, zh);
  await s.fill(f.locator("input"), String(value));
  await f.locator("input").evaluate((el) => el.blur()); // no focus ring or spinner in the picture
  return f;
}

// The save bar ends the page, right where the floating AI button sits; the button
// would show up cut off at the frame's corner and has no part in these steps.
const hideFab = (s) => s.page.addStyleTag({ content: '[class*="_fab_"]{visibility:hidden !important}' });

// The save bar alone, as wide as the cards above it.
async function saveCrop(s) {
  const c = await card(s, "scheduler-practice").boundingBox();
  const r = await around([saveBar(s)], 14);
  return { ...r, x: c.x - 14, width: c.width + 28 };
}

// Card with its fields marked as step 1, then the save bar as step 2.
async function cardThenSave(s, guide, marks) {
  await hideFab(s);
  const c = card(s, guide);
  await center(c);
  await s.shot(1, marks, { crop: c, pad: 14 });
  await center(saveBar(s));
  await s.shot(2, saveBtn(s), { crop: await saveCrop(s) });
}

export default {
  page: "scheduler",
  role: "admin",
  mocks(m) {
    useScheduler(m);
  },
  sections: [
    // s01 Change placement and overcommit
    async (s) => {
      await s.go("/scheduler");
      const f = [
        await setField(s, "CPU Overcommit Ratio", "CPU 超配比", 3),
        field(s, "Disk Overcommit Ratio", "Disk 超配比"),
        field(s, "RAM Overcommit Ratio", "RAM 超配比"),
        await setField(s, "Node Reassignment Cost", "換節點成本", 0.2),
      ];
      await cardThenSave(s, "scheduler-placement", f);
    },
    // s02 Tune how busy nodes are penalized
    async (s) => {
      await s.go("/scheduler");
      await setField(s, "LoadAvg Warn / Core", "LoadAvg 警戒 / 核", 0.7);
      await setField(s, "Disk Penalty Weight", "磁碟懲罰權重", 0.9);
      await cardThenSave(s, "scheduler-thresholds", card(s, "scheduler-thresholds"));
    },
    // s03 Set up scheduled boots
    async (s) => {
      await s.go("/scheduler");
      // In on-screen order, so badge 1 sits on the card's first field.
      const lead = await setField(s, "Boot Lead Time (minutes)", "提前開機（分）", 10);
      const f = [
        await setField(s, "Boot Batch Size", "開機批次大小", 8),
        field(s, "Batch Interval (seconds)", "批次間隔（秒）"),
        lead,
        field(s, "Window Grace Period (minutes)", "時段寬限（分）"),
      ];
      await cardThenSave(s, "scheduler-boot", f);
    },
    // s04 Set practice sessions and expiry warnings (card and save bar share one screen)
    async (s) => {
      await s.go("/scheduler");
      const f = [
        await setField(s, "Practice Session (hours)", "練習時段（小時）", 2),
        field(s, "Practice Warning (minutes)", "練習提醒（分）"),
        await setField(s, "Expiry Warning (hours)", "到期提醒（小時）", 48),
      ];
      await hideFab(s);
      const c = card(s, "scheduler-practice");
      await center(c);
      const crop = await around([c, saveBar(s)], 14);
      await s.shot(1, f, { crop, scroll: false });
      await s.shot(2, saveBtn(s), { crop, scroll: false });
    },
    // s05 Discard unsaved changes
    async (s) => {
      await s.go("/scheduler");
      await setField(s, "Practice Session (hours)", "練習時段（小時）", 6);
      await hideFab(s);
      const c = card(s, "scheduler-practice");
      await center(c);
      const restore = saveBar(s).getByRole("button", { name: s.t("Restore", "還原") });
      await s.shot(1, restore, { crop: await around([c, saveBar(s)], 14), scroll: false });
      await s.click(restore);
      await s.shot(2, [field(s, "Practice Session (hours)", "練習時段（小時）"), saveBtn(s)], { crop: await around([c, saveBar(s)], 14), scroll: false, newFrame: true });
    },
  ],
};
