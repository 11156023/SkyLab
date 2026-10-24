// Manual page: manual/course-cms.md
import { D, H, ago } from "../lib/mock.mjs";

const uid = (n) => `5b1e9c40-0000-4c00-8000-${String(n).padStart(12, "0")}`;
const CLASS = { linux: uid(1), web: uid(2), sec: uid(3), cloud: uid(4) };
const PATH = { linux: uid(11), web: uid(12), sec: uid(13) };
const ROOM = { cmd: uid(21), perm: uid(22), shell: uid(23), html: uid(24), flask: uid(25), recon: uid(26) };
const TASK = { fs: uid(31), cmds: uid(32), grep: uid(33) };

function classes(m) {
  return [
    { id: CLASS.linux, name: m.L("Linux System Administration", "Linux 系統管理"), term: "115-1" },
    { id: CLASS.web, name: m.L("Web Programming", "網頁程式設計"), term: "115-1" },
    { id: CLASS.sec, name: m.L("Introduction to Information Security", "資訊安全導論"), term: "115-1" },
    { id: CLASS.cloud, name: m.L("Cloud Computing Practice", "雲端運算實務"), term: "115-1" },
  ];
}

function seed(m) {
  const cls = classes(m);
  const path = (id, title, status, classIdx, room_count, created) => ({
    id, title, description: null, status, created_by: m.users.teacher.id,
    created_at: ago(created), updated_at: ago(2 * D), room_count,
    teaching_class_id: cls[classIdx].id, teaching_class_name: cls[classIdx].name,
  });
  const room = (id, path_id, title, difficulty, order, task_count) => ({ id, path_id, title, description: null, difficulty, category: null, order, task_count });
  const task = (id, room_id, title, order, content) => ({ id, room_id, title, order, content });
  const q = (id, task_id, prompt, question_type, points, order) => ({ id, task_id, prompt, question_type, points, order });
  return {
    paths: [
      path(PATH.linux, m.L("Linux System Administration Lab", "Linux 系統管理實作"), "published", 0, 3, 40 * D),
      path(PATH.web, m.L("Web Programming: From HTML to Flask", "網頁程式設計：從 HTML 到 Flask"), "draft", 1, 2, 35 * D),
      path(PATH.sec, m.L("Hands-on Security Basics", "資安攻防入門"), "published", 2, 1, 30 * D),
    ],
    rooms: [
      room(ROOM.cmd, PATH.linux, m.L("Basic Linux Commands", "Linux 基本指令"), "easy", 0, 3),
      room(ROOM.perm, PATH.linux, m.L("Users and Permissions", "使用者與權限管理"), "medium", 1, 2),
      room(ROOM.shell, PATH.linux, m.L("Shell Script Automation", "Shell Script 自動化"), "hard", 2, 2),
      room(ROOM.html, PATH.web, m.L("HTML and CSS Basics", "HTML 與 CSS 基礎"), "easy", 0, 2),
      room(ROOM.flask, PATH.web, m.L("Building a Flask App", "Flask 應用程式開發"), "medium", 1, 3),
      room(ROOM.recon, PATH.sec, m.L("Reconnaissance with Nmap", "使用 Nmap 偵察"), "easy", 0, 2),
    ],
    tasks: [
      task(TASK.fs, ROOM.cmd, m.L("Explore the file system", "認識檔案系統"), 0, m.L(
        "## The Linux directory tree\n\nEverything starts at `/`. Use `pwd`, `ls -l` and `cd` to look around.\n\n- `/etc` holds configuration files\n- `/home` holds user folders\n- `/var/log` holds log files\n\nOpen your lab machine's terminal and find out which directory you start in.",
        "## Linux 目錄結構\n\n所有檔案都從 `/` 開始。用 `pwd`、`ls -l`、`cd` 四處看看。\n\n- `/etc` 放設定檔\n- `/home` 放使用者資料夾\n- `/var/log` 放日誌檔\n\n打開實驗機的終端機，找出你一登入時所在的目錄。")),
      task(TASK.cmds, ROOM.cmd, m.L("Practise everyday commands", "常用指令練習"), 1, m.L(
        "## Copy, move and remove\n\nTry `cp`, `mv`, `rm` and `mkdir -p` in your home folder.\n\n```bash\nmkdir -p ~/lab1/notes\ncp /etc/hostname ~/lab1/\n```",
        "## 複製、搬移與刪除\n\n在自己的家目錄練習 `cp`、`mv`、`rm` 與 `mkdir -p`。\n\n```bash\nmkdir -p ~/lab1/notes\ncp /etc/hostname ~/lab1/\n```")),
      task(TASK.grep, ROOM.cmd, m.L("Text tools: grep and sed", "文字處理工具 grep／sed"), 2, ""),
    ],
    questions: [
      q(uid(41), TASK.fs, m.L("Which directory holds the system's configuration files?", "系統設定檔集中放在哪個目錄？"), "flag", 10, 0),
      q(uid(42), TASK.fs, m.L("Run hostname on your lab machine and enter the result.", "在實驗機執行 hostname，輸入顯示的結果。"), "flag", 20, 1),
      q(uid(43), TASK.fs, m.L("Read the Filesystem Hierarchy Standard summary.", "閱讀 FHS 檔案系統階層標準摘要。"), "no_answer", 0, 2),
    ],
  };
}

