// Shared mock data for the administrator system pages:
// admin-users (/admin), governance (/governance), audit (/audit), scheduler (/scheduler).
import { D, H, MIN, ago } from "../lib/mock.mjs";
import { me, people, users } from "./common.mjs";

// ---------------------------------------------------------------- users

// Every account on the User Management page (current admin included).
export function accountList(m) {
  const U = users(m);
  const P = people(m);
  const row = (u, extra = {}) => ({ ...me(u), registration_status: "approved", ...extra });
  const person = (id, email, full_name, role, extra = {}) => row({ id, email, full_name, role, is_superuser: false }, extra);
  return [
    person("7d3c1a2e-0201-4a00-9000-0000000000bb", "11356030@ntub.edu.tw", m.L("Mia Chou", "周佳蓉"), "student", { is_active: false, registration_status: "pending_review", created_at: ago(5 * H) }),
    person("7d3c1a2e-0202-4a00-9000-0000000000bb", "11356023@ntub.edu.tw", m.L("Ryan Hsieh", "謝承恩"), "student", { is_active: false, registration_status: "pending_review", created_at: ago(20 * H) }),
    person("7d3c1a2e-0203-4a00-9000-0000000000bb", "11356004@ntub.edu.tw", m.L("Olivia Kuo", "郭芷晴"), "student", { is_active: false, registration_status: "pending_review", created_at: ago(D + 3 * H) }),
    person("7d3c1a2e-0204-4a00-9000-0000000000bb", "11356011@ntub.edu.tw", m.L("Ethan Liao", "廖品睿"), "student", { is_active: false, registration_status: "pending_email", created_at: ago(2 * D) }),
    person("7d3c1a2e-0205-4a00-9000-0000000000bb", "11356019@ntub.edu.tw", m.L("Chloe Yang", "楊子涵"), "student", { is_active: false, registration_status: "rejected", created_at: ago(6 * D) }),
    row(U.admin, { totp_enabled: true, created_at: "2026-02-01T01:00:00Z" }),
    person("7d3c1a2e-0206-4a00-9000-0000000000bb", "ops@skylab.edu.tw", m.L("IT Center", "電算中心"), "admin", { is_superuser: true, totp_enabled: true, totp_required: true, created_at: "2026-02-03T02:00:00Z" }),
    row(U.teacher, { totp_enabled: true, created_at: "2026-02-10T01:00:00Z" }),
    row(P[5], { auth_source: "ldap", created_at: "2026-02-12T03:00:00Z" }),
    person("7d3c1a2e-0207-4a00-9000-0000000000bb", "wu.net@ntub.edu.tw", m.L("Prof. Wu", "吳俊宏"), "teacher", { auth_source: "ldap", created_at: "2026-02-15T06:00:00Z" }),
    row(U.student, { created_at: "2026-02-20T01:00:00Z" }),
    row(P[0], { created_at: "2026-02-21T02:30:00Z" }),
    row(P[1], { totp_enabled: true, created_at: "2026-02-21T05:10:00Z" }),
    row(P[2], { created_at: "2026-02-22T01:40:00Z" }),
    row(P[3], { auth_source: "ldap", created_at: "2026-02-24T08:00:00Z" }),
    row(P[4], { created_at: "2026-02-25T02:00:00Z" }),
    person("7d3c1a2e-0208-4a00-9000-0000000000bb", "11256052@ntub.edu.tw", m.L("Jason Lin", "林冠廷"), "student", { created_at: "2026-03-02T03:00:00Z" }),
    person("7d3c1a2e-0209-4a00-9000-0000000000bb", "11156067@ntub.edu.tw", m.L("Sophia Tsai", "蔡宜蓁"), "student", { is_active: false, created_at: "2026-03-04T03:00:00Z" }),
    person("7d3c1a2e-0210-4a00-9000-0000000000bb", "11256078@ntub.edu.tw", m.L("Daniel Hsu", "許家豪"), "student", { auth_source: "ldap", created_at: "2026-03-09T05:00:00Z" }),
    person("7d3c1a2e-0211-4a00-9000-0000000000bb", "11156083@ntub.edu.tw", m.L("Hannah Ho", "何思妤"), "student", { created_at: "2026-03-11T01:00:00Z" }),
    person("7d3c1a2e-0212-4a00-9000-0000000000bb", "11256094@ntub.edu.tw", m.L("Leo Chiang", "江宥廷"), "student", { is_active: false, created_at: "2026-03-15T07:00:00Z" }),
  ];
}

