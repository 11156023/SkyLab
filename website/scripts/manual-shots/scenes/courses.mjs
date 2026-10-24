// Manual page: manual/courses.md (student view of My Courses, a course, a week)
import { D, H, MIN, NOW, ago, iso } from "../lib/mock.mjs";

const PATH = "p-linux";

// Monday 2026-10-12 in Taipei; times below are UTC.
function paths(m) {
  return [
    { id: PATH, title: m.L("Linux System Administration (IM 2A)", "Linux 系統管理（資管二甲）"), description: m.L("Ubuntu server administration lab", "Ubuntu 伺服器管理實作"), room_count: 5, total_questions: 8, completed_questions: 4, progress_percent: 50 },
    { id: "p-cloud", title: m.L("Cloud Computing Practice (IM 3A)", "雲端運算實務（資管三甲）"), description: "", room_count: 6, total_questions: 6, completed_questions: 6, progress_percent: 100 },
    { id: "p-web", title: m.L("Web Programming (IM 2B)", "網頁程式設計（資管二乙）"), description: "", room_count: 6, total_questions: 10, completed_questions: 2, progress_percent: 20 },
    { id: "p-db", title: m.L("Database Systems (IM 2A)", "資料庫系統（資管二甲）"), description: "", room_count: 6, total_questions: 12, completed_questions: 5, progress_percent: 42 },
    { id: "p-ai", title: m.L("Introduction to AI (General Ed.)", "人工智慧導論（通識）"), description: "", room_count: 0, total_questions: 0, completed_questions: 0, progress_percent: 0 },
  ];
}

function schedule(m) {
  const lin = m.L("Prof. Lin", "林志明");
  const chen = m.L("Prof. Chen", "陳美玲");
  return [
    { id: PATH, state: "now", session_date: "2026-10-12", start_at: "2026-10-12T01:10:00Z", end_at: "2026-10-12T04:00:00Z", location: m.L("Info Bldg B201", "資訊大樓 B201"), teacher: lin },
    { id: "p-cloud", state: "later", session_date: "2026-10-12", start_at: "2026-10-12T06:10:00Z", end_at: "2026-10-12T09:00:00Z", location: m.L("Info Bldg B302", "資訊大樓 B302"), teacher: chen },
    { id: "p-web", state: "available", session_date: "2026-10-14", start_at: "2026-10-14T05:10:00Z", end_at: "2026-10-14T08:00:00Z", location: m.L("Info Bldg B105", "資訊大樓 B105"), teacher: chen },
    { id: "p-db", state: "ended", session_date: "2026-10-12", start_at: "2026-10-12T00:10:00Z", end_at: "2026-10-12T01:50:00Z", location: m.L("Info Bldg B201", "資訊大樓 B201"), teacher: lin },
  ].map((r) => ({ ...r, title: paths(m).find((p) => p.id === r.id).title, label: "" }));
}

const WEEK_DATES = ["2026-09-14", "2026-09-21", "2026-09-28", "2026-10-05", "2026-10-12"];

function weekTitles(m) {
  return [
    m.L("Shell basics and file permissions", "Shell 基礎與檔案權限"),
    m.L("Users, groups and sudo", "使用者、群組與 sudo"),
    m.L("Package management and services", "套件管理與服務"),
    m.L("Networking and SSH hardening", "網路設定與 SSH 強化"),
    m.L("Nginx web server and firewall", "Nginx 網頁伺服器與防火牆"),
  ];
}