function progress(m, pathId) {
  if (pathId !== PATH.linux) return { path_id: pathId, total_questions: 6, students: [] };
  const rooms = [
    [ROOM.cmd, m.L("Basic Linux Commands", "Linux 基本指令"), 6],
    [ROOM.perm, m.L("Users and Permissions", "使用者與權限管理"), 5],
    [ROOM.shell, m.L("Shell Script Automation", "Shell Script 自動化"), 4],
  ];
  const ppl = [
    [m.users.student, [6, 5, 3]],
    ["7d3c1a2e-0100-4a00-9000-0000000000aa", "11256003@ntub.edu.tw", m.L("Emily Chang", "張雅婷"), [6, 4, 1]],
    ["7d3c1a2e-0101-4a00-9000-0000000000aa", "11156012@ntub.edu.tw", m.L("Kevin Chen", "陳冠宇"), [6, 2, 0]],
    ["7d3c1a2e-0102-4a00-9000-0000000000aa", "11256021@ntub.edu.tw", m.L("Grace Lee", "李佳穎"), [5, 0, 0]],
    ["7d3c1a2e-0103-4a00-9000-0000000000aa", "11156034@ntub.edu.tw", m.L("Brian Huang", "黃柏翰"), [3, 0, 0]],
    ["7d3c1a2e-0104-4a00-9000-0000000000aa", "11256045@ntub.edu.tw", m.L("Iris Wu", "吳欣怡"), [6, 5, 4]],
  ].map((p) => (Array.isArray(p) && p.length === 2 ? [p[0].id, p[0].email, p[0].full_name, p[1]] : p));
  const total = rooms.reduce((a, r) => a + r[2], 0);
  return {
    path_id: pathId,
    total_questions: total,
    students: ppl.map(([user_id, user_email, user_name, done]) => {
      const completed = done.reduce((a, b) => a + b, 0);
      return {
        user_id, user_email, user_name, total_questions: total, completed_questions: completed,
        progress_percent: Math.round((completed / total) * 1000) / 10,
        rooms: rooms.map(([room_id, room_title, n], i) => ({
          room_id, room_title, total_questions: n, completed_questions: done[i],
          progress_percent: Math.round((done[i] / n) * 1000) / 10,
        })),
      };
    }),
  };
}

let seq = 100;
const A = "^\\/api\\/v1\\/admin\\/courses";
const re = (s) => new RegExp(A + s + "$");