// GET /users/, /users/registration-policy, /users/pending-registrations and the write endpoints.
export function useAccounts(m, { policy = { signup_enabled: true, approval_required: true, google_signup_enabled: false, student_email_domain: "school.edu.tw" }, list = null } = {}) {
  const st = m.state;
  st.accounts = list ?? accountList(m);
  st.policy = { ...policy };
  const find = (id) => st.accounts.find((u) => u.id === id);
  m.use([
    ["GET", /^\/api\/v1\/users\/$/, () => ({ data: st.accounts, count: st.accounts.length })],
    ["GET", /^\/api\/v1\/users\/registration-policy$/, () => st.policy],
    ["PUT", /^\/api\/v1\/users\/registration-policy$/, ({ body }) => (st.policy = { ...st.policy, ...body })],
    ["GET", /^\/api\/v1\/users\/pending-registrations$/, () => {
      const data = st.accounts.filter((u) => u.registration_status === "pending_review");
      return { data, count: data.length };
    }],
    ["POST", /^\/api\/v1\/users\/([^/]+)\/registration\/(approve|reject)$/, ({ mt }) => {
      const u = find(mt[1]);
      Object.assign(u, mt[2] === "approve" ? { registration_status: "approved", is_active: true } : { registration_status: "rejected" });
      return u;
    }],
    ["POST", /^\/api\/v1\/users\/$/, ({ body }) => {
      const u = { ...me({ id: "7d3c1a2e-0300-4a00-9000-0000000000cc", ...body, is_superuser: body.role === "admin" }), ...body, registration_status: "approved", created_at: ago(0) };
      st.accounts.unshift(u);
      return u;
    }],
    ["PATCH", /^\/api\/v1\/users\/([^/]+)$/, ({ mt, body }) => {
      const u = find(mt[1]);
      Object.assign(u, body || {});
      return st.patchDelay ? { __delay: st.patchDelay, __body: u } : u;
    }],
    ["DELETE", /^\/api\/v1\/users\/([^/]+)\/totp$/, ({ mt }) => ({ ...find(mt[1]), totp_enabled: false })],
    ["DELETE", /^\/api\/v1\/users\/([^/]+)$/, ({ mt }) => {
      st.accounts = st.accounts.filter((u) => u.id !== mt[1]);
      return { message: "ok" };
    }],
  ]);
}

// ---------------------------------------------------------------- user groups