// Week 5 checkpoints: one per feedback status the manual lists.
function checkpoints(m) {
  const cp = (id, title, description, node, latest) => ({ id, task_id: "task-5", assignment_id: "as-5", check_available: true, title, description, detectable: "auto", machine_node_key: node, latest_check: latest });
  const item = (id, status, score, max, comment) => ({ status: "completed", score, max_score: max, summary: "", items: [{ item_id: id, status, score, max_score: max, comment }] });
  return [
    cp("c1", m.L("Install Nginx and enable it at boot", "安裝 Nginx 並設定開機自動啟動"), m.L("systemctl is-enabled nginx returns enabled", "systemctl is-enabled nginx 回傳 enabled"), "web",
      item("c1", "pass", 3, 3, m.L("Nginx is active and enabled; the default page answers on port 80.", "Nginx 已啟動並設為開機啟動，80 埠可正常回應預設頁面。"))),
    cp("c2", m.L("Serve the course site from /var/www/lab", "以 /var/www/lab 提供課程網站"), m.L("The server block points to /var/www/lab and index.html loads", "server 區塊指向 /var/www/lab，index.html 可正常載入"), "web",
      item("c2", "warning", 2, 3, m.L("The site loads, but the server block still listens on the default_server; remove the default site.", "網站可以開啟，但 server 區塊仍使用 default_server，請移除預設站台。"))),
    cp("c3", m.L("Write a short report of your changes", "撰寫設定變更說明"), m.L("Upload a short text describing what you changed", "上傳一段文字說明你做了哪些修改"), null,
      item("c3", "unknown", null, 2, m.L("The AI could not judge this item; your teacher will review it.", "AI 無法判斷這一項，將由老師確認。"))),
    cp("c4", m.L("Allow only ports 22, 80 and 443 with UFW", "以 UFW 只開放 22、80、443 埠"), m.L("ufw status lists exactly the three rules", "ufw status 只列出這三條規則"), "linux",
      item("c4", "fail", 0, 2, m.L("UFW is inactive. Run sudo ufw enable after adding the rules.", "UFW 尚未啟用，新增規則後請執行 sudo ufw enable。"))),
    cp("c5", m.L("Rotate Nginx logs daily", "每天輪替 Nginx 記錄檔"), m.L("logrotate keeps 7 daily copies", "logrotate 保留 7 份每日記錄"), "linux",
      { status: "running", score: null, max_score: 2, items: [] }),
    cp("c6", m.L("Back up the site configuration", "備份網站設定"), m.L("A tar archive of /etc/nginx exists in ~/backup", "~/backup 內有 /etc/nginx 的 tar 備份"), "linux", null),
  ];
}

function weeks(m) {
  const titles = weekTitles(m);
  const replies = {
    3: m.L("Good work on the systemd unit. Remember to check journalctl when a service fails to start.", "systemd 單元檔寫得很好，服務啟動失敗時記得先看 journalctl。"),
    5: m.L("Your Nginx setup is almost there. Enable UFW before Wednesday and I will re-check it.", "Nginx 的設定差不多了，週三前把 UFW 啟用，我會再檢查一次。"),
  };
  return WEEK_DATES.map((date, i) => ({
    id: `wk-${i + 1}`, teaching_class_id: "cls-linux", teaching_class_name: paths(m)[0].title, week_number: i + 1, session_date: date, title: titles[i],
    files: i === 4
      ? [{ id: "f-51", filename: "week05-nginx-ufw.pdf", media_type: "application/pdf" }, { id: "f-52", filename: "nginx-lab-handout.pdf", media_type: "application/pdf" }]
      : [{ id: `f-${i + 1}1`, filename: `week0${i + 1}-slides.pdf`, media_type: "application/pdf" }],
    teacher_reply: replies[i + 1] || "",
    checkpoints: i === 4 ? checkpoints(m) : [],
  }));
}

function weekItems(m) {
  const L = m.L;
  return [
    [[L("Create ~/lab01 with three nested folders", "建立 ~/lab01 與三層子資料夾"), "auto"], [L("Make notes.txt readable only by you", "讓 notes.txt 只有自己可讀"), "auto"], [L("Explain chmod 750 in your own words", "用自己的話說明 chmod 750"), "manual"]],
    [[L("Add user devops with a home directory", "新增含家目錄的使用者 devops"), "auto"], [L("Put devops in the webadmin group", "把 devops 加入 webadmin 群組"), "auto"], [L("Allow webadmin to restart nginx with sudo", "讓 webadmin 可以用 sudo 重新啟動 nginx"), "partial"], [L("Lock the account of user guest", "鎖定 guest 帳號"), "auto"]],
    [[L("Install htop and tree with apt", "用 apt 安裝 htop 與 tree"), "auto"], [L("Write a systemd unit for the hello service", "撰寫 hello 服務的 systemd 單元檔"), "partial"], [L("Enable the service at boot", "設定服務開機自動啟動"), "auto"]],
    [[L("Give the host a static IP with netplan", "用 netplan 設定固定 IP"), "auto"], [L("Disable SSH password login", "停用 SSH 密碼登入"), "auto"], [L("Log in with your own SSH key", "以自己的 SSH 金鑰登入"), "partial"], [L("Change the SSH port and document why", "修改 SSH 埠並說明原因"), "manual"]],
    checkpoints(m).map((c, k) => [c.title, k === 2 ? "manual" : k === 1 ? "partial" : "auto"]),
  ];
}

