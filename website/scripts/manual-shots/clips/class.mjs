// Demo clip: a teacher submits a class, the whole class's machines are built, and the
// class lands on the live usage heatmap.
import { CLASS_IDS, installClasses } from "../mocks/class.mjs";

const WEEKS = {
  1: { title: ["HTML structure and semantic tags", "HTML 結構與語意標籤"], status: "published" },
  2: { title: ["CSS layout with Flexbox and Grid", "CSS 版面：Flexbox 與 Grid"], status: "published" },
  3: { title: ["JavaScript basics and the DOM", "JavaScript 基礎與 DOM"], status: "published" },
};
// Machines added per progress poll (the page polls every 3 s); the class has 45 students x 2 machines.
const PER_POLL = 24;

export default {
  role: "teacher",
  mocks(m) {
    installClasses(m);
    const c = m.state.classes.web;
    c.weeks = WEEKS;
    const total = () => m.state.classObj("web").total_machines;
    // Submitted → one poll waiting for review (approved off screen) → built a batch per poll → ready.
    const progress = () => {
      if (c.status === "pending_review") {
        c.status = "provisioning";
        c.ready = 0;
      } else if (c.status === "provisioning") {
        c.ready += PER_POLL;
        if (c.ready >= total()) c.status = "active";
      }
      return m.state.classObj("web");
    };
    const R = (suffix) => new RegExp(`^/api/v1/teaching-classes/${CLASS_IDS.web}${suffix}$`);
    m.use([
      ["POST", R("/provision"), () => { c.status = "pending_review"; return { __delay: 400, __body: m.state.classObj("web") }; }],
      ["GET", R("/provision-status"), progress],
      ["POST", R("/reconcile"), progress],
    ]);
  },
  async run(s, rec) {
    await s.go(`/class-setup?classId=${CLASS_IDS.web}&step=5`, { wait: 1200 });
    await rec.start();
    await s.page.waitForTimeout(900);
    await rec.moveTo(s.page.locator('[data-guide="class-setup-summary"]'), { duration: 800 });
    await s.page.waitForTimeout(700);
    await rec.click(s.page.locator('[data-guide="class-setup-provision"]'), { wait: 600 });
    const panel = s.page.locator('[data-guide="class-overview-status"]');
    await panel.waitFor({ timeout: 10000 });
    await rec.moveTo({ x: 1150, y: 700 }, { duration: 700 });

    // Waiting for review, then the build: play the waiting part faster.
    rec.speed(2.5);
    const deadline = Date.now() + 45000;
    while (s.m.state.classes.web.status !== "active") {
      if (Date.now() > deadline) throw new Error("the class never finished building");
      await s.page.waitForTimeout(300);
    }
    rec.speed(1);
    await s.page.waitForTimeout(1500);

    // A class that is ready opens on the usage heatmap; open it if the page stayed on the overview.
    const heatmap = s.page.getByText(s.t("Student Usage Heatmap", "學生使用率熱力圖"), { exact: true });
    if (!(await heatmap.isVisible())) {
      await rec.click(s.page.getByRole("button", { name: s.t("Class Monitoring", "課堂監看"), exact: true }).first(), { wait: 400 });
    }
    await heatmap.waitFor({ timeout: 10000 });
    await s.page.waitForTimeout(1000);
    // Bring the whole grid of students into view.
    await s.page.evaluate(() => window.scrollBy({ top: 250, behavior: "smooth" }));
    await s.page.waitForTimeout(1000);
    rec.poster();
    await rec.moveTo({ x: 820, y: 520 }, { duration: 1000 });
    await rec.stop(2400);
  },
};