// Administrator-managed groups, one school class each. On User Management the counts come from the
// accounts in accountList(); the teacher's class pages have no account list and see class-sized counts.
export function useUserGroups(m) {
  const st = m.state;
  let next = 1;
  const group = (name, emails, size, days) => ({
    id: `5b8e2f10-${String(next++).padStart(4, "0")}-4a00-9000-0000000000dd`, name, emails: [...emails], size, created_at: ago(days * D),
  });
  st.groups = [
    group(m.L("IM Year 3 Class B", "資管三乙"), ["11256003@ntub.edu.tw", "11256021@ntub.edu.tw", "11256045@ntub.edu.tw", "11256052@ntub.edu.tw",
      "11256078@ntub.edu.tw", "11256094@ntub.edu.tw", "lin@ntub.edu.tw"], [47, 46], 40),
    group(m.L("IM Year 4 Class A", "資管四甲"), ["11156001@ntub.edu.tw", "11156012@ntub.edu.tw", "11156034@ntub.edu.tw", "11156067@ntub.edu.tw",
      "11156083@ntub.edu.tw"], [44, 44], 40),
    group(m.L("Course TAs", "課程助教"), ["11156012@ntub.edu.tw", "11256021@ntub.edu.tw"], [2, 2], 12),
  ];
  const members = (g) => (st.accounts ?? []).filter((u) => g.emails.includes(u.email));
  const pub = (g) => {
    const base = { id: g.id, name: g.name, created_at: g.created_at };
    if (!st.accounts) return { ...base, member_count: g.size[0], student_count: g.size[1] };
    const list = members(g);
    return { ...base, member_count: list.length, student_count: list.filter((u) => u.role === "student").length };
  };
  const find = (id) => st.groups.find((g) => g.id === id);
  const result = (g, extra) => ({ added: 0, removed: 0, not_found: [], ...extra, group: pub(g) });
  const emailsOf = (ids) => (st.accounts ?? []).filter((u) => ids.includes(u.id)).map((u) => u.email);
  m.use([
    ["GET", /^\/api\/v1\/user-groups\/$/, () => ({ data: st.groups.map(pub), count: st.groups.length })],
    ["POST", /^\/api\/v1\/user-groups\/$/, ({ body }) => {
      const g = group(body.name, [], [0, 0], 0);
      st.groups.push(g);
      return pub(g);
    }],
    ["PATCH", /^\/api\/v1\/user-groups\/([^/]+)$/, ({ mt, body }) => {
      const g = find(mt[1]);
      g.name = body.name;
      return pub(g);
    }],
    ["DELETE", /^\/api\/v1\/user-groups\/([^/]+)$/, ({ mt }) => {
      st.groups = st.groups.filter((g) => g.id !== mt[1]);
      return { message: "ok" };
    }],
    ["GET", /^\/api\/v1\/user-groups\/([^/]+)\/members$/, ({ mt }) => {
      const data = members(find(mt[1])).map(({ id, email, full_name, role, is_active }) => ({ id, email, full_name, role, is_active }));
      return { data, count: data.length };
    }],
    ["POST", /^\/api\/v1\/user-groups\/([^/]+)\/members$/, ({ mt, body }) => {
      const g = find(mt[1]);
      const fresh = emailsOf(body?.user_ids ?? []).filter((e) => !g.emails.includes(e));
      g.emails.push(...fresh);
      return result(g, { added: fresh.length });
    }],
    ["POST", /^\/api\/v1\/user-groups\/([^/]+)\/members\/remove$/, ({ mt, body }) => {
      const g = find(mt[1]);
      const gone = emailsOf(body?.user_ids ?? []).filter((e) => g.emails.includes(e));
      g.emails = g.emails.filter((e) => !gone.includes(e));
      return result(g, { removed: gone.length });
    }],
    // The scenes upload a roster of two known students and one ID that has no account.
    ["POST", /^\/api\/v1\/user-groups\/([^/]+)\/members\/import-csv$/, ({ mt }) => {
      const g = find(mt[1]);
      const fresh = ["11256045@ntub.edu.tw", "11156034@ntub.edu.tw"].filter((e) => !g.emails.includes(e));
      g.emails.push(...fresh);
      return { __delay: 300, __body: result(g, { added: fresh.length, not_found: ["11256999@ntub.edu.tw"] }) };
    }],
  ]);
}

// ---------------------------------------------------------------- governance and quotas

export const governanceConfig = () => ({
  alerts_enabled: true,
  alert_cpu_threshold: 90,
  alert_memory_threshold: 90,
  alert_disk_threshold: 85,
  alert_cooldown_minutes: 30,
  alert_check_interval_seconds: 60,
  alert_email_enabled: true,
  ttl_enabled: true,
  expiry_warn_days: 3,
  expiry_grace_delete_days: 7,
  idle_detection_enabled: true,
  idle_cpu_threshold_percent: 1,
  idle_window_hours: 48,
  idle_notify_after_hours: 12,
  idle_grace_hours: 24,
  idle_scan_batch_size: 20,
  workload_advisor_enabled: true,
  mining_detection_enabled: true,
  mining_cpu_threshold_percent: 90,
  mining_window_hours: 6,
  mining_scan_batch_size: 20,
  mining_auto_suspend: true,
  provision_max_concurrency: 4,
  snapshot_cleanup_enabled: true,
  snapshot_retention_days: 7,
  student_snapshot_max_count: 3,
  student_backup_max_count: 2,
  updated_at: ago(9 * D),
});