function assignments(m, done) {
  const titles = weekTitles(m);
  const rows = WEEK_DATES.map((date, i) => ({
    id: `as-${i + 1}`, teaching_class_id: "cls-linux", teaching_class_week_id: `wk-${i + 1}`, title: titles[i], summary: "",
    approved_at: `${date}T00:30:00Z`, check_available: true,
    items: weekItems(m)[i].map(([title, detectable], k) => ({ id: `as-${i + 1}-i${k}`, title, description: "", detectable, order: k })),
    completion: { completed: done.has(`as-${i + 1}`), completed_item_ids: [], ready_at: null },
  }));
  rows.push({
    id: "as-mid", teaching_class_id: "cls-linux", teaching_class_week_id: null,
    title: m.L("Midterm project: deploy a LAMP stack", "期中專題：部署 LAMP 環境"),
    summary: m.L("Build a working LAMP server on your practice host: Apache serves a PHP page that reads from MariaDB, and the database is not reachable from outside.",
      "在你的實作主機上架好可運作的 LAMP：Apache 提供能讀取 MariaDB 的 PHP 頁面，且資料庫不可從外部連線。"),
    approved_at: "2026-10-08T01:00:00Z", check_available: true,
    items: [
      { id: "mid-1", title: m.L("Apache answers on port 80", "Apache 在 80 埠提供服務"), description: m.L("curl http://localhost returns your index page", "curl http://localhost 會回傳你的首頁"), detectable: "auto", order: 0 },
      { id: "mid-2", title: m.L("PHP page reads rows from MariaDB", "PHP 頁面能讀出 MariaDB 的資料"), description: m.L("info.php lists the students table", "info.php 列出 students 資料表"), detectable: "partial", order: 1 },
      { id: "mid-3", title: m.L("Explain your security choices", "說明你的安全性設定"), description: m.L("Write why MariaDB only listens on 127.0.0.1", "寫下為什麼 MariaDB 只監聽 127.0.0.1"), detectable: "manual", order: 2 },
    ],
    completion: { completed: false, completed_item_ids: [], ready_at: null },
  });
  return rows;
}

function practiceMachines(m) {
  return [
    { machine_node_id: "mn-1", node_key: "web", name: "web-server", role: m.L("Web server", "網頁伺服器"), resource_type: "qemu", vmid: 30121,
      public_url: "https://web-8c2f41d09e7a4b13.skylab.ntub.edu.tw", forward_endpoints: [{ host: "gw.skylab.ntub.edu.tw", external_port: 40121, internal_port: 22, protocol: "tcp" }] },
    { machine_node_id: "mn-2", node_key: "linux", name: "linux-lab", role: m.L("Practice host", "實作主機"), resource_type: "lxc", vmid: 30122,
      public_url: null, forward_endpoints: [{ host: null, external_port: 40122, internal_port: 22, protocol: "tcp" }] },
    { machine_node_id: "mn-3", node_key: "db", name: "db-server", role: m.L("Database", "資料庫"), resource_type: "qemu", vmid: 30123, public_url: null, forward_endpoints: [] },
    { machine_node_id: "mn-4", node_key: "monitor", name: "monitor", role: m.L("Monitoring", "監控主機"), resource_type: "lxc", vmid: null, public_url: null, forward_endpoints: [] },
  ];
}

