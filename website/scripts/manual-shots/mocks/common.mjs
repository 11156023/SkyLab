// Accounts, sign-in and the endpoints every signed-in layout calls.
// Scene files add their own page data with m.use([...]).

import { D, NOW } from "../lib/mock.mjs";

const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
// Expires a week after the frozen clock: a far-future expiry makes the frontend's
// refresh timer overflow setTimeout and refresh in a tight loop.
export const fakeJwt = (sub) =>
  `${b64({ alg: "HS256", typ: "JWT" })}.${b64({ sub, exp: Math.floor((NOW + 7 * D) / 1000), type: "access" })}.c2lnbmF0dXJl`;

export function users(m) {
  return {
    student: { id: "7d3c1a2e-0001-4a00-9000-000000000001", email: "11156001@ntub.edu.tw", full_name: m.L("Alex Wang", "王小明"), role: "student", is_superuser: false },
    teacher: { id: "7d3c1a2e-0002-4a00-9000-000000000002", email: "lin@ntub.edu.tw", full_name: m.L("Prof. Lin", "林志明"), role: "teacher", is_superuser: false },
    admin: { id: "7d3c1a2e-0003-4a00-9000-000000000003", email: "admin@skylab.edu.tw", full_name: m.L("System Admin", "系統管理員"), role: "admin", is_superuser: true },
  };
}

// Extra people for lists (user pickers, rosters, review queues).
export function people(m) {
  return [
    ["7d3c1a2e-0100-4a00-9000-0000000000aa", "11256003@ntub.edu.tw", m.L("Emily Chang", "張雅婷"), "student"],
    ["7d3c1a2e-0101-4a00-9000-0000000000aa", "11156012@ntub.edu.tw", m.L("Kevin Chen", "陳冠宇"), "student"],
    ["7d3c1a2e-0102-4a00-9000-0000000000aa", "11256021@ntub.edu.tw", m.L("Grace Lee", "李佳穎"), "student"],
    ["7d3c1a2e-0103-4a00-9000-0000000000aa", "11156034@ntub.edu.tw", m.L("Brian Huang", "黃柏翰"), "student"],
    ["7d3c1a2e-0104-4a00-9000-0000000000aa", "11256045@ntub.edu.tw", m.L("Iris Wu", "吳欣怡"), "student"],
    ["7d3c1a2e-0105-4a00-9000-0000000000aa", "chen@ntub.edu.tw", m.L("Prof. Chen", "陳美玲"), "teacher"],
  ].map(([id, email, full_name, role]) => ({ id, email, full_name, role, is_superuser: false }));
}

export function me(u, extra = {}) {
  return {
    id: u.id,
    email: u.email,
    full_name: u.full_name,
    avatar_url: null,
    role: u.role,
    is_superuser: u.is_superuser,
    is_active: true,
    auth_source: "local",
    onboarding_completed: true,
    totp_enabled: false,
    totp_required: false,
    totp_setup_required: false,
    created_at: "2026-02-10T01:00:00Z",
    ...extra,
  };
}

export function registerCommon(m) {
  const U = users(m);
  m.users = U;
  m.currentUser = () => U[m.state.role || "student"];
  m.register([
    ["GET", /^\/api\/v1\/setup\/status$/, () => ({ completed: true, completed_at: "2026-02-01T00:00:00Z", steps: { admin: true, proxmox: true, subnet: true, gateway: true, platform_entry: true } })],
    ["GET", /^\/api\/v1\/login\/methods$/, () => ({ password: true, google: false, ldap: false, turnstile_site_key: "" })],
    ["POST", /^\/api\/v1\/login\/access-token$/, ({ body }) => {
      const email = String(body?.username || "").toLowerCase();
      const role = Object.keys(U).find((k) => U[k].email === email) || "student";
      m.state.role = role;
      return { access_token: fakeJwt(U[role].id), refresh_token: "rt." + role, token_type: "bearer" };
    }],
    ["POST", /^\/api\/v1\/login\/refresh-token$/, () => ({ access_token: fakeJwt(m.currentUser().id), refresh_token: "rt." + (m.state.role || "student") })],
    ["POST", /^\/api\/v1\/login\/logout$/, () => ({ message: "ok" })],
    ["GET", /^\/api\/v1\/users\/me$/, () => {
      if (!m.state.role) return { __status: 401, __body: { detail: "Not authenticated" } };
      return me(m.currentUser(), m.state.meExtra);
    }],
    ["GET", /^\/api\/v1\/users\/me\/preflight$/, () => ({ __status: 404, __body: { detail: "Not Found" } })],
    ["POST", /^\/api\/v1\/users\/me\/onboarding\/complete$/, () => me(m.currentUser())],
    ["POST", /^\/api\/v1\/users\/me\/user-guides\/[^/]+\/seen$/, () => undefined],
    ["GET", /^\/api\/v1\/users\/$/, () => {
      const data = [...Object.values(U), ...people(m)].map((u) => me(u));
      return { data, count: data.length };
    }],

    // Layout: sidebar, header bell, AI widget, session banners.
    ["GET", /^\/api\/v1\/ip-management\/status$/, () => ({ configured: true })],
    ["GET", /^\/api\/v1\/resources\/my\/session-status$/, () => []],
    ["GET", /^\/api\/v1\/classroom\/live$/, () => ({ session: null, taken_over_vmids: [] })],
    ["GET", /^\/api\/v1\/jobs\/$/, () => ({ items: [], total: 0 })],
    ["GET", /^\/api\/v1\/courses\/reminders$/, () => []],
    ["GET", /^\/api\/v1\/ai\/contextual-help\/surfaces$/, () => []],
    ["GET", /^\/api\/v1\/push\/vapid-public-key$/, () => ({ enabled: false })],
    ["GET", /^\/api\/v1\/notifications.*$/, () => ({ data: [], count: 0 })],
  ]);
}