export function useGovernance(m, { config = {}, overrides = null } = {}) {
  const st = m.state;
  st.gov = { ...governanceConfig(), ...config };
  st.globalQuota = { max_cpu_cores: 8, max_memory_mb: 16384, max_disk_gb: 100, max_instances: 5, updated_at: ago(12 * D + 3 * H) };
  st.quotas = overrides ?? [
    { id: "q-01", user_id: users(m).teacher.id, user_email: "lin@ntub.edu.tw", max_cpu_cores: 32, max_memory_mb: 65536, max_disk_gb: 500, max_instances: 20, created_at: ago(30 * D) },
    { id: "q-02", user_id: "7d3c1a2e-0105-4a00-9000-0000000000aa", user_email: "chen@ntub.edu.tw", max_cpu_cores: 24, max_memory_mb: 49152, max_disk_gb: 0, max_instances: 15, created_at: ago(28 * D) },
    { id: "q-03", user_id: "7d3c1a2e-0101-4a00-9000-0000000000aa", user_email: "11156012@ntub.edu.tw", max_cpu_cores: 16, max_memory_mb: 32768, max_disk_gb: 200, max_instances: 8, created_at: ago(6 * D) },
    { id: "q-04", user_id: "7d3c1a2e-0208-4a00-9000-0000000000bb", user_email: "11256052@ntub.edu.tw", max_cpu_cores: 12, max_memory_mb: 24576, max_disk_gb: 150, max_instances: 0, created_at: ago(2 * D) },
  ];
  m.use([
    ["GET", /^\/api\/v1\/governance\/config$/, () => st.gov],
    ["PUT", /^\/api\/v1\/governance\/config$/, ({ body }) => (st.gov = { ...st.gov, ...body, updated_at: ago(0) })],
    ["GET", /^\/api\/v1\/quotas$/, () => st.quotas],
    ["GET", /^\/api\/v1\/quotas\/global$/, () => st.globalQuota],
    ["PUT", /^\/api\/v1\/quotas\/global$/, ({ body }) => (st.globalQuota = { ...st.globalQuota, ...body, updated_at: ago(0) })],
    ["POST", /^\/api\/v1\/quotas$/, ({ body }) => {
      const u = (st.accounts ?? accountList(m)).find((x) => x.id === body.user_id);
      const q = { id: "q-new", user_email: u?.email ?? null, created_at: ago(0), ...body };
      st.quotas = [...st.quotas, q];
      return q;
    }],
    ["PUT", /^\/api\/v1\/quotas\/([^/]+)$/, ({ mt, body }) => {
      const q = st.quotas.find((x) => x.id === mt[1]);
      Object.assign(q, body);
      return q;
    }],
    ["DELETE", /^\/api\/v1\/quotas\/([^/]+)$/, ({ mt }) => {
      st.quotas = st.quotas.filter((x) => x.id !== mt[1]);
      return undefined;
    }],
  ]);
  // The user picker in the quota dialog lists every account page by page.
  if (!st.accounts) {
    st.accounts = accountList(m);
    m.use([["GET", /^\/api\/v1\/users\/$/, () => ({ data: st.accounts, count: st.accounts.length })]]);
  }
}

// ---------------------------------------------------------------- scheduler

export const schedulerConfig = () => ({
  cpu_overcommit_ratio: 2,
  disk_overcommit_ratio: 1,
  memory_overcommit_ratio: 1,
  placement_reassignment_cost: 0.15,
  placement_peak_cpu_margin: 1.1,
  placement_peak_memory_margin: 1.05,
  placement_loadavg_warn_per_core: 0.8,
  placement_loadavg_max_per_core: 1.5,
  placement_loadavg_penalty_weight: 0.9,
  placement_disk_contention_warn_share: 0.7,
  placement_disk_contention_high_share: 0.9,
  placement_disk_penalty_weight: 0.75,
  placement_cpu_peak_warn_share: 0.7,
  placement_cpu_peak_high_share: 1.2,
  placement_memory_peak_warn_share: 0.8,
  placement_memory_peak_high_share: 0.85,
  placement_resource_weight_cpu: 1,
  placement_resource_weight_memory: 1,
  placement_resource_weight_disk: 1,
  scheduled_boot_batch_size: 5,
  scheduled_boot_batch_interval_seconds: 10,
  scheduled_boot_lead_time_minutes: 5,
  window_grace_period_minutes: 30,
  practice_session_hours: 3,
  practice_warning_minutes: 30,
  expiry_warning_hours: 24,
  updated_at: ago(15 * D),
  is_configured: true,
});

export function useScheduler(m) {
  const st = m.state;
  st.sched = schedulerConfig();
  m.use([
    ["GET", /^\/api\/v1\/proxmox-config\/$/, () => st.sched],
    ["PUT", /^\/api\/v1\/proxmox-config\/$/, ({ body }) => (st.sched = { ...st.sched, ...body, updated_at: ago(0) })],
  ]);
}

