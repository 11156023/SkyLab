// Manual page: manual/ldap.md
import { ago } from "../lib/mock.mjs";
import { endFrame, newFrame, stableTokens } from "../mocks/infra.mjs";

// A fresh install: nothing configured yet (the model's defaults).
const EMPTY = {
  enabled: false, server_uri: "", use_starttls: false, bind_dn: "", bind_password_set: false,
  user_search_base: "", user_filter_template: "(uid={username})", email_attribute: "mail", name_attribute: "displayName",
  teacher_group_dn: null, admin_group_dn: null, auto_create_users: true, connect_timeout_seconds: 5, updated_at: ago(30 * 86400000),
};
// Everything filled in and tested, not yet enabled.
const SAVED = {
  ...EMPTY, server_uri: "ldap://dc01.ntub.edu.tw:389", use_starttls: true, bind_dn: "CN=svc-skylab,OU=Service,DC=ntub,DC=edu,DC=tw",
  bind_password_set: true, user_search_base: "OU=Users,DC=ntub,DC=edu,DC=tw", user_filter_template: "(sAMAccountName={username})",
  teacher_group_dn: "CN=Teachers,OU=Groups,DC=ntub,DC=edu,DC=tw", admin_group_dn: "CN=SkyLabAdmins,OU=Groups,DC=ntub,DC=edu,DC=tw",
  connect_timeout_seconds: 5, updated_at: ago(2 * 3600000),
};

function routes(m, initial) {
  stableTokens(m);
  m.state.ldap = { ...initial };
  m.use([
    ["GET", /^\/api\/v1\/admin\/ldap-config$/, () => m.state.ldap],
    ["PUT", /^\/api\/v1\/admin\/ldap-config$/, ({ body }) => {
      const { bind_password, ...rest } = body;
      m.state.ldap = { ...m.state.ldap, ...rest, bind_password_set: m.state.ldap.bind_password_set || Boolean(bind_password), updated_at: ago(0) };
      return m.state.ldap;
    }],
    // The backend's success text is not translated (services/user/ldap_auth_service.py).
    ["POST", /^\/api\/v1\/admin\/ldap-config\/test$/, () => ({ __delay: 300, __body: { ok: true, message: m.L("LDAP service bind succeeded", "LDAP service bind 成功") } })],
  ]);
}

const field = (s, label) => s.page.locator("label", { hasText: label }).locator("input").first();
const check = (s, label) => s.page.locator("label", { hasText: label }).first();
// The whole form, save bar included, fits in a 1240 px tall window.
async function openTall(s) {
  await s.page.setViewportSize({ width: 1440, height: 1240 });
  await s.go("/ldap");
}
const area = { x: 336, y: 0, width: 1104, height: 1030 };
async function fillHome(s, loc, value) {
  await s.fill(loc, value);
  await loc.press("Home");
}
const card = (s, guide) => s.page.locator(`[data-guide="${guide}"]`);