export default {
  page: "course-cms",
  role: "teacher",
  mocks(m) {
    const st = seed(m);
    const cls = classes(m);
    m.state.cms = st;
    m.use([
      ["GET", /^\/api\/v1\/teaching-classes$/, () => cls],
      ["GET", re("\\/paths"), () => st.paths],
      ["POST", re("\\/paths"), ({ body }) => {
        const c = cls.find((x) => x.id === body.teaching_class_id);
        const p = { id: uid(++seq), title: body.title, description: null, status: "draft", created_by: m.users.teacher.id, created_at: ago(0), updated_at: ago(0), room_count: 0, teaching_class_id: c?.id ?? null, teaching_class_name: c?.name ?? null };
        st.paths.push(p);
        return p;
      }],
      ["GET", re("\\/paths\\/([^/]+)\\/progress"), ({ mt }) => progress(m, mt[1])],
      ["GET", re("\\/paths\\/([^/]+)\\/rooms"), ({ mt }) => st.rooms.filter((r) => r.path_id === mt[1]).map((r) => ({ ...r, task_count: st.tasks.filter((t) => t.room_id === r.id).length || r.task_count }))],
      ["PUT", re("\\/paths\\/([^/]+)\\/publish"), ({ mt, body }) => { const p = st.paths.find((x) => x.id === mt[1]); p.status = body.published ? "published" : "draft"; return p; }],
      ["PUT", re("\\/paths\\/([^/]+)"), ({ mt, body }) => { const p = st.paths.find((x) => x.id === mt[1]); Object.assign(p, body); return p; }],
      ["DELETE", re("\\/paths\\/([^/]+)"), () => ({ message: "ok" })],
      ["POST", re("\\/rooms"), ({ body }) => { const r = { id: uid(++seq), description: null, category: null, task_count: 0, ...body }; st.rooms.push(r); return r; }],
      ["PUT", re("\\/rooms\\/([^/]+)"), ({ mt, body }) => { const r = st.rooms.find((x) => x.id === mt[1]); Object.assign(r, body); return r; }],
      ["DELETE", re("\\/rooms\\/([^/]+)"), () => ({ message: "ok" })],
      ["GET", re("\\/rooms\\/([^/]+)\\/tasks"), ({ mt }) => st.tasks.filter((t) => t.room_id === mt[1])],
      ["POST", re("\\/tasks"), ({ body }) => { const t = { id: uid(++seq), ...body }; st.tasks.push(t); return t; }],
      ["PUT", re("\\/tasks\\/([^/]+)"), ({ mt, body }) => { const t = st.tasks.find((x) => x.id === mt[1]); Object.assign(t, body); return t; }],
      ["DELETE", re("\\/tasks\\/([^/]+)"), () => ({ message: "ok" })],
      ["GET", re("\\/tasks\\/([^/]+)\\/questions"), ({ mt }) => st.questions.filter((q) => q.task_id === mt[1])],
      ["POST", re("\\/questions"), ({ body }) => { const q = { id: uid(++seq), task_id: body.task_id, prompt: body.prompt, question_type: body.question_type, points: body.points, order: body.order }; st.questions.push(q); return q; }],
      ["PUT", re("\\/questions\\/([^/]+)"), ({ mt, body }) => { const q = st.questions.find((x) => x.id === mt[1]); Object.assign(q, body); return q; }],
      ["DELETE", re("\\/questions\\/([^/]+)"), () => ({ message: "ok" })],
    ]);
  },
  sections: [
    // s01 Find your way around
    async (s) => {
      await s.go("/course-cms");
      const tabs = [s.page.locator('[data-guide-tab="course-cms-tab-editor"]'), s.page.locator('[data-guide-tab="course-cms-tab-progress"]')];
      await openPath(s, PATH.linux);
      const crop = await main(s);
      await s.shot(1, tabs, { crop });
      await s.shot(2, [s.page.locator('[data-guide="course-cms-tree"]'), s.page.locator('[data-guide="course-cms-workspace"]')], { crop });
      const toggle = toggleBtn(s, m(s).rooms[0].title);
      await s.click(toggle);
      await s.shot(3, [toggle, row(s, m(s).tasks[0].title).getByRole("button").last()], { crop });
    },
    // s02 Add a learning path
    async (s) => {
      await s.go("/course-cms");
      const add = s.page.locator('[data-guide="course-cms-add-path"]');
      await s.shot(1, add, { crop: await main(s, 420) });
      await s.click(add);
      const dlg = s.page.getByRole("dialog");
      const sel = dlg.locator("select");
      await sel.selectOption(CLASS.cloud);
      await listbox(sel, true);
      await s.fill(dlg.locator("input"), s.t("Containers and Kubernetes", "雲端運算實務：容器與 Kubernetes"));
      await s.shot(2, sel);
      await s.shot(3, dlg.locator("input"));
      await s.shot(4, dlg.getByRole("button", { name: s.t("Create", "建立"), exact: true }));
    },
    // s03 Edit a learning path
    async (s) => {
      await s.go("/course-cms");
      await openPath(s, PATH.linux);
      const crop = await main(s, 640);
      const ws = s.page.locator('[data-guide="course-cms-workspace"]');
      const activeRow = s.page.locator('[aria-current="true"]');
      const title = ws.getByRole("textbox").first();
      await s.fill(title, s.t("Linux System Administration Lab (Fall 2026)", "Linux 系統管理實作（115 學年度上學期）"));
      const sel = ws.locator("select");
      await listbox(sel, true);
      await s.settle(150);
      await s.shot(1, [activeRow, ws.locator("p").first()], { crop });
      await s.shot(2, [title, ws.getByRole("button", { name: s.t("Save", "儲存"), exact: true })], { crop });
      await s.shot(3, [ws.getByText(s.t("Published, visible to students", "已發布，學生看得到")), ws.getByRole("button", { name: s.t("Unpublish", "下架"), exact: true })], { crop });
      await s.shot(4, sel, { crop });
      await listbox(sel, false);
      await s.click(ws.getByRole("button", { name: s.t("Delete path", "刪除路徑") }));
      await s.shot(5, s.page.getByRole("alertdialog").getByRole("button", { name: s.t("Delete", "刪除"), exact: true }));
    },
    // s04 Add and edit rooms
    async (s) => {
      await s.go("/course-cms");
      await s.click(toggleBtn(s, m(s).paths[0].title), { wait: 500 });
      await s.click(row(s, m(s).rooms[1].title).getByRole("button").last(), { wait: 600 });
      const crop = await main(s, 480);
      const addRoom = s.page.getByRole("button", { name: s.t("Add room", "新增房間") });
      await s.click(addRoom);
      const input = s.page.getByRole("textbox", { name: s.t("New room title", "新房間標題") });
      await s.fill(input, s.t("Network Services", "網路服務設定"));
      const ws = s.page.locator('[data-guide="course-cms-workspace"]');
      const name = ws.getByRole("textbox").first();
      await s.fill(name, s.t("Users, Groups and Permissions", "使用者、群組與權限管理"));
      const diff = ws.getByRole("group", { name: s.t("Difficulty", "難度") });
      await s.click(diff.getByRole("button", { name: s.t("Hard", "困難") }));
      // Step 1: the "Add room" row (now showing the input) at the bottom of the rooms.
      await s.shot(1, s.page.locator("nav form").first(), { crop });
      await s.shot(2, s.page.getByRole("button", { name: s.t("Add", "新增"), exact: true }), { crop });
      await s.shot(3, [name, diff, ws.getByRole("button", { name: s.t("Save", "儲存"), exact: true })], { crop });
      await s.click(ws.getByRole("button", { name: s.t("Delete room", "刪除房間") }));
      await s.shot(4, s.page.getByRole("alertdialog").getByRole("button", { name: s.t("Delete", "刪除"), exact: true }));
    },
    // s05 Add and edit tasks
    async (s) => {
      await s.go("/course-cms");
      await s.click(toggleBtn(s, m(s).paths[0].title), { wait: 500 });
      await s.click(toggleBtn(s, m(s).rooms[0].title), { wait: 500 });
      await s.click(row(s, m(s).tasks[1].title).getByRole("button").last(), { wait: 600 });
      const crop = await main(s, 740);
      await s.click(s.page.getByRole("button", { name: s.t("Add task", "新增任務") }));
      const input = s.page.getByRole("textbox", { name: s.t("New task title", "新任務標題") });
      await s.fill(input, s.t("Find files with find", "用 find 搜尋檔案"));
      const ws = s.page.locator('[data-guide="course-cms-workspace"]');
      const title = ws.getByRole("textbox").first();
      const content = ws.locator("textarea");
      await content.fill(m(s).tasks[1].content + s.t("\n\nWhen you are done, answer the questions below.", "\n\n完成後回答下方題目。"));
      await s.settle(150);
      await s.shot(1, s.page.locator("nav form").first(), { crop });
      await s.shot(2, [title, content], { crop });
      await s.shot(3, ws.getByRole("button", { name: s.t("Save task", "儲存任務") }), { crop });
      await s.click(ws.getByRole("button", { name: s.t("Delete task", "刪除任務") }));
      await s.shot(4, s.page.getByRole("alertdialog").getByRole("button", { name: s.t("Delete", "刪除"), exact: true }));
    },
    // s06 Add questions to a task
    async (s) => {
      await s.go("/course-cms");
      await s.click(toggleBtn(s, m(s).paths[0].title), { wait: 500 });
      await s.click(toggleBtn(s, m(s).rooms[0].title), { wait: 500 });
      await s.click(row(s, m(s).tasks[0].title).getByRole("button").last(), { wait: 600 });
      const ws = s.page.locator('[data-guide="course-cms-workspace"]');
      const heading = ws.getByRole("heading", { name: s.t("Questions", "題目") });
      const addQ = ws.getByRole("button", { name: s.t("Add question", "新增題目") });
      const full = await main(s);
      await heading.scrollIntoViewIfNeeded();
      const hb = await heading.boundingBox();
      const qcrop = { ...full, y: Math.max(0, Math.round(hb.y - 330)), height: 560 };
      const editQ = ws.getByRole("button", { name: s.t("Edit question “", "編輯題目「") + m(s).questions[1].prompt + s.t("”", "」") });
      const trash = ws.getByRole("button", { name: s.t("Delete", "刪除"), exact: true }).nth(1);
      await s.shot(1, heading, { crop: qcrop, scroll: false });
      await s.shot(2, addQ, { crop: qcrop, scroll: false });
      // Three frames at most: the list also carries 7 (click a question) and 8 (its trash icon);
      // the delete confirmation is a plain yes/no dialog and is not pictured.
      await s.shot(7, editQ, { crop: qcrop, scroll: false });
      await s.shot(8, trash, { crop: qcrop, scroll: false });
      await s.click(addQ, { wait: 500 });
      const dlg = s.page.getByRole("dialog");
      await s.fill(dlg.locator("textarea"), s.t("Which command shows the full path of the current directory?", "哪個指令可以顯示目前所在目錄的完整路徑？"));
      const answer = dlg.getByRole("textbox", { name: s.t("Correct answer", "正確答案") });
      await s.fill(answer, "pwd");
      const pts = dlg.getByRole("spinbutton");
      await s.fill(pts, "15");
      await s.shot(3, dlg.getByRole("group", { name: s.t("Question type", "題型") }));
      await s.shot(4, dlg.locator("textarea"));
      await s.shot(5, answer);
      await s.shot(6, [pts, dlg.getByRole("button", { name: s.t("Create", "建立"), exact: true })]);
      await s.click(dlg.getByRole("button", { name: s.t("Cancel", "取消"), exact: true }), { wait: 500 });
      await s.click(editQ, { wait: 500 });
      const dlg2 = s.page.getByRole("dialog");
      await s.shot(7, [dlg2.getByRole("textbox", { name: s.t("Correct answer", "正確答案") }), dlg2.getByRole("button", { name: s.t("Save", "儲存"), exact: true })], { newFrame: true });
    },
    // s07 Watch student progress
    async (s) => {
      await s.go("/course-cms");
      const tab = s.page.locator('[data-guide-tab="course-cms-tab-progress"]');
      await s.click(tab);
      const sel = s.page.locator('[data-guide="course-cms-progress-toolbar"] select');
      await sel.selectOption(PATH.linux);
      await s.settle(1200);
      const crop = await main(s, 560);
      await s.shot(1, tab, { crop });
      await s.shot(2, sel, { crop });
      await s.shot(3, s.page.getByText(s.t("Live updating", "即時更新中")), { crop });
      const th = s.page.locator("table th");
      await s.shot(4, [th.nth(0), th.nth(1), th.nth(2), th.nth(3), th.nth(4)], { crop });
      await sel.selectOption(PATH.sec);
      await s.settle(1200);
      // From the top, so the path picker shows which path has no answers yet.
      const empty = s.page.getByText(s.t("No submissions yet", "尚無作答紀錄"));
      const eb = await empty.boundingBox();
      await s.shot(5, empty, { crop: await main(s, Math.round(eb.y + eb.height + 60)), newFrame: true });
    },
  ],
};