// ---------------------------------------------------------------- audit log

export const AUDIT_ACTIONS = [
  ["login_success", "auth"], ["login_failed", "auth"], ["login_google_success", "auth"], ["login_ldap_success", "auth"],
  ["login_ldap_failed", "auth"], ["login_totp_failed", "auth"], ["password_change", "auth"], ["totp_enable", "auth"], ["totp_admin_reset", "auth"],
  ["vm_create", "resource"], ["lxc_create", "resource"], ["resource_start", "resource"], ["resource_stop", "resource"],
  ["resource_reboot", "resource"], ["resource_reset", "resource"], ["resource_delete", "resource"],
  ["snapshot_create", "resource"], ["snapshot_delete", "resource"], ["backup_create", "resource"], ["backup_restore", "resource"],
  ["vm_request_submit", "request"], ["vm_request_review", "request"], ["vm_request_expired", "request"], ["spec_change_request", "request"],
  ["user_create", "user"], ["user_update", "user"], ["user_delete", "user"],
  ["firewall_rule_create", "firewall"], ["firewall_rule_delete", "firewall"], ["firewall_connection_create", "firewall"],
  ["gateway_config_update", "gateway"], ["config_update", "system"], ["mining_detected", "system"], ["ai_api_request_review", "ai"],
].map(([value, category]) => ({ value, category }));