function resource(m, vmid, status) {
  const pm = practiceMachines(m).find((x) => x.vmid === vmid);
  return {
    vmid, name: pm.name, type: pm.resource_type === "lxc" ? "lxc" : "qemu", status, node: "pve-02", machine_kind: "teaching_class", class_relation: "student",
    teaching_class_id: "cls-linux", teaching_class_name: paths(m)[0].title, owner_name: m.L("Alex Wang", "王小明"), owner_email: "11156001@ntub.edu.tw",
    os_info: pm.resource_type === "lxc" ? "Ubuntu 24.04 (LXC)" : "Ubuntu 24.04 LTS", ip_address: `10.20.8.${vmid - 30100}`,
    uptime: status === "running" ? 3 * H : 0, cpu: status === "running" ? 0.04 : 0, mem: status === "running" ? 0.6 * 1024 ** 3 : 0, maxmem: 4 * 1024 ** 3,
    expiry_date: iso(NOW + 80 * D), can_control: true, can_manage: false, can_delete: false, public_urls: [], allocation_scope: "class", is_placeholder: false,
  };
}

// Fake terminal for the LXC practice host.
function termServer(ws) {
  const PROMPT = "\x1b[1;32mstudent@linux-lab\x1b[0m:\x1b[1;34m~\x1b[0m$ ";
  ws.send("OK" + "Welcome to Ubuntu 24.04.1 LTS (SkyLab LXC · linux-lab)\r\n\r\n * Documentation:  https://help.ubuntu.com\r\n\r\nLast login: Mon Oct 12 09:12:44 2026 from 10.20.0.5\r\n" + PROMPT);
  ws.onMessage(() => {});
}

// The elements of `loc` that lie fully inside the viewport.
async function inView(s, loc) {
  const vp = s.page.viewportSize();
  const out = [];
  for (const el of await loc.all()) {
    const b = await el.boundingBox();
    if (b && b.y >= 0 && b.y + b.height <= vp.height) out.push(el);
  }
  return out;
}

const scrollTo = (s, y) => s.page.evaluate((y) => window.scrollTo(0, y), y);