// The page area right of the sidebar, from the top (height: px, default the whole viewport).
async function main(s, height = 900) {
  const h1 = await s.page.locator("h1").first().boundingBox();
  const x = Math.max(0, Math.round(h1.x - 24));
  return { x, y: 0, width: 1440 - x, height };
}

// Mock data for the section's language (same seed as the mocks).
function m(s) {
  return s.m.state.cms;
}
const row = (s, label) => s.page.locator("nav li > div").filter({ hasText: label }).first();
const toggleBtn = (s, label) => s.page.getByRole("button", { name: s.t(`Expand or collapse “${label}”`, `展開或收合「${label}」`) });

async function openPath(s, pathId) {
  const p = m(s).paths.find((x) => x.id === pathId);
  await s.click(s.page.getByRole("button", { name: p.title, exact: true }), { wait: 600 });
}

// Shows a native <select> as an open list (native drop-downs are not captured in screenshots).
async function listbox(sel, on) {
  await sel.evaluate((el, on) => {
    if (on) {
      el.setAttribute("size", String(Math.min(el.options.length, 6)));
      el.style.height = "auto";
      el.style.overflow = "hidden";
    } else {
      el.removeAttribute("size");
      el.style.height = "";
      el.style.overflow = "";
    }
  }, on);
}