export function auditLogs(m) {
  const U = users(m);
  const P = people(m);
  const who = {
    admin: [U.admin.id, U.admin.email, U.admin.full_name],
    lin: [U.teacher.id, U.teacher.email, U.teacher.full_name],
    chen: [P[5].id, P[5].email, P[5].full_name],
    alex: [U.student.id, U.student.email, U.student.full_name],
    emily: [P[0].id, P[0].email, P[0].full_name],
    kevin: [P[1].id, P[1].email, P[1].full_name],
    grace: [P[2].id, P[2].email, P[2].full_name],
    brian: [P[3].id, P[3].email, P[3].full_name],
    iris: [P[4].id, P[4].email, P[4].full_name],
    sys: [null, null, null],
  };
  const L = m.L;
  // [minutes ago, who, action, details, vmid, ip]
  const rows = [
    [2, "alex", "resource_start", L("Started LXC flask-blog-lab (10312)", "啟動 LXC flask-blog-lab（10312）"), 10312, "10.20.31.45"],
    [4, "sys", "resource_stop", L("Practice session ended: auto-stopped docker-lab (10318) after 3 hours", "練習時段結束：自動關閉 docker-lab（10318），已運行 3 小時"), 10318, null],
    [7, "kevin", "login_failed", L("Password login failed for 11156012@ntub.edu.tw: incorrect password", "11156012@ntub.edu.tw 密碼登入失敗：密碼錯誤"), null, "140.131.77.102"],
    [9, "kevin", "login_success", L("Password login", "密碼登入"), null, "140.131.77.102"],
    [12, "admin", "vm_request_review", L("Approved VM request jupyter-ml-lab from 11256003@ntub.edu.tw (4 cores, 8 GB, 2026-10-14 to 2026-12-31); comment: OK for the machine learning course project", "核准 11256003@ntub.edu.tw 的申請 jupyter-ml-lab（4 核、8 GB，2026-10-14 至 2026-12-31）；意見：機器學習課程專題，同意"), null, "10.20.1.15"],
    [15, "lin", "firewall_rule_create", L("Opened TCP 8080 from 10.20.0.0/16 to web-midterm-01 (10421)", "開放 10.20.0.0/16 到 web-midterm-01（10421）的 TCP 8080"), 10421, "10.20.5.22"],
    [18, "sys", "snapshot_delete", L("Snapshot governance: deleted snapshot before-upgrade of ubuntu-desktop (10304), older than 7 days", "快照治理：刪除 ubuntu-desktop（10304）的快照 before-upgrade，已超過 7 天"), 10304, null],
    [22, "emily", "vm_request_submit", L("Submitted request jupyter-ml-lab: VM, Ubuntu 24.04 LTS, 4 cores, 8192 MB, 64 GB", "送出申請 jupyter-ml-lab：VM、Ubuntu 24.04 LTS、4 核、8192 MB、64 GB"), null, "10.20.31.88"],
    [26, "grace", "snapshot_create", L("Created snapshot week5-done on redis-cache (10322)", "在 redis-cache（10322）建立快照 week5-done"), 10322, "10.20.32.17"],
    [31, "brian", "login_ldap_success", L("LDAP login", "LDAP 登入"), null, "10.20.33.61"],
    [35, "sys", "resource_start", L("Scheduled boot: started 5 machines of class Network Engineering (batch 1 of 6)", "排程開機：啟動「網路工程」班級機器 5 台（第 1／6 批）"), null, null],
    [38, "admin", "user_update", L("Updated user 11156067@ntub.edu.tw: is_active true → false", "更新使用者 11156067@ntub.edu.tw：is_active true → false"), null, "10.20.1.15"],
    [44, "iris", "resource_reboot", L("Rebooted VM win11-office (10330)", "重新開機 VM win11-office（10330）"), 10330, "10.20.34.9"],
    [52, "chen", "lxc_create", L("Created LXC db-lab-01 (10425) on pve-02 from template Ubuntu 24.04 MySQL", "在 pve-02 以範本 Ubuntu 24.04 MySQL 建立 LXC db-lab-01（10425）"), 10425, "10.20.5.30"],
    [58, "kevin", "login_totp_failed", L("Two-step verification failed: invalid code", "兩步驟驗證失敗：驗證碼錯誤"), null, "140.131.77.102"],
    [65, "alex", "backup_create", L("Created backup of flask-blog-lab (10312) on pbs-store", "在 pbs-store 建立 flask-blog-lab（10312）的備份"), 10312, "10.20.31.45"],
    [72, "admin", "config_update", L("Updated governance settings: idle_grace_hours 48 → 24", "更新治理設定：idle_grace_hours 48 → 24"), null, "10.20.1.15"],
    [80, "sys", "vm_request_expired", L("Request summer-project from 11156001@ntub.edu.tw expired; resource 10296 queued for deletion", "11156001@ntub.edu.tw 的申請 summer-project 已到期，資源 10296 排入刪除佇列"), 10296, null],
    [86, "lin", "user_create", L("Created user 11356041@ntub.edu.tw (student)", "建立使用者 11356041@ntub.edu.tw（學生）"), null, "10.20.5.22"],
    [95, "emily", "login_google_success", L("Google login", "Google 登入"), null, "10.20.31.88"],
    [104, "brian", "resource_reset", L("One-click reset of class machine net-lab-07 (10512) to template Debian 12 Router", "一鍵重置班級機器 net-lab-07（10512），回到範本 Debian 12 Router"), 10512, "10.20.33.61"],
    [118, "admin", "gateway_config_update", L("Updated Gateway HTTP rules: added lab.skylab.edu.tw → 10.20.40.12:8080", "更新 Gateway HTTP 規則：新增 lab.skylab.edu.tw → 10.20.40.12:8080"), null, "10.20.1.15"],
    [131, "grace", "resource_stop", L("Stopped LXC redis-cache (10322)", "關閉 LXC redis-cache（10322）"), 10322, "10.20.32.17"],
    [140, "sys", "mining_detected", L("Suspected mining on cuda-training (10327): CPU above 90% for 6 hours; snapshot taken and machine suspended", "cuda-training（10327）疑似挖礦：CPU 連續 6 小時高於 90%；已存證快照並暫停"), 10327, null],
    [152, "iris", "password_change", L("Changed account password", "變更帳號密碼"), null, "10.20.34.9"],
    [166, "chen", "firewall_connection_create", L("Connected db-lab-01 (10425) to web-lab-01 (10424) on TCP 3306", "連接 db-lab-01（10425）到 web-lab-01（10424）的 TCP 3306"), 10425, "10.20.5.30"],
    [175, "admin", "totp_admin_reset", L("Reset two-factor authentication for 11156012@ntub.edu.tw (lost phone)", "重設 11156012@ntub.edu.tw 的兩步驟驗證（手機遺失）"), null, "10.20.1.15"],
    [190, "alex", "spec_change_request", L("Requested spec change for flask-blog-lab (10312): memory 4096 MB → 8192 MB", "申請調整 flask-blog-lab（10312）規格：記憶體 4096 MB → 8192 MB"), 10312, "10.20.31.45"],
    [204, "lin", "vm_create", L("Created VM web-midterm-01 (10421) on pve-03 from template Ubuntu 24.04 LAMP", "在 pve-03 以範本 Ubuntu 24.04 LAMP 建立 VM web-midterm-01（10421）"), 10421, "10.20.5.22"],
    [219, "sys", "resource_shutdown", L("Idle detection: shut down jupyter-old (10288) after 24 hours idle below 1% CPU", "閒置偵測：jupyter-old（10288）CPU 低於 1% 已 24 小時，自動關機"), 10288, null],
    [233, "kevin", "resource_start", L("Started VM cuda-training (10327)", "啟動 VM cuda-training（10327）"), 10327, "140.131.77.102"],
    [248, "admin", "resource_delete", L("Deleted LXC old-notes (10275) owned by 11156001@ntub.edu.tw", "刪除 11156001@ntub.edu.tw 的 LXC old-notes（10275）"), 10275, "10.20.1.15"],
    [262, "emily", "login_success", L("Password login", "密碼登入"), null, "10.20.31.88"],
    [281, "brian", "firewall_rule_delete", L("Removed rule TCP 22 from 0.0.0.0/0 on net-lab-07 (10512)", "移除 net-lab-07（10512）上 0.0.0.0/0 的 TCP 22 規則"), 10512, "10.20.33.61"],
    [297, "grace", "lxc_create", L("Created LXC redis-cache (10322) on pve-01 from request redis-cache", "依申請 redis-cache 在 pve-01 建立 LXC redis-cache（10322）"), 10322, null],
    [315, "sys", "resource_start", L("Scheduled boot: started 4 machines of class Operating Systems (batch 2 of 2)", "排程開機：啟動「作業系統」班級機器 4 台（第 2／2 批）"), null, null],
    [330, "iris", "login_failed", L("Password login failed for 11256045@ntub.edu.tw: incorrect password", "11256045@ntub.edu.tw 密碼登入失敗：密碼錯誤"), null, "10.20.34.9"],
    [344, "chen", "vm_request_review", L("Rejected request game-server from 11156001@ntub.edu.tw; comment: Please use the class machine for this assignment", "拒絕 11156001@ntub.edu.tw 的申請 game-server；意見：這份作業請使用班級機器"), null, "10.20.5.30"],
    [362, "alex", "snapshot_create", L("Created snapshot before-flask-upgrade on flask-blog-lab (10312)", "在 flask-blog-lab（10312）建立快照 before-flask-upgrade"), 10312, "10.20.31.45"],
    [380, "admin", "user_delete", L("Deleted user 10956077@ntub.edu.tw (graduated)", "刪除使用者 10956077@ntub.edu.tw（已畢業）"), null, "10.20.1.15"],
    [401, "kevin", "totp_enable", L("Enabled two-factor authentication", "啟用兩步驟驗證"), null, "140.131.77.102"],
    [423, "lin", "backup_restore", L("Restored backup 2026-10-11 02:00 to web-midterm-01 (10421)", "將 2026-10-11 02:00 的備份還原到 web-midterm-01（10421）"), 10421, "10.20.5.22"],
    [445, "sys", "snapshot_delete", L("Snapshot governance: deleted snapshot lab3 of db-lab-01 (10425), older than 7 days", "快照治理：刪除 db-lab-01（10425）的快照 lab3，已超過 7 天"), 10425, null],
    [468, "brian", "login_ldap_failed", L("LDAP login failed for 11156034: invalid credentials", "11156034 LDAP 登入失敗：帳號或密碼錯誤"), null, "10.20.33.61"],
    [492, "emily", "resource_stop", L("Stopped VM jupyter-ml-lab (10319)", "關閉 VM jupyter-ml-lab（10319）"), 10319, "10.20.31.88"],
    [515, "admin", "ai_api_request_review", L("Approved AI API request from lin@ntub.edu.tw: 2 keys, rate limit 60 requests/min", "核准 lin@ntub.edu.tw 的 AI API 申請：2 把金鑰、每分鐘 60 次"), null, "10.20.1.15"],
    [540, "grace", "login_success", L("Password login", "密碼登入"), null, "10.20.32.17"],
    [566, "chen", "resource_start", L("Started LXC db-lab-01 (10425)", "啟動 LXC db-lab-01（10425）"), 10425, "10.20.5.30"],
    [590, "sys", "resource_stop", L("Usage window ended: stopped 24 machines of class Network Engineering after the 30-minute grace period", "使用時段結束：寬限 30 分鐘後關閉「網路工程」班級機器 24 台"), null, null],
    [612, "iris", "vm_create", L("Created VM win11-office (10330) on pve-04 from request win11-office", "依申請 win11-office 在 pve-04 建立 VM win11-office（10330）"), 10330, null],
  ];
  return rows.map(([mins, k, action, details, vmid, ip], i) => {
    const [user_id, user_email, user_full_name] = who[k];
    return {
      id: `a0d1e5c2-${String(9000 - i).padStart(4, "0")}-4b00-9000-00000000a${String(i).padStart(3, "0")}`,
      user_id, user_email, user_full_name, vmid, resource_vmid: vmid, action, details, ip_address: ip,
      user_agent: ip ? "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/141.0" : null,
      created_at: ago(mins * MIN),
    };
  });
}