export default {
  page: "ldap",
  role: "admin",
  mocks(m) { routes(m, SAVED); },
  sections: [
    // s01 Set the server address
    {
      mocks(m) { routes(m, EMPTY); },
      run: async (s) => {
        await s.go("/ldap");
        await s.fill(field(s, s.t("Server URI", "伺服器 URI")), "ldap://dc01.ntub.edu.tw:389");
        await s.fill(field(s, s.t("Connect Timeout", "連線逾時")), "10");
        await s.click(check(s, s.t("Use StartTLS", "使用 StartTLS")).locator("input"));
        await s.shot(1, field(s, s.t("Server URI", "伺服器 URI")), { crop: card(s, "ldap-login") });
        await s.shot(2, field(s, s.t("Connect Timeout", "連線逾時")), { crop: card(s, "ldap-login") });
        await s.shot(3, check(s, s.t("Use StartTLS", "使用 StartTLS")), { crop: card(s, "ldap-login") });
      },
    },
    // s02 Set the service account and user search
    {
      mocks(m) { routes(m, EMPTY); },
      run: async (s) => {
        await s.go("/ldap");
        const c = card(s, "ldap-service-account");
        await c.scrollIntoViewIfNeeded();
        await fillHome(s, field(s, s.t("Bind DN", "服務帳號 DN")), "CN=svc-skylab,OU=Service,DC=ntub,DC=edu,DC=tw");
        await s.fill(field(s, s.t("Bind Password", "服務帳號密碼")), "Sv!c-Skylab-2026");
        await fillHome(s, field(s, s.t("User Search Base DN", "使用者搜尋 Base DN")), "OU=Users,DC=ntub,DC=edu,DC=tw");
        await s.fill(field(s, s.t("User Filter Template", "使用者過濾範本")), "(sAMAccountName={username})");
        const opts = { crop: c, scroll: false };
        await s.shot(1, field(s, s.t("Bind DN", "服務帳號 DN")), opts);
        await s.shot(2, field(s, s.t("Bind Password", "服務帳號密碼")), opts);
        await s.shot(3, field(s, s.t("User Search Base DN", "使用者搜尋 Base DN")), opts);
        await s.shot(4, field(s, s.t("User Filter Template", "使用者過濾範本")), opts);
        await s.shot(5, [field(s, s.t("Email Attribute", "Email 屬性")), field(s, s.t("Name Attribute", "姓名屬性"))], opts);
      },
    },
    // s03 Set account creation and role mapping
    {
      mocks(m) { routes(m, EMPTY); },
      run: async (s) => {
        await s.go("/ldap");
        const c = card(s, "ldap-role-mapping");
        await c.scrollIntoViewIfNeeded();
        await fillHome(s, field(s, s.t("Teacher Group DN", "教師群組 DN")), "CN=Teachers,OU=Groups,DC=ntub,DC=edu,DC=tw");
        await fillHome(s, field(s, s.t("Administrator Group DN", "管理員群組 DN")), "CN=SkyLabAdmins,OU=Groups,DC=ntub,DC=edu,DC=tw");
        const opts = { crop: c, scroll: false };
        await s.shot(1, check(s, s.t("Auto-create Accounts", "自動建立帳號")).locator("input"), opts);
        await s.shot(2, [field(s, s.t("Teacher Group DN", "教師群組 DN")), field(s, s.t("Administrator Group DN", "管理員群組 DN"))], opts);
      },
    },
    // s04 Test the settings
    async (s) => {
      await openTall(s);
      const btn = s.page.getByRole("button", { name: s.t("Test Connection", "測試連線") });
      await s.click(btn, { wait: 700 });
      const toast = s.page.locator("[data-sonner-toast]").first();
      await toast.waitFor();
      await s.shot(1, btn, { crop: area, scroll: false });
      await s.shot(2, toast, { crop: area, scroll: false });
    },
    // s05 Enable LDAP sign-in
    async (s) => {
      await openTall(s);
      const en = check(s, s.t("Enable LDAP Login", "啟用 LDAP 登入"));
      await s.click(en.locator("input"));
      await s.shot(1, en.locator("input"), { crop: area, scroll: false });
      await s.shot(2, s.page.getByRole("button", { name: s.t("Save LDAP Settings", "儲存 LDAP 設定") }), { crop: area, scroll: false });
    },
    // s06 Discard unsaved changes
    async (s) => {
      await openTall(s);
      const uri = field(s, s.t("Server URI", "伺服器 URI"));
      await s.fill(uri, "ldaps://dc02.ntub.edu.tw:636");
      const restore = s.page.getByRole("button", { name: s.t("Restore", "還原"), exact: true });
      await s.shot(1, [restore, uri], { crop: area, scroll: false });
      await s.click(restore, { wait: 400 });
      await newFrame(s, 420); // the field changing back is too small a change on its own
      await s.shot(2, uri, { crop: card(s, "ldap-login"), scroll: false });
      await endFrame(s);
    },
  ],
};
