// Manual page: manual/ai-judge.md (AI Check tab of a class)
import { D, H, MIN, NOW, ago, iso } from "../lib/mock.mjs";

const CID = "c1a55000-0000-4000-8000-000000000020";
const ROUTE = `/class-management/${CID}/ai`;
const J = `^/api/v1/teaching-classes/${CID}/judge`;
const re = (s) => new RegExp(s);

// ---------------------------------------------------------------- class

const TOPICS = [
  ["Course intro and SkyLab setup", "課程介紹與 SkyLab 環境"],
  ["Linux command line and SSH", "Linux 指令與 SSH 連線"],
  ["Docker and Docker Compose", "Docker 與 Docker Compose"],
  ["Installing n8n", "安裝 n8n"],
  ["n8n with PostgreSQL", "n8n 串接 PostgreSQL"],
  ["Webhooks and form intake", "Webhook 與表單收件"],
  ["Scheduled jobs and backups", "排程與備份"],
  ["Midterm project", "期中專題"],
  ["Midterm week", "期中考週"],
  ["Calling external APIs", "串接外部 API"],
  ["Error handling and retries", "錯誤處理與重試"],
  ["Notifications: email and LINE", "通知：Email 與 LINE"],
  ["Workflow version control", "工作流程版本控管"],
  ["Credentials and security", "憑證與安全性"],
  ["Performance and monitoring", "效能與監控"],
  ["Final project workshop", "期末專題實作"],
  ["Final project demos", "期末專題展示"],
  ["Course review", "期末總結"],
];
const FIRST_CLASS = Date.parse("2026-09-09T00:00:00+08:00");
const weekDate = (n) => new Date(FIRST_CLASS + (n - 1) * 7 * D + 8 * H).toISOString().slice(0, 10);

const NODES = [
  { id: "mn-0", node_key: "node-n8n", name: "n8n", sort_order: 0, role: "Workflow server", resource_type: "qemu", cpu: 2, memory_mb: 4096, disk_gb: 32, source_type: "template", template_name: "n8n" },
  { id: "mn-1", node_key: "node-pg", name: "PostgreSQL", sort_order: 1, role: "Database", resource_type: "lxc", cpu: 2, memory_mb: 2048, disk_gb: 16, source_type: "template", template_name: "PostgreSQL" },
  { id: "mn-2", node_key: "node-linux", name: "Linux", sort_order: 2, role: "Practice host", resource_type: "lxc", cpu: 1, memory_mb: 1024, disk_gb: 12, source_type: "template", template_name: "Linux" },
];
const VM_PREFIX = ["n8n", "pg", "linux"];

const ROSTER = [
  ["Alex Wang", "王小明"], ["Grace Lee", "李佳穎"], ["Kevin Chen", "陳冠宇"], ["Emily Chang", "張雅婷"], ["Brian Huang", "黃柏翰"],
  ["Olivia Lin", "林宜臻"], ["Ethan Wu", "吳承翰"], ["Chloe Liu", "劉怡君"], ["Ryan Tsai", "蔡明哲"], ["Sophia Yang", "楊詠晴"],
  ["Jason Hsu", "許家豪"], ["Ivy Cheng", "鄭宇彤"], ["Daniel Hsieh", "謝承佑"], ["Megan Kuo", "郭芷瑄"], ["Leo Hung", "洪浩宇"],
  ["Hannah Tseng", "曾子涵"], ["Eric Chiu", "邱冠霖"], ["Zoe Liao", "廖品妍"], ["Andy Lai", "賴柏翰"], ["Joyce Chou", "周欣妤"],
  ["Henry Yeh", "葉昱辰"], ["Tina Su", "蘇郁婷"], ["Peter Chuang", "莊博鈞"], ["Amy Lu", "呂宜蓁"], ["Victor Ho", "何俊廷"],
  ["Nina Kao", "高子晴"], ["Sam Lo", "羅志豪"], ["Vivian Chung", "鍾佩珊"], ["Jack Chiang", "江冠廷"], ["Lily Fan", "范書瑜"],
];
const OFF = new Set([27, 28]); // machines not powered on
const PENDING = new Set([1, 5, 9]); // teacher-review item still undecided
const FAIL_CRON = new Set([0, 7]);
const FAIL_DB = new Set([2]);
const WARN_PSQL = new Set([3]);
const PARTIAL = new Set([10, 14, 19]); // reported part of the items
const NOT_REPORTED = new Set([12, 22, 25, 27, 28]);
const REPLIED = new Set([2, 4]);

function roster(m) {
  return ROSTER.map(([en, zh], i) => {
    const sid = `111560${String(i + 1).padStart(2, "0")}`;
    return {
      idx: i,
      sid,
      name: m.L(en, zh),
      email: `${sid}@ntub.edu.tw`,
      user_id: i === 0 ? m.users.student.id : `7d3c1a2e-0${String(200 + i)}-4a00-9000-0000000000bb`,
    };
  });
}
const vmOf = (i, j) => 30100 + i * 3 + j;

// ---------------------------------------------------------------- checklist items

const step = (id, title, argv, assertion) => ({ id, title, collector: { type: "command", argv, timeout_seconds: 20 }, assertion });
const fileStep = (id, title, path, assertion) => ({ id, title, collector: { type: "file_text", path, read_mode: "full", timeout_seconds: 20 }, assertion });
const item = (id, title, method, nodeKey, steps, extra = {}) => ({
  id, title, detection_method: method, detectable: "auto", judgement_mode: "ai", target_node_key: nodeKey,
  peer_node_key: null, fallback: null, missing_information: [], checked: true, check_steps: steps, ...extra,
});

function week5Items(m) {
  return [
    item("w5-1", m.L("n8n is served over an HTTPS URL", "n8n 以 HTTPS 網址對外提供服務"),
      m.L("Request n8n's health endpoint on the n8n machine and expect HTTP 200.", "在 n8n 機器上請求 n8n 健康檢查端點，確認回應 200。"), "node-n8n",
      [step("w5-1-1", m.L("Health check returns 200", "健康檢查回 200"), ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "http://localhost:5678/healthz"], { type: "text_equals", expected: "200" })]),
    item("w5-2", m.L("n8n uses PostgreSQL as its database", "n8n 改用 PostgreSQL 作為資料庫"),
      m.L("Read DB_TYPE inside the n8n container; it should be postgresdb.", "讀取 n8n 容器的環境變數 DB_TYPE，應為 postgresdb。"), "node-n8n",
      [step("w5-2-1", "DB_TYPE = postgresdb", ["docker", "exec", "n8n", "printenv", "DB_TYPE"], { type: "text_contains", expected: "postgresdb" })]),
    item("w5-3", m.L("PostgreSQL has an n8n database and role", "PostgreSQL 已建立 n8n 專用資料庫與帳號"),
      m.L("List the databases with psql and confirm that n8n exists.", "以 psql 列出資料庫，確認 n8n 存在。"), "node-pg",
      [step("w5-3-1", m.L("Database n8n exists", "資料庫 n8n 存在"), ["psql", "-U", "postgres", "-tAc", "select datname from pg_database where datname='n8n'"], { type: "text_contains", expected: "n8n" })]),
    item("w5-4", m.L("Linux host can query n8n tables with psql", "Linux 主機能以 psql 查詢 n8n 資料表"),
      m.L("Run psql on the Linux host and count the rows in workflow_entity.", "在 Linux 主機執行 psql，查詢 workflow_entity 筆數。"), "node-linux",
      [step("w5-4-1", m.L("psql query succeeds", "psql 查詢成功"), ["psql", "-h", "postgresql", "-U", "n8n", "-tAc", "select count(*) from workflow_entity"], { type: "returncode_equals", expected: 0 })]),
    item("w5-5", m.L("Linux host runs a daily 02:00 backup", "Linux 主機已設定每日 02:00 備份排程"),
      m.L("Read the crontab and confirm pg_dump runs every day at 02:00.", "讀取 crontab，確認每天 02:00 執行 pg_dump。"), "node-linux",
      [step("w5-5-1", m.L("crontab contains pg_dump", "crontab 含 pg_dump"), ["crontab", "-l"], { type: "text_contains", expected: "pg_dump" })]),
    item("w5-6", m.L("Form submissions are stored in the database", "表單資料確實寫入資料庫"),
      m.L("Collect the latest 5 rows of the signup table for the teacher to judge.", "收集 signup 資料表最新 5 筆，由導師確認內容合理。"), "node-n8n",
      [step("w5-6-1", m.L("Latest signup rows", "signup 最新資料"), ["psql", "-h", "postgresql", "-U", "n8n", "-c", "select name, email, created_at from signup order by id desc limit 5"], null)],
      { judgement_mode: "teacher" }),
  ];
}