export function useAudit(m, { stats = true } = {}) {
  const all = auditLogs(m);
  const P = people(m);
  const U = users(m);
  const TOTAL = 1286;
  const filtered = (q) => all.filter((r) =>
    (!q.search || r.details.toLowerCase().includes(q.search.toLowerCase()) || (r.ip_address ?? "").includes(q.search)) &&
    (!q.action || r.action === q.action) &&
    (!q.user_id || r.user_id === q.user_id) &&
    (!q.start_time || r.created_at >= q.start_time) &&
    (!q.end_time || r.created_at <= q.end_time));
  const anyFilter = (q) => q.search || q.action || q.user_id || q.start_time || q.end_time;
  m.use([
    ["GET", /^\/api\/v1\/audit-logs\/$/, ({ query }) => {
      const rows = filtered(query);
      const skip = Number(query.skip || 0);
      // No filter: pretend the log holds TOTAL entries (page 2+ repeats the sample with older times).
      if (!anyFilter(query)) {
        const shift = skip ? (620 + skip * 12) * MIN : 0;
        const data = all.slice(0, 50).map((r) => ({ ...r, id: `${r.id}-${skip}`, created_at: new Date(Date.parse(r.created_at) - shift).toISOString() }));
        return { data, count: TOTAL };
      }
      return { data: rows.slice(skip, skip + 50), count: rows.length };
    }],
    ["GET", /^\/api\/v1\/audit-logs\/stats$/, ({ query }) => {
      if (!stats) return { __status: 500, __body: { detail: "Internal Server Error" } };
      return query.start_time || query.end_time
        ? { total: 342, danger: 3, login_failed: 9, active_users: 126 }
        : { total: TOTAL, danger: 14, login_failed: 37, active_users: 212 };
    }],
    ["GET", /^\/api\/v1\/audit-logs\/actions$/, () => AUDIT_ACTIONS],
    ["GET", /^\/api\/v1\/audit-logs\/users$/, () => [U.admin, U.teacher, P[5], U.student, ...P.slice(0, 5)].map((u) => ({ id: u.id, email: u.email, full_name: u.full_name }))],
    ["GET", /^\/api\/v1\/audit-logs\/export$/, () => ({
      __contentType: "text/csv; charset=utf-8",
      __text: "﻿id,created_at,action,user_email,user_full_name,vmid,ip_address,user_agent,details\n" +
        all.map((r) => [r.id, r.created_at, r.action, r.user_email ?? "", r.user_full_name ?? "", r.vmid ?? "", r.ip_address ?? "", r.user_agent ?? "", `"${r.details}"`].join(",")).join("\n"),
    })],
  ]);
}

// ---------------------------------------------------------------- scene helpers (system pages)

// Scrolls an element to the middle of its scroll container.
export const center = (loc) => loc.evaluate((el) => el.scrollIntoView({ block: "center" }));

// Crop rectangle covering several elements (viewport px).
export async function around(locs, pad = 20) {
  const bs = [];
  for (const l of locs) bs.push(await l.boundingBox());
  const x = Math.min(...bs.map((b) => b.x)) - pad;
  const y = Math.min(...bs.map((b) => b.y)) - pad;
  return { x, y, width: Math.max(...bs.map((b) => b.x + b.width)) + pad - x, height: Math.max(...bs.map((b) => b.y + b.height)) + pad - y };
}

// Main content column (right of the sidebar), from y with height h.
export const content = (y = 0, h = 900) => ({ x: 340, y, width: 1100, height: h });