export default {
  page: "courses",
  role: "student",
  mocks(m) {
    m.state.done = new Set(["as-1", "as-2", "as-3"]);
    m.state.status = { 30121: "running", 30122: "stopped", 30123: "starting" };
    m.use([
      ["GET", /^\/api\/v1\/courses\/paths$/, () => paths(m)],
      ["GET", /^\/api\/v1\/courses\/schedule$/, () => schedule(m)],
      ["GET", /^\/api\/v1\/courses\/paths\/p-linux$/, () => ({ id: PATH, title: paths(m)[0].title, description: paths(m)[0].description, rooms: [] })],
      ["GET", /^\/api\/v1\/courses\/paths\/p-linux\/ai-assignments$/, () => assignments(m, m.state.done)],
      ["GET", /^\/api\/v1\/courses\/paths\/p-linux\/weekly-tasks$/, () => weeks(m)],
      ["GET", /^\/api\/v1\/courses\/paths\/p-linux\/practice-machines$/, () => practiceMachines(m)],
      ["PUT", /^\/api\/v1\/courses\/paths\/p-linux\/ai-assignments\/([^/]+)\/completion$/, ({ mt, body }) => {
        if (body?.completed) m.state.done.add(mt[1]); else m.state.done.delete(mt[1]);
        return { __delay: 200, __body: { completed: Boolean(body?.completed), completed_item_ids: [], ready_at: body?.completed ? iso(NOW) : null } };
      }],
      ["GET", /^\/api\/v1\/resources\/my$/, () => [30121, 30122, 30123].map((v) => resource(m, v, m.state.status[v]))],
      // Asked for alongside "my resources"; this student has no quick-practice sessions.
      ["GET", /^\/api\/v1\/quick-practice\/sessions\/my$/, () => []],
      ["GET", /^\/api\/v1\/resources\/(\d+)$/, ({ mt }) => resource(m, Number(mt[1]), m.state.status[Number(mt[1])])],
      ["POST", /^\/api\/v1\/resources\/(\d+)\/start$/, ({ mt }) => { m.state.status[Number(mt[1])] = "running"; return { __delay: 400, __body: { message: "ok" } }; }],
    ]);
    m.ws = (p, ws) => { if (/^\/ws\/terminal\//.test(p)) termServer(ws); };
  },
  sections: [
    // s01 Find your course
    async (s) => {
      await s.go("/courses", { wait: 800 });
      const crop = { x: 40, y: 0, width: 1400, height: 580 };
      const nav = s.page.getByRole("button", { name: s.t("Courses", "課程"), exact: true });
      await s.shot(1, [nav, s.page.locator("h1").first()], { crop });
      const ticket = s.page.locator('[data-guide="course-card"]').first();
      const parts = ["status", "title", "fields", "stub"].map((c) => ticket.locator(`[class*="_${c}_"]`).first());
      await s.shot(2, [...parts, s.page.locator('[data-guide="course-card"]').last().locator('[class*="_stub_"]')], { crop });
      await s.shot(3, s.page.locator('[data-guide="course-card"]').nth(1), { crop });
    },
    // s02 Read the course page
    async (s) => {
      await s.go(`/courses/${PATH}`, { wait: 800 });
      await s.shot(1, s.page.locator('[data-guide="home-tasks"]'));
      await s.shot(2, s.page.locator('[data-guide="home-current-course"]'));
      await s.shot(3, s.page.locator('[data-guide="home-start"]'));
      await s.shot(4, s.page.getByRole("button", { name: s.t("Back to My Courses", "返回我的課程") }));
    },
    // s03 Mark a week as done
    async (s) => {
      await s.go(`/courses/${PATH}`, { wait: 800 });
      const box = s.page.locator("#weekly-task-week-wk-4").locator('label[class*="weekCompletion"]');
      await s.click(box.locator("input"), { wait: 300 });
      const toast = s.page.locator("[data-sonner-toast]").first();
      await toast.waitFor();
      await s.page.mouse.move(1300, 880);
      await s.page.evaluate(() => document.activeElement?.blur());
      await s.page.waitForTimeout(900);
      await s.shot(1, box, { scroll: false });
      await s.shot(2, toast, { scroll: false });
    },
    // s04 Read an AI task's requirements
    async (s) => {
      await s.go(`/courses/${PATH}`, { wait: 800 });
      const row = s.page.locator("#weekly-task-assignment-as-mid");
      const toggle = row.locator("button[aria-controls]");
      await s.click(toggle, { wait: 500 });
      await s.page.mouse.move(1300, 880);
      await row.evaluate((el) => window.scrollBy(0, el.getBoundingClientRect().top - 90));
      await s.settle(300);
      await s.shot(1, toggle, { scroll: false });
      await s.shot(2, [row.locator('[class*="aiBrief"]'), row.locator("ol")], { scroll: false });
      await s.shot(3, await row.locator('[class*="aiCheckBadge"]').all(), { scroll: false });
    },
    // s05 Enter a class machine
    async (s) => {
      await s.go(`/courses/${PATH}`, { wait: 800 });
      const panel = s.page.locator('[data-guide="home-start"]');
      await s.shot(1, await panel.locator('[class*="machineState"]').all(), { crop: panel });
      // Step 2: the stopped LXC is started, then its terminal opens.
      await s.click(panel.locator('button[class*="machineLaunch"]').nth(1), { wait: 1500 });
      const dialog = s.page.getByRole("dialog").first();
      await dialog.waitFor();
      await s.page.getByText("Last login").waitFor();
      // Let the "machine started" toast fade so it does not cover the terminal header.
      await s.page.locator("[data-sonner-toast]").first().waitFor({ state: "detached", timeout: 9000 }).catch(() => {});
      await s.settle(600);
      const db = await dialog.boundingBox();
      await s.shot(2, dialog.locator('[class*="terminalArea"]'), { crop: { x: db.x - 12, y: db.y - 12, width: db.width + 24, height: 330 } });
      await s.page.keyboard.press("Escape");
      await s.settle(400);
      if (await dialog.isVisible().catch(() => false)) {
        await s.page.locator('[role="dialog"] button[aria-label]').last().click().catch(() => {});
        await s.settle(400);
      }
      // Step 3: a machine that never finishes booting. The boot wait polls every
      // 2 s for ~90 s; shorten those pauses while it runs.
      await s.page.evaluate(() => {
        const orig = window.setTimeout;
        window.__origSetTimeout = orig;
        window.setTimeout = (fn, ms, ...rest) => orig(fn, ms === 2000 ? 20 : ms, ...rest);
      });
      await s.click(panel.locator('button[class*="machineLaunch"]').nth(2), { wait: 200 });
      await s.page.getByText(s.t("The machine is still starting up. Please try again shortly.", "機器仍在啟動中，請稍後再試。")).waitFor({ timeout: 15000 });
      await s.page.evaluate(() => { window.setTimeout = window.__origSetTimeout; });
      await s.page.mouse.move(1300, 880);
      await s.page.waitForTimeout(700);
      await s.shot(3, [s.page.locator("[data-sonner-toast]").first(), panel.locator('button[class*="machineLaunch"]').nth(2)], { scroll: false });
      await s.shot(4, panel.locator('button[class*="iconBtn"]').first(), { scroll: false });
      await s.page.goto("about:blank");
    },
    // s06 Open a week
    async (s) => {
      await s.go(`/courses/${PATH}`, { wait: 800 });
      const row = s.page.locator("#weekly-task-week-wk-5").locator("button[aria-controls]");
      const open = s.page.getByRole("button", { name: s.t("Open this week", "查看本週") });
      await s.shot(1, [row, open], { scroll: false });
      await s.click(open, { wait: 800 });
      await s.shot(2, [s.page.locator('[data-guide="course-week-header"] > *').first(), s.page.getByRole("button", { name: s.t("Back to course", "返回課程") })], { min: [1100, 320] });
    },
    // s07 Read check feedback
    async (s) => {
      await s.go(`/courses/${PATH}/weeks/wk-5`, { wait: 800 });
      const cards = s.page.locator('[data-guide="course-week-feedback"] article[class*="feedbackCard"]');
      // Let the machine panel and feedback finish loading (the layout shifts until then), then go to the top.
      await cards.first().waitFor();
      await s.page.locator('[data-guide="course-week-machine"]').waitFor();
      // The header back button comes with the course path, a little after the week itself.
      await s.page.locator("button", { hasText: s.t("Back to course", "返回課程") }).waitFor();
      await s.settle(600);
      await scrollTo(s, 0);
      await s.settle(300);
      await s.shot(1, s.page.locator('[data-guide="course-week-teacher-reply"]'), { scroll: false });
      await s.shot(2, [cards.first().locator('[class*="feedbackTop"] > div'), cards.first().locator('[class*="checkpointMachine"]')], { scroll: false });
      await s.shot(3, cards.first().locator('[class*="feedbackStatus"]'), { scroll: false });
      await s.shot(4, cards.first().locator('[class*="feedbackBody"]'), { scroll: false });
      // Step 3: scroll down to see the statuses of the other checkpoints.
      await cards.nth(1).evaluate((el) => window.scrollBy(0, el.getBoundingClientRect().top - 12));
      await s.settle(300);
      await s.shot(3, await inView(s, s.page.locator('[data-guide="course-week-feedback"] [class*="feedbackStatus"]')), { scroll: false, crop: s.page.locator('[data-guide="course-week-feedback"]') });
    },
    // s08 Use the machines and materials of a week
    async (s) => {
      await s.go(`/courses/${PATH}/weeks/wk-5`, { wait: 800 });
      const mp = s.page.locator('[data-guide="course-week-machine"]');
      await mp.evaluate((el) => window.scrollBy(0, el.getBoundingClientRect().top - 12));
      await s.settle(300);
      await s.shot(1, (await inView(s, mp.locator('a, code, [class*="urlPending"]'))).concat(await inView(s, mp.locator("button").first())), { scroll: false, crop: mp });
      const mat = s.page.locator('[data-guide="course-week-materials"]');
      await mat.evaluate((el) => el.scrollIntoView({ block: "center" }));
      await s.settle(300);
      await s.shot(2, await mat.locator("button").all(), { scroll: false, crop: mat });
    },
  ],
};