function week6Items(m) {
  return [
    item("w6-1", m.L("Signup workflow is active", "報名表單工作流程已啟用"),
      m.L("List n8n workflows and confirm \"Signup form\" is active.", "列出 n8n 工作流程，確認「報名表單」為啟用狀態。"), "node-n8n",
      [step("w6-1-1", m.L("Workflow is active", "工作流程已啟用"), ["docker", "exec", "n8n", "n8n", "list:workflow", "--active=true"], { type: "text_contains", expected: "Signup form" })]),
    item("w6-2", m.L("Webhook /webhook/signup answers with 200", "Webhook /webhook/signup 回應 200"),
      m.L("POST a test submission to the webhook on port 5678 and expect HTTP 200.", "對 5678 埠的 Webhook 送出測試資料，確認回應 200。"), "node-n8n",
      [step("w6-2-1", m.L("Webhook returns 200", "Webhook 回 200"), ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-X", "POST", "http://localhost:5678/webhook/signup"], { type: "text_equals", expected: "200" })]),
    item("w6-3", m.L("signup table exists in the n8n database", "n8n 資料庫裡有 signup 資料表"),
      m.L("Describe the signup table with psql.", "以 psql 查看 signup 資料表結構。"), "node-pg",
      [step("w6-3-1", m.L("Table signup exists", "signup 資料表存在"), ["psql", "-U", "postgres", "-d", "n8n", "-tAc", "select to_regclass('public.signup')"], { type: "text_contains", expected: "signup" })]),
    item("w6-4", m.L("Confirmation email template is filled in", "確認信範本已填寫"),
      m.L("Collect the email template for the teacher to judge.", "收集確認信範本，由導師判斷內容。"), "node-n8n",
      [fileStep("w6-4-1", m.L("Email template text", "確認信範本內容"), "/srv/n8n/templates/confirm.html", null)],
      { judgement_mode: "teacher" }),
  ];
}

// s05: one item lacks a path, one was edited since the last review.
function week6ReviewItems(m) {
  const items = week6Items(m);
  items[2] = { ...items[2], detection_method: m.L("Describe the signup table with psql; port 5433 instead of the default.", "以 psql 查看 signup 資料表結構，改連 5433 埠。") };
  return [
    ...items.slice(0, 3),
    item("w6-5", m.L("Daily export CSV contains today's signups", "每日匯出的 CSV 含有當天報名資料"),
      m.L("Read the exported CSV and check that it has rows dated today.", "讀取匯出的 CSV，確認有今天日期的資料。"), "node-linux", [],
      { detectable: "partial", missing_information: [m.L("the full path of the exported CSV file", "匯出 CSV 檔的完整路徑")] }),
    items[3],
  ];
}

// ---------------------------------------------------------------- results

const raw = (argv, stdout, rc = 0) => JSON.stringify({ argv, cwd: null, stdout, stderr: "", returncode: rc, error: null, errors: [] });

function checkFor(m, x, check) {
  const i = x.idx;
  const argv = check.collector.argv;
  switch (check.id) {
    case "w5-1-1": return { status: "pass", evidence: "200", raw: raw(argv, "200") };
    case "w5-2-1": return FAIL_DB.has(i)
      ? { status: "fail", evidence: m.L("DB_TYPE is not set; n8n still uses SQLite", "DB_TYPE 未設定，n8n 仍使用內建 SQLite"), raw: raw(argv, "", 1) }
      : { status: "pass", evidence: "postgresdb", raw: raw(argv, "postgresdb\n") };
    case "w5-3-1": return { status: "pass", evidence: "n8n", raw: raw(argv, "n8n\n") };
    case "w5-4-1": return WARN_PSQL.has(i)
      ? { status: "warning", evidence: m.L("Query succeeded but returned 0 rows; no workflow saved yet?", "查詢成功但回傳 0 筆，工作流程可能尚未儲存"), raw: raw(argv, "0\n") }
      : { status: "pass", evidence: m.L("3 workflows", "3 筆工作流程"), raw: raw(argv, "3\n") };
    case "w5-5-1": return FAIL_CRON.has(i)
      ? { status: "fail", evidence: m.L("no crontab for student (no backup job yet)", "no crontab for student（尚未設定備份排程）"), raw: raw(argv, "", 1) }
      : { status: "pass", evidence: "0 2 * * * pg_dump … > ~/backup/n8n.sql", raw: raw(argv, "0 2 * * * pg_dump -h postgresql -U n8n n8n > ~/backup/n8n-$(date +\\%F).sql\n") };
    case "w5-6-1": {
      const rows = ` name       | email                  | created_at\n------------+------------------------+---------------------\n ${x.name.padEnd(10)} | ${x.email} | 2026-10-07 10:${String(12 + (i % 40)).padStart(2, "0")}:08\n ${m.L("Test one", "測試一").padEnd(10)} | test1@example.com      | 2026-10-07 09:58:41\n(2 rows)\n`;
      return { status: "collected", evidence: m.L("Collected the latest 2 signup rows", "收集到 signup 最新 2 筆資料"), raw: raw(argv, rows) };
    }
    default: return { status: "unknown", evidence: "", raw: raw([], "") };
  }
}

const TIMING = { queue_seconds: 1.284, prepare_seconds: 3.912, script_seconds: 2.405, download_seconds: 0.318, finalize_wait_seconds: 0.207, batch_wait_seconds: 4.65, total_seconds: 12.776 };

function studentResult(m, x, items, reviewOf) {
  return {
    student_id: x.user_id,
    nodes: NODES.map((n, j) => {
      const vmid = vmOf(x.idx, j);
      const base = { node_key: n.node_key, vmid, run_id: `run-${j}`, display_label: `P${j + 1}`, unmapped_checks: [], timing: TIMING };
      if (OFF.has(x.idx)) {
        return { ...base, execution_status: "failed", reason_code: "vm_powered_off", failure_stage: "preflight", timing: {}, teacher_review: null, items: items.filter((it) => it.target_node_key === n.node_key).map((it) => ({ rubric_item_id: it.id, title: it.title, status: "unknown", checks: [], missing_check_ids: [], evidence_state: "unavailable" })) };
      }
      const its = items.filter((it) => it.target_node_key === n.node_key).map((it) => {
        const checks = it.check_steps.map((st) => ({ id: st.id, title: st.title, ...checkFor(m, x, st) }));
        const status = checks.some((c) => c.status === "fail") ? "fail" : checks.some((c) => c.status === "warning") ? "warning" : checks.some((c) => c.status === "collected") ? "collected" : "pass";
        return { rubric_item_id: it.id, title: it.title, status, peer_node_key: null, peer_display_label: null, reason_code: null, checks, missing_check_ids: [] };
      });
      return { ...base, execution_status: "completed", reason_code: null, teacher_review: reviewOf(x, vmid, n), items: its };
    }),
  };
}

// ---------------------------------------------------------------- world

// Builds the class, its weeks and the checks. opts:
//   checks: { [week]: { items, msgs, source, sets, batch, rev } }
function world(m, opts = {}) {
  const S = m.state;
  const students = roster(m);
  S.checks = opts.checks;
  S.allWeeks = Boolean(opts.allWeeks);

  const weekObj = (n) => ({
    id: `wk-${n}`, week_number: n, session_date: weekDate(n), title: m.L(...TOPICS[n - 1]),
    status: n <= 6 ? "published" : "draft", target_node_key: null, files: [],
  });
  const weeks = TOPICS.map((_, i) => weekObj(i + 1));
  const classObj = () => ({
    id: CID, name: m.L("n8n Automation Practice (IM 3A)", "n8n 自動化實務 (資管三甲)"), term: "115-1", location: m.L("Info Building B302", "資訊大樓 B302"),
    status: "active", timezone: "Asia/Taipei", start_date: "2026-09-09", end_date: "2027-01-06", weekday: 2, start_time: "09:10:00", end_time: "12:00:00",
    boot_lead_minutes: 10, shutdown_grace_minutes: 30, course_version_id: "envv-n8n-lab-1",
    course_environment: { id: "env-n8n-lab", version_id: "envv-n8n-lab-1", name: m.L("n8n automation lab", "n8n 自動化實務環境"), version: 1, status: "published" },
    machine_nodes: NODES, instructor_machine: null, weeks,
    students: students.map((x) => ({ id: `ns-${x.idx}`, user_id: x.user_id, status: "active", joined_at: "2026-09-02T02:00:00Z", email: x.email, full_name: x.name,
      machines: NODES.map((n, j) => ({ machine_node_id: n.id, vmid: vmOf(x.idx, j), status: "completed", error: null })) })),
    provision_jobs: [], ready_machines: students.length * 3, total_machines: students.length * 3, member_count: students.length,
    topology_edges: [], node_positions: {}, publications: [],
  });

  const weekNums = () => {
    const nums = Object.keys(S.checks).map(Number);
    return S.allWeeks ? TOPICS.map((_, i) => i + 1) : nums;
  };
  const check = (n) => (S.checks[n] ||= { items: [], msgs: [], rev: 1 });
  const sessionObj = (n) => {
    const c = check(n);
    return {
      id: `chk-wk${n}`, teaching_class_id: CID, teaching_class_week_id: `wk-${n}`, title: m.L(`Week ${n}: ${TOPICS[n - 1][0]}`, `第 ${n} 週：${TOPICS[n - 1][1]}`),
      status: "ready", selected_file_id: `file-wk${n}`, summary: null, created_at: iso(NOW - (8 - Math.min(n, 7)) * D), updated_at: ago(2 * H), last_activity_at: ago(2 * H),
      pinned_at: null, message_count: c.msgs.length, script_count: c.sets ? 3 : 0, run_count: c.batch ? 1 : 0,
    };
  };
  const fileObj = (n) => {
    const c = check(n);
    const items = c.items;
    const uploaded = c.source === "uploaded";
    return {
      id: `file-wk${n}`, teaching_class_id: CID, template_key: "n8n", source_type: uploaded ? "uploaded" : "created",
      display_name: uploaded ? m.L(`Week ${n} rubric`, `第 ${n} 週評分表`) : m.L(`Week ${n} checklist`, `第 ${n} 週檢查表`),
      original_filename: uploaded ? m.L(`week${n}-rubric.docx`, `第${n}週評分表.docx`) : null,
      name: uploaded ? m.L(`Week ${n} rubric`, `第 ${n} 週評分表`) : m.L(`Week ${n} checklist`, `第 ${n} 週檢查表`),
      environment_keys: ["n8n", "postgresql", "linux"], analysis_revision: c.rev,
      analysis_json: {
        items, total_items: items.length, checked_count: items.length,
        auto_count: items.filter((x) => x.detectable === "auto").length, partial_count: items.filter((x) => x.detectable === "partial").length, manual_count: 0,
        detectability_needs_review: Boolean(c.reviewIds?.length), pending_review_item_ids: c.reviewIds || [],
      },
      status: "ready", created_at: iso(NOW - 6 * D), updated_at: ago(3 * H),
    };
  };
  const weekOf = (sid) => Number(String(sid).replace("chk-wk", ""));
  const setObj = (n) => ({
    artifact_set_id: `set-wk${n}`, status: "approved", is_active: true, source_analysis_revision: check(n).setRev ?? check(n).rev, source_is_current: true,
    created_at: ago(26 * H),
    children: NODES.map((nd, k) => ({ id: `art-${n}-${k}`, target_node_key: nd.node_key, name: m.L(`${nd.name} · week ${n} check script`, `${nd.name} · 第 ${n} 週檢查腳本`), status: "approved" })),
  });

  const reviewOf = (x, vmid, node) => {
    if (node.node_key !== "node-n8n") return null;
    if (PENDING.has(x.idx) || OFF.has(x.idx)) return null;
    const feedback = "";
    return { feedback, decisions: { "w5-6-1": "pass" }, updated_at: ago(20 * H) };
  };
  const completedBatch = (n) => {
    const items = check(n).items;
    return {
      run_batch_id: "batch-1", status: "completed", summary: { targets: students.length * 3, completed: (students.length - OFF.size) * 3, failed: OFF.size * 3 },
      started_at: ago(22 * H), finished_at: ago(22 * H - 3 * MIN),
      nodes: NODES.map((nd, k) => ({ target_node_key: nd.node_key, display_label: `P${k + 1}`, artifact_id: `art-${n}-${k}`, run_id: `run-${k}`, status: "completed", progress_json: { done: students.length, total: students.length }, result_summary_json: {} })),
      students: students.map((x) => studentResult(m, x, items, reviewOf)),
    };
  };
  S.live = null; // { id, status, nodes, done }
  const liveBatch = () => {
    const L = S.live;
    const total = students.length;
    const finished = L.status === "completed";
    const nodeRows = finished
      ? NODES.map(() => ["completed", total, null])
      : L.nodes;
    const completed = nodeRows.reduce((a, [, d]) => a + d, 0);
    return {
      run_batch_id: L.id, status: L.status, summary: { targets: total * 3, completed, failed: 0 },
      started_at: ago(2 * MIN), finished_at: finished ? iso(NOW) : null,
      nodes: NODES.map((nd, k) => ({ target_node_key: nd.node_key, display_label: `P${k + 1}`, artifact_id: `art-5-${k}`, run_id: `run-live-${k}`, status: nodeRows[k][0], progress_json: { done: nodeRows[k][1], total, ...(nodeRows[k][2] ? { stage: nodeRows[k][2] } : {}) }, result_summary_json: {} })),
      students: finished ? students.map((x) => studentResult(m, x, check(5).items, reviewOf)) : [],
    };
  };

  const progressOf = (n) => {
    const c = check(n);
    return {
      items: c.items.map((it) => ({ id: it.id, title: it.title, machine_node_key: it.target_node_key })),
      students: students.map((x) => {
        const ids = c.items.map((it) => it.id);
        const done = NOT_REPORTED.has(x.idx) ? [] : PARTIAL.has(x.idx) ? ids.slice(0, 3) : ids;
        const ready = !NOT_REPORTED.has(x.idx) && !PARTIAL.has(x.idx);
        const reply = (c.replies || {})[x.user_id] ?? (REPLIED.has(x.idx) && c.batch ? m.L("Nice work. Remember to set DB_TYPE before restarting n8n.", "做得不錯，重新啟動 n8n 前記得先設定 DB_TYPE。") : "");
        return { user_id: x.user_id, full_name: x.name, email: x.email, completed_item_ids: done, ready_at: ready ? ago(26 * H) : null,
          teacher_reply: reply, teacher_reply_at: reply ? ago(20 * H) : null };
      }),
    };
  };

  S.attachQueue = [];
  m.use([
    ["GET", re(`^/api/v1/teaching-classes/${CID}$`), () => classObj()],
    ["GET", re(`^/api/v1/classroom/classes/${CID}/students$`), () => students.map((x) => ({
      user_id: x.user_id, email: x.email, full_name: x.name,
      vms: NODES.map((n, j) => ({ vmid: vmOf(x.idx, j), node_key: n.node_key, display_label: `P${j + 1}`, name: `${VM_PREFIX[j]}-${x.sid}`, status: OFF.has(x.idx) ? "stopped" : "running", vm_type: n.resource_type })),
    }))],

    ["GET", re(`${J}/sessions/$`), () => weekNums().map(sessionObj)],
    ["PUT", re(`${J}/sessions/weeks/wk-(\\d+)$`), ({ mt }) => { check(Number(mt[1])); return { __delay: 300, __body: sessionObj(Number(mt[1])) }; }],
    ["PATCH", re(`${J}/sessions/chk-wk(\\d+)$`), ({ mt, body }) => {
      const from = Number(mt[1]);
      const to = Number(String(body?.teaching_class_week_id).replace("wk-", ""));
      S.checks[to] = S.checks[from];
      delete S.checks[from];
      return sessionObj(to);
    }],
    ["POST", re(`${J}/sessions/chk-wk\\d+/fork$`), ({ body }) => { const to = Number(String(body?.teaching_class_week_id).replace("wk-", "")); check(to); return sessionObj(to); }],
    ["DELETE", re(`${J}/sessions/chk-wk(\\d+)$`), ({ mt }) => { delete S.checks[Number(mt[1])]; return { message: "ok" }; }],
    ["GET", re(`${J}/files/$`), () => weekNums().map(fileObj)],
    ["PATCH", re(`${J}/files/file-wk(\\d+)/analysis$`), ({ mt, body }) => {
      const c = check(Number(mt[1]));
      if (Array.isArray(body?.analysis?.items)) c.items = body.analysis.items;
      c.reviewIds = body?.analysis?.pending_review_item_ids || [];
      c.rev += 1;
      return { __delay: 150, __body: fileObj(Number(mt[1])) };
    }],
    ["GET", re(`${J}/sessions/chk-wk(\\d+)/messages$`), ({ mt }) => check(Number(mt[1])).msgs],
    ["DELETE", re(`${J}/sessions/chk-wk(\\d+)/messages$`), ({ mt }) => { check(Number(mt[1])).msgs = []; return sessionObj(Number(mt[1])); }],
    ["DELETE", re(`${J}/sessions/[^/]+/messages/[^/]+/proposal$`), () => ({ message: "ok" })],
    ["POST", re(`${J}/sessions/chk-wk(\\d+)/messages$`), (ctx) => (S.chat ? S.chat(Number(ctx.mt[1]), ctx) : { __status: 500, __body: { detail: "no chat" } })],
    ["POST", re(`${J}/sessions/[^/]+/attachments$`), () => ({ __delay: 200, __body: { attachment: S.attachQueue.shift() } })],
    ["DELETE", re(`${J}/sessions/[^/]+/attachments/[^/]+$`), () => ({ message: "ok" })],

    ["GET", re(`${J}/sessions/chk-wk(\\d+)/script-sets$`), ({ mt }) => (check(Number(mt[1])).sets ? [setObj(Number(mt[1]))] : [])],
    ["POST", re(`${J}/sessions/chk-wk(\\d+)/script-sets$`), ({ mt }) => {
      const n = Number(mt[1]);
      check(n).sets = true;
      return { __delay: 900, __body: { ...setObj(n), source_analysis_revision: check(n).rev } };
    }],
    ["GET", re(`${J}/sessions/chk-wk\\d+/runs$`), () => []],
    ["GET", re(`${J}/sessions/chk-wk(\\d+)/progress$`), ({ mt }) => progressOf(Number(mt[1]))],
    ["GET", re(`${J}/sessions/chk-wk(\\d+)/run-batches$`), ({ mt }) => {
      const n = Number(mt[1]);
      const out = [];
      if (S.live && n === 5) out.push({ run_batch_id: S.live.id, status: S.live.status, summary: liveBatch().summary });
      if (check(n).batch) out.push({ run_batch_id: "batch-1", status: "completed", summary: completedBatch(n).summary });
      return out;
    }],
    ["GET", re(`${J}/sessions/chk-wk(\\d+)/run-batches/([^/]+)$`), ({ mt }) => {
      const n = Number(mt[1]);
      if (mt[2] === "batch-1" && check(n).batch) return completedBatch(n);
      if (S.live && mt[2] === S.live.id) {
        if (S.liveDone) S.live.status = "completed";
        return liveBatch();
      }
      return { __status: 404, __body: { detail: "Run batch not found" } };
    }],
    ["POST", re(`${J}/sessions/chk-wk\\d+/script-sets/[^/]+/runs$`), ({ body }) => {
      if (S.runFail) return { __status: 502, __body: { detail: "Bad Gateway" } };
      S.live = { id: body?.run_batch_id || "batch-2", status: "running", nodes: [["completed", 30, null], ["running", 18, null], ["pending", 0, "preparing"]] };
      return { __delay: 300, __body: liveBatch() };
    }],
    ["PATCH", re(`${J}/sessions/[^/]+/runs/[^/]+/targets/(\\d+)/review$`), ({ mt, body }) => {
      const vmid = Number(mt[1]);
      const i = Math.floor((vmid - 30100) / 3);
      return { target_results_json: { targets: [{ vmid, student_id: students[i]?.user_id, teacher_review: { feedback: body?.feedback || "", decisions: body?.decisions || {}, updated_at: iso(NOW) } }] } };
    }],
    ["PUT", re(`${J}/sessions/chk-wk(\\d+)/students/([^/]+)/reply$`), ({ mt, body }) => {
      const c = check(Number(mt[1]));
      (c.replies ||= {})[decodeURIComponent(mt[2])] = body?.feedback || "";
      return { user_id: decodeURIComponent(mt[2]), teacher_reply: body?.feedback || "", teacher_reply_at: iso(NOW) };
    }],
    ["GET", re(`${J}/files/[^/]+/download$`), () => ({ __text: "rubric", __contentType: "application/octet-stream" })],
  ]);
  return { students };
}

// ---------------------------------------------------------------- conversations

let seq = 0;
const msg = (role, content, extra = {}) => ({
  id: `msg-${++seq}`, session_id: extra.session_id || "chk", role, content, message_type: "chat",
  metadata_json: extra.metadata_json || {}, attachments: extra.attachments || [], created_at: extra.at || ago(3 * H),
});

function week5Messages(m) {
  const items = week5Items(m);
  return [
    msg("user", m.L(
      "This week students connect n8n to PostgreSQL. Check on the n8n machine that n8n answers on HTTPS and uses PostgreSQL; on the PostgreSQL machine that the n8n database exists; on the Linux machine that psql can read the workflow table and that a pg_dump backup runs every day at 02:00. I want to look at the signup rows myself.",
      "這週學生要把 n8n 改接 PostgreSQL。請在 n8n 機器檢查 HTTPS 有回應、資料庫改用 PostgreSQL；在 PostgreSQL 機器檢查有 n8n 資料庫；在 Linux 機器檢查 psql 讀得到工作流程資料表，以及每天 02:00 有 pg_dump 備份。signup 的資料我想自己看。"), { at: ago(28 * H) }),
    msg("assistant", m.L(
      "I split this week's requirements into 6 checkpoints on three machines. The signup rows are collected for you to judge. Apply them if they look right, or tell me what to change.",
      "我把這週的要求拆成 6 個檢查點，分別在三台機器上檢查；signup 的資料會收集起來由你判斷。確認沒問題就套用，或告訴我要調整哪一項。"),
    { at: ago(28 * H - MIN), metadata_json: { tool_calls: items.map((d) => ({ status: "staged", operation: "add", title: d.title })), status: "proposed", rubric_proposal: items.map((d) => ({ ...d, operation: "add" })), source_file_id: "file-wk5", analysis_revision: 1 } }),
    msg("assistant", m.L("All 6 checkpoints are complete: each has a machine and a check command, and item 6 is judged by you. The scripts can be built.", "6 個檢查點都已核對完成：每一項都有對應的機器與檢查指令，第 6 項由你判定。可以製作檢查腳本。"),
      { at: ago(26 * H), metadata_json: { tool_calls: [], script_ready: true, status: "ready" } }),
  ];
}

const W6_REQUEST = [
  "On the n8n machine, check that the workflow \"Signup form\" is active and that the webhook /webhook/signup answers with 200. On the PostgreSQL machine, check that the signup table exists.",
  "請在 n8n 機器檢查「報名表單」工作流程已啟用，而且 Webhook /webhook/signup 會回應 200；在 PostgreSQL 機器檢查 signup 資料表存在。",
];
const W6_QUESTION = [
  "Before I write the checks I need two details:\n1. Which port does n8n listen on inside the machine? Is it the default 5678?\n2. Which database holds the signup table: n8n, or a separate one?",
  "寫檢查之前還需要兩項資訊：\n1. n8n 在機器內監聽哪個埠？是預設的 5678 嗎？\n2. signup 資料表放在哪個資料庫：n8n，還是另外建的資料庫？",
];
const W6_ANSWER = ["Port 5678, and the table is in the n8n database.", "埠號是 5678，資料表在 n8n 資料庫。"];

function proposalReply(m, n, rev) {
  const items = week6Items(m).slice(0, 3);
  return msg("assistant", m.L(
    "Thanks. I proposed 3 checkpoints:\n1. Signup workflow is active (n8n)\n2. Webhook /webhook/signup answers with 200 (n8n)\n3. signup table exists in the n8n database (PostgreSQL)\nApply them above the checklist, or tell me what to change.",
    "了解，我提出 3 個檢查點：\n1. 報名表單工作流程已啟用（n8n）\n2. Webhook /webhook/signup 回應 200（n8n）\n3. n8n 資料庫裡有 signup 資料表（PostgreSQL）\n請在檢查表上方確認後套用，或告訴我要調整哪一項。"),
  { at: iso(NOW), session_id: `chk-wk${n}`, metadata_json: { tool_calls: items.map((d) => ({ status: "staged", operation: "add", title: d.title })), status: "proposed", rubric_proposal: items.map((d) => ({ ...d, operation: "add" })), source_file_id: `file-wk${n}`, analysis_revision: rev } });
}

// s04: a rubric document analysed row by row.
function itemwiseMessages(m) {
  const items = week6Items(m);
  const label = (k) => m.L(`Row ${k}`, `第 ${k} 列`);
  const results = [
    { source_index: 1, source_label: label(1), title: items[0].title, status: "ready", operation: { ...items[0], operation: "add" }, missing_information: [], detail: "" },
    { source_index: 2, source_label: label(2), title: items[1].title, status: "ready", operation: { ...items[1], operation: "add" }, missing_information: [], detail: "" },
    { source_index: 3, source_label: label(3), title: items[2].title, status: "ready", operation: { ...items[2], operation: "add" }, missing_information: [], detail: "" },
    { source_index: 4, source_label: label(4), title: items[3].title, status: "teacher_review", operation: { ...items[3], operation: "add" }, missing_information: [], detail: "" },
    { source_index: 5, source_label: label(5), title: m.L("Daily export CSV contains today's signups", "每日匯出的 CSV 含有當天報名資料"), status: "needs_information", operation: null, missing_information: [m.L("the full path of the exported CSV file", "匯出 CSV 檔的完整路徑")], detail: "" },
    { source_index: 6, source_label: label(6), title: m.L("Workflow layout is easy to read", "工作流程版面清楚易讀"), status: "unsupported", operation: null, missing_information: [], detail: m.L("Readability is a judgement on the editor screen; there is no evidence a script can collect.", "可讀性要看編輯畫面判斷，沒有腳本能收集的證據。") },
    { source_index: 7, source_label: label(7), title: m.L("Signup notification reaches LINE", "報名通知送到 LINE"), status: "analysis_error", operation: null, missing_information: [], detail: m.L("AI reply failed: timeout", "AI 回覆失敗：逾時") },
  ];
  const reply = m.L(
    "I went through the 7 rows of the attachment:\nRows 1–3 are ready as proposals; row 4 collects the template for you to judge.\nRow 5 still needs the full path of the exported CSV file.\nRow 6 cannot be checked automatically: readability is a judgement on the editor screen.\nRow 7 failed to analyse; send it again on its own.",
    "已逐項核查附件中的 7 個項目：\n第 1～3 列已整理成提案；第 4 列會收集範本供你判斷。\n第 5 列還缺少資訊：匯出 CSV 檔的完整路徑。\n第 6 列目前無法安全取證：可讀性要看編輯畫面判斷。\n第 7 列這項分析沒有成功，請稍後針對此項重新送出。");
  return [
    msg("user", m.L("Turn this rubric into checks for week 6.", "請把這份評分表整理成第 6 週的檢查。"), { session_id: "chk-wk6", at: ago(20 * MIN), attachments: [{ id: "att-9", original_filename: m.L("week6-rubric.docx", "第6週評分表.docx"), status: "ready" }] }),
    msg("assistant", reply, { session_id: "chk-wk6", at: ago(18 * MIN), metadata_json: { tool_calls: [], status: "proposed", rubric_proposal: results.filter((r) => r.operation).map((r) => r.operation), item_results: results, source_file_id: "file-wk6", analysis_revision: 1 } }),
  ];
}

// ---------------------------------------------------------------- helpers

const base = (m) => ({
  2: { items: [], msgs: [], rev: 3, sets: true, batch: false },
  3: { items: [], msgs: [], rev: 4, sets: true, batch: false },
  4: { items: [], msgs: [], rev: 2, sets: true, batch: false },
  5: { items: week5Items(m), msgs: week5Messages(m), rev: 3, setRev: 3, sets: true, batch: true },
});

// The three-column workspace needs about 1100 px of content width, and the
// checklist shows as a table only above about 700 px of its own width.
const VW = 1920;
async function open(s, week, { tab = null, height = 1080 } = {}) {
  if (!s.judgeWidths) {
    // Wider week and chat columns (the user can drag them; the page remembers the widths)
    // keep the English week titles, tabs and chat placeholder from being cut off.
    s.judgeWidths = true;
    await s.page.addInitScript(() => {
      try {
        localStorage.setItem("aiJudge.weekColumnWidth", "280");
        localStorage.setItem("aiJudge.chatColumnWidth", "380");
      } catch {}
    });
  }
  await s.page.setViewportSize({ width: VW, height });
  await s.go(`${ROUTE}?week=wk-${week}`, { wait: 900 });
  if (tab === "review") {
    await s.click(s.page.locator('[data-guide-tab="ai-judge-tab-review"]'), { wait: 900 });
  }
}
const weekRow = (s, n) => s.page.locator('[data-guide="ai-judge-sessions"] [role="listitem"]').nth(n - 1);
const assistant = (s) => s.page.locator('[data-guide="ai-judge-assistant"]');
const rubricCard = (s) => s.page.locator('[data-guide="ai-judge-rubric-items"]');
const composer = (s) => assistant(s).locator("textarea");
const studentRow = (s, name) => s.page.locator("article", { hasText: name }).first();
// The page without the navigation sidebar.
const main = (s) => { const vp = s.page.viewportSize(); return { x: 340, y: 0, width: vp.width - 340, height: vp.height }; };
// A locator's box widened to the left (context for the assistant column).
async function around(loc, left = 0, pad = 16) {
  const b = await loc.boundingBox();
  return { x: b.x - left - pad, y: b.y - pad, width: b.width + left + 2 * pad, height: b.height + 2 * pad };
}
const waitToasts = (s) => s.page.waitForFunction(() => !document.querySelector("[data-sonner-toast]"), null, { timeout: 8000 }).catch(() => {});

function chatFlow(m) {
  // First message: the assistant asks; second: it proposes.
  return (n, { body }) => {
    const c = m.state.checks[n];
    const user = msg("user", body?.content || "", { session_id: `chk-wk${n}`, at: iso(NOW) });
    if (!c.asked) {
      c.asked = true;
      const a = msg("assistant", m.L(...W6_QUESTION), { session_id: `chk-wk${n}`, at: iso(NOW), metadata_json: { tool_calls: [], status: "needs_information" } });
      c.msgs.push(user, a);
      return { __delay: 400, __body: { user_message: user, assistant_message: a, rubric_proposal: [], base_revision: c.rev } };
    }
    const a = proposalReply(m, n, c.rev);
    c.msgs.push(user, a);
    return { __delay: 400, __body: { user_message: user, assistant_message: a, rubric_proposal: a.metadata_json.rubric_proposal, base_revision: c.rev } };
  };
}

// ---------------------------------------------------------------- scene

export default {
  page: "ai-judge",
  role: "teacher",
  sections: [
    // s01 Find your way around the page
    {
      mocks(m) { world(m, { checks: base(m) }); },
      run: async (s) => {
        await open(s, 5);
        await s.shot(0, [s.page.locator('[data-guide="ai-judge-sessions"]'), rubricCard(s), assistant(s)], { crop: main(s), pad: 0 });
      },
    },
    // s02 Create this week's check
    {
      mocks(m) { world(m, { checks: base(m) }); },
      run: async (s) => {
        await open(s, 5);
        await s.click(weekRow(s, 6).locator("button").first(), { wait: 600 });
        await s.shot(1, weekRow(s, 6), { crop: main(s) });
        const create = s.page.getByRole("button", { name: s.t("Create this week's check", "建立這週的檢查") });
        await s.shot(2, create, { crop: main(s) });
        await s.click(create, { wait: 1200 });
        await waitToasts(s);
        await s.shot(3, [s.page.locator('[data-guide-tab="ai-judge-tab-rubrics"]'), rubricCard(s)], { crop: main(s), newFrame: true });
      },
    },
    // s03 Ask the assistant for check items
    {
      mocks(m) {
        world(m, { checks: { ...base(m), 6: { items: [], msgs: [], rev: 1 } } });
        m.state.chat = chatFlow(m);
        m.state.attachQueue = [
          { id: "att-1", original_filename: m.L("week6-rubric.docx", "第6週評分表.docx"), status: "ready" },
          { id: "att-2", original_filename: m.L("signup-form-spec.pdf", "報名表單規格.pdf"), status: "processing" },
        ];
      },
      run: async (s) => {
        await open(s, 6);
        await s.fill(composer(s), s.t(...W6_REQUEST), { wait: 300 });
        // The assistant column: its lower part (composer) and its upper part (conversation).
        const ab = await assistant(s).boundingBox();
        const ctx = { x: ab.x - 16, y: ab.y + ab.height - 340, width: ab.width + 32, height: 338 };
        const input = assistant(s).locator('input[type="file"]');
        await input.setInputFiles({ name: "rubric.docx", mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", buffer: Buffer.from("x") });
        await s.settle(400);
        await input.setInputFiles({ name: "spec.pdf", mimeType: "application/pdf", buffer: Buffer.from("x") });
        await s.settle(400);
        const attachBtn = assistant(s).getByRole("button", { name: s.t("Add attachment", "新增附件") });
        const chips = assistant(s).locator('[class*="chatAttachmentRail"]');
        await s.shot(1, composer(s), { crop: ctx });
        await s.shot(2, [attachBtn, chips], { crop: ctx });
        await s.shot(3, assistant(s).getByRole("button", { name: s.t("Send", "送出") }), { crop: ctx });
        // Remove the attachments again so the request is a plain message.
        for (let i = 0; i < 2; i++) {
          await s.click(assistant(s).getByRole("button", { name: new RegExp(s.t("Remove attachment", "移除附件")) }).first(), { wait: 300 });
        }
        await composer(s).focus();
        await s.press("Enter", { wait: 1200 });
        const question = assistant(s).locator('[class*="chatMsgRow"]').last();
        const qb = await question.boundingBox();
        await s.shot(4, question.locator('[class*="chatBubble"]'), { crop: { x: ab.x - 16, y: ab.y - 16, width: ab.width + 32, height: qb.y + qb.height + 40 - ab.y }, newFrame: true });
        await s.fill(composer(s), s.t(...W6_ANSWER), { wait: 200 });
        await composer(s).focus();
        await s.press("Enter", { wait: 1500 });
        await waitToasts(s);
        await s.shot(5, s.page.getByRole("region", { name: s.t("AI proposal", "AI 提案") }).or(s.page.locator('[class*="proposalPreview"]')).first(), { crop: main(s), newFrame: true });
      },
    },
    // s04 Apply or ignore a proposal
    {
      mocks(m) {
        world(m, { checks: { ...base(m), 6: { items: [], msgs: itemwiseMessages(m), rev: 1, source: "uploaded" } } });
      },
      run: async (s) => {
        await open(s, 6, { height: 1300 });
        const panel = s.page.locator('[class*="proposalPreview"]').first();
        const unselectable = panel.locator('[class*="proposalRow"]:not(label)');
        const n = await unselectable.count();
        const badges = [];
        for (let i = 0; i < n; i++) badges.push(unselectable.nth(i).locator('[class*="detBadge"]'));
        const tick = panel.locator('label[class*="proposalRow"]').nth(3).locator("input");
        await s.click(tick, { wait: 300 });
        // The checklist card from its top down to the end of the proposal box.
        const card = await rubricCard(s).boundingBox();
        const pb = await panel.boundingBox();
        const crop = { x: card.x - 8, y: card.y - 8, width: card.width + 16, height: pb.y + pb.height - card.y + 24 };
        await s.shot(1, panel.locator('button[class*="proposalToggle"]'), { crop });
        await s.shot(2, badges, { crop });
        // The number goes on Apply so it does not hide the unticked box.
        const row4 = panel.locator('label[class*="proposalRow"]').nth(3);
        await s.shot(3, [panel.getByRole("button", { name: s.t("Apply", "同意套用") }), row4.locator("b"), tick], { crop });
        await s.click(panel.getByRole("button", { name: s.t("Ignore", "忽略") }), { wait: 500 });
        const dlg = s.page.getByRole("dialog").or(s.page.getByRole("alertdialog")).last();
        await s.shot(4, dlg.getByRole("button", { name: s.t("Ignore", "忽略") }));
      },
    },
    // s05 Read and adjust the checklist
    {
      mocks(m) {
        world(m, { checks: { ...base(m), 6: { items: week6ReviewItems(m), msgs: [], rev: 4, reviewIds: ["w6-3"] } } });
      },
      run: async (s) => {
        await open(s, 6, { height: 1300 });
        await s.click(rubricCard(s).getByRole("button", { name: s.t("Expand settings for item 1", "展開第 1 項檢查設定") }), { wait: 400 });
        const badges = rubricCard(s).locator('[class*="detBadge"]');
        await s.shot(0, [rubricCard(s).locator('[class*="rubricDetailRow"]'), badges.nth(2), badges.nth(3)], { crop: rubricCard(s), pad: 12 });
      },
    },
    // s06 Build the check scripts
    {
      mocks(m) {
        world(m, { checks: { ...base(m), 6: { items: week6Items(m), msgs: [], rev: 2 } } });
        m.state.chat = (n, { body }) => {
          const c = m.state.checks[n];
          const user = msg("user", body?.content || "", { session_id: `chk-wk${n}`, at: iso(NOW) });
          if (m.state.buildFail) {
            const fixed = { ...week6Items(m)[1], detection_method: m.L("POST a test submission to http://localhost:5678/webhook/signup and expect HTTP 200.", "對 http://localhost:5678/webhook/signup 送出測試資料，確認回應 200。"), operation: "update" };
            const a = msg("assistant", m.L(
              "Item 4 \"Daily export CSV contains today's signups\" still needs the full path of the exported CSV file, so the scripts were not built. I also suggest spelling out the webhook URL in item 2.",
              "第 4 項「每日匯出的 CSV 含有當天報名資料」還缺少匯出 CSV 檔的完整路徑，所以這次沒有製作腳本。另外建議第 2 項寫明 Webhook 的完整網址。"),
            { session_id: `chk-wk${n}`, at: iso(NOW), metadata_json: { tool_calls: [{ status: "staged", operation: "update", title: fixed.title }], script_ready: false, status: "needs_information", is_refine: true } });
            c.msgs.push(a);
            return { __delay: 300, __body: { user_message: { ...user, metadata_json: { ui_hidden: true } }, assistant_message: a, rubric_proposal: [fixed], base_revision: c.rev } };
          }
          const a = msg("assistant", m.L("All 4 checkpoints are complete. Building the scripts now.", "4 個檢查點都已核對完成，開始製作腳本。"), { session_id: `chk-wk${n}`, at: iso(NOW), metadata_json: { tool_calls: [], script_ready: true, status: "ready" } });
          c.msgs.push(a);
          return { __delay: 2500, __body: { user_message: { ...user, metadata_json: { ui_hidden: true } }, assistant_message: a, rubric_proposal: [], base_revision: c.rev } };
        };
      },
      run: async (s) => {
        await open(s, 6);
        const build = s.page.locator('[data-guide="ai-judge-save-build"] button');
        await build.click();
        await s.page.waitForTimeout(900);
        // The button now reads "Reviewing items…".
        await s.shot(1, build, { crop: main(s) });
        await s.shot(2, s.page.locator('[data-workflow-status]').first(), { crop: main(s) });
        await s.page.locator('[data-guide-tab="ai-judge-tab-review"][aria-pressed="true"], [data-guide-tab="ai-judge-tab-review"][aria-checked="true"], [data-guide-tab="ai-judge-tab-review"][aria-selected="true"]').waitFor({ timeout: 10000 }).catch(() => {});
        await s.settle(1200);
        await s.page.evaluate(() => window.scrollTo(0, 0));
        await s.shot(3, [s.page.locator('[data-guide-tab="ai-judge-tab-review"]'), s.page.locator('[data-guide="ai-judge-review"] p[role="status"]').first()], { crop: main(s) });

        // A build that stops: one item still lacks a path.
        s.m.state.buildFail = true;
        s.m.state.checks[6] = { items: [...week6Items(m6(s)).slice(0, 3), week6ReviewItems(m6(s))[3], week6Items(m6(s))[3]], msgs: [], rev: 5 };
        await open(s, 6);
        await s.click(s.page.locator('[data-guide="ai-judge-save-build"] button'), { wait: 1200 });
        await waitToasts(s);
        await s.page.evaluate(() => window.scrollTo(0, 0));
        await s.shot(4, [s.page.locator('[data-workflow-status="error"]'), s.page.locator('[class*="proposalPreview"]').first()], { crop: main(s) });
      },
    },
    // s07 Run the checks
    {
      mocks(m) { world(m, { checks: base(m) }); },
      run: async (s) => {
        await open(s, 5, { tab: "review" });
        const review = s.page.locator('[data-guide="ai-judge-review"]');
        await s.click(studentRow(s, s.t("Grace Lee", "李佳穎")).locator("button").first(), { wait: 500 });
        await s.shot(1, review.locator('p[role="status"]').first(), { crop: main(s) });
        const runAll = review.getByRole("button", { name: s.t("Run all", "一次執行"), exact: true });
        const runReady = review.getByRole("button", { name: new RegExp(s.t("Check reported students", "檢查已回報的學生")) });
        const runOne = review.getByRole("button", { name: s.t("Check this student", "檢查這位學生") });
        await s.shot(2, [runAll, runReady, runOne], { crop: main(s), scroll: false });
        await s.click(runAll, { wait: 600 });
        await s.click(s.page.getByRole("dialog").getByRole("button", { name: s.t("Run", "確認執行"), exact: true }), { wait: 900 });
        await waitToasts(s);
        await s.shot(3, review.locator('div[aria-live="polite"][class*="runOnceProgress"]'), { crop: main(s) });

        // A run whose acknowledgement was lost.
        s.m.state.live = null;
        s.m.state.runFail = true;
        await open(s, 5, { tab: "review" });
        await s.click(review.getByRole("button", { name: s.t("Run all", "一次執行"), exact: true }), { wait: 600 });
        await s.click(s.page.getByRole("dialog").getByRole("button", { name: s.t("Run", "確認執行"), exact: true }), { wait: 1200 });
        await waitToasts(s);
        await s.shot(4, review.getByRole("button", { name: s.t("Confirm this run again", "重新確認執行") }), { crop: main(s) });
      },
    },
    // s08 Review the results
    {
      mocks(m) { world(m, { checks: base(m) }); },
      run: async (s) => {
        await open(s, 5, { tab: "review" });
        const review = s.page.locator('[data-guide="ai-judge-review"]');
        await s.shot(1, review.locator('[class*="reviewStats"]'), { crop: main(s) });
        await s.shot(2, review.locator('[class*="reviewSort"]'), { crop: main(s) });
        const grace = studentRow(s, s.t("Grace Lee", "李佳穎"));
        await s.shot(3, grace.locator("button").first(), { crop: main(s) });
        await s.click(grace.locator("button").first(), { wait: 500 });
        const pendingCheck = grace.locator('[class*="reviewCheckPending"]').first();
        await pendingCheck.locator("details summary").click();
        await s.settle(300);
        await s.shot(4, grace.locator('[class*="reviewMachineCard"]').first(), { crop: main(s) });
        await s.shot(5, [pendingCheck.locator('[class*="reviewDecision"]'), pendingCheck.locator("details")], { crop: main(s), scroll: false });
        await s.click(grace.locator("button").first(), { wait: 300 });
        const vivian = studentRow(s, s.t("Vivian Chung", "鍾佩珊"));
        await s.click(vivian.locator("button").first(), { wait: 500 });
        await s.shot(6, [vivian.locator('[class*="machineChips"]'), vivian.locator('[class*="reviewMachineHead"] [class*="badge"]').first()], { crop: main(s) });
      },
    },
    // s09 Reply to a student and save
    {
      mocks(m) { world(m, { checks: base(m) }); },
      run: async (s) => {
        await open(s, 5, { tab: "review" });
        const grace = studentRow(s, s.t("Grace Lee", "李佳穎"));
        await s.click(grace.locator("button").first(), { wait: 500 });
        const reply = grace.locator('[class*="replyBar"] textarea');
        await s.click(grace.locator('[class*="reviewDecision"] button').first(), { wait: 200 });
        await s.fill(reply, s.t("The signup rows look right. Next week, add a check that the email column is not empty.", "signup 的資料正確。下週可以再加上 email 欄位不能空白的檢查。"), { wait: 300 });
        await s.shot(1, reply, { min: [960, 480] });
        await s.shot(2, grace.locator('[class*="reviewSaveRow"] button'), { min: [960, 480] });

        // New results arrive while a decision is still unsaved.
        s.m.state.live = { id: "batch-2", status: "running", nodes: [["completed", 30, null], ["running", 22, null], ["running", 9, null]] };
        await open(s, 5, { tab: "review" });
        const grace2 = studentRow(s, s.t("Grace Lee", "李佳穎"));
        await s.click(grace2.locator("button").first(), { wait: 400 });
        await s.click(grace2.locator('[class*="reviewDecision"] button').first(), { wait: 200 });
        s.m.state.liveDone = true;
        const discard = s.page.getByRole("button", { name: s.t("Discard unsaved reviews and load", "放棄未存的核查並載入") });
        await discard.waitFor({ timeout: 10000 });
        await waitToasts(s);
        await s.page.evaluate(() => window.scrollTo(0, 0));
        await s.shot(3, discard.locator("xpath=..").first(), { min: [960, 420] });
      },
    },
    // s10 Move, copy or delete a week's check
    {
      mocks(m) { world(m, { checks: base(m) }); },
      run: async (s) => {
        await open(s, 5);
        const more = weekRow(s, 5).getByRole("button", { name: /.+/ }).last();
        await s.click(more, { wait: 400 });
        await s.shot(1, [more, s.page.getByRole("menu")], { crop: main(s) });
        await s.click(s.page.getByRole("menuitem", { name: s.t("Move to another week", "移到其他週") }), { wait: 400 });
        const dlg = s.page.getByRole("dialog").last();
        await dlg.locator("select").selectOption("wk-7");
        await s.settle(200);
        await s.shot(2, [dlg.locator("select"), dlg.getByRole("button", { name: s.t("Save week", "儲存週次") })]);
        await s.page.keyboard.press("Escape");

        s.m.state.allWeeks = true;
        await open(s, 5);
        const more2 = weekRow(s, 5).getByRole("button", { name: /.+/ }).last();
        await s.click(more2, { wait: 400 });
        await s.shot(3, [s.page.getByRole("menuitem", { name: s.t("Move to another week", "移到其他週") }), s.page.getByRole("menuitem", { name: s.t("Copy to another week", "複製到其他週") })], { crop: main(s) });
      },
    },
    // s11 Clear the conversation or view the source file
    {
      mocks(m) {
        const checks = base(m);
        checks[5] = { ...checks[5], source: "uploaded" };
        world(m, { checks });
      },
      run: async (s) => {
        await open(s, 5);
        const sources = assistant(s).getByRole("button", { name: s.t("Sources", "資料來源"), exact: true });
        await s.click(sources, { wait: 400 });
        await s.shot(0, [sources, assistant(s).getByRole("button", { name: s.t("Clear chat", "清除內容") }), assistant(s).locator('[class*="sourceRow"] button')], { crop: await around(assistant(s), 0, 6) });
      },
    },
  ],
};

// The scene functions only get the session; rebuild the language helper from it.
function m6(s) {
  return { L: (en, zh) => (s.lang === "en" ? en : zh) };
}
