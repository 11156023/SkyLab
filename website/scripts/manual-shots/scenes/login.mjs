// Manual page: manual/login.md
import { fakeJwt } from "../mocks/common.mjs";
import { avatarPng, fakeExternals, shotNew, totpSetup } from "../mocks/auth.mjs";

const methods = (extra = {}) => ({ password: true, google: true, ldap: false, signup_enabled: false, turnstile_site_key: "", ...extra });
const TURNSTILE_KEY = "0x4AAAAAAAskylab-ntub";
const tokens = (m, role = "student") => ({ access_token: fakeJwt(m.users[role].id), refresh_token: "rt." + role, token_type: "bearer" });
const totpChallenge = { totp_required: true, totp_token: "totp-challenge.mock", access_token: null, refresh_token: null };

// Sign-in form helpers
const card = (s) => s.page.locator('[class*="_card_"]').first();
async function openLogin(s, route = "/login") {
  await fakeExternals(s);
  await s.go(route, { wait: 800 });
}
async function fillPassword(s, email = "11156001@ntub.edu.tw") {
  await s.fill(s.page.locator("#username"), email);
  await s.fill(s.page.locator("#password"), "Skylab@2026");
}
const loginButton = (s) => s.page.locator('form button[type="submit"]').first();
const turnstile = (s) => s.page.locator("[data-fake-turnstile]").first();
const center = (loc) => loc.evaluate((el) => el.scrollIntoView({ block: "center" }));
// A target that outlines only the text inside an element (its padded box would run
// over the heading above). s.shot only calls these methods when a crop is given.
const textOf = (loc) => ({
  scrollIntoViewIfNeeded: () => loc.scrollIntoViewIfNeeded(),
  boundingBox: () => loc.evaluate((el) => {
    const r = document.createRange();
    r.selectNodeContents(el);
    const b = r.getBoundingClientRect();
    return { x: b.x - 8, y: b.y - 4, width: b.width + 16, height: b.height + 8 };
  }),
});
// Crop: the card's full width, from the first to the last of `locs` (plus pad) vertically.
async function band(s, locs, pad = 28) {
  const c = await card(s).boundingBox();
  const bs = [];
  for (const l of locs) bs.push(await l.boundingBox());
  const y = Math.min(...bs.map((b) => b.y)) - pad;
  const bottom = Math.max(...bs.map((b) => b.y + b.height)) + pad;
  return { x: c.x - 24, y, width: c.width + 48, height: bottom - y };
}

export default {
  page: "login",
  role: null,
  mocks(m) {
    m.use([
      ["GET", /^\/api\/v1\/login\/methods$/, () => methods()],
      ["POST", /^\/api\/v1\/login\/totp$/, () => { m.state.role = "student"; return tokens(m); }],
    ]);
  },
  sections: [
    // s01 Sign in with email and password
    {
      mocks(m) {
        m.use([
          ["GET", /^\/api\/v1\/login\/methods$/, () => methods({ ldap: true, turnstile_site_key: TURNSTILE_KEY })],
          ["POST", /^\/api\/v1\/login\/access-token$/, () => totpChallenge],
        ]);
      },
      run: async (s) => {
        await openLogin(s);
        const tabs = s.page.getByRole("group", { name: s.t("Log in method", "登入方式") });
        await s.shot(1, tabs.getByRole("button", { name: "Email" }), { crop: card(s), pad: 32 });
        await s.fill(s.page.locator("#username"), "11156001@ntub.edu.tw");
        await s.shot(2, s.page.locator("#username"), { crop: card(s), pad: 32 });
        await s.fill(s.page.locator("#password"), "Skylab@2026");
        await s.shot(3, [s.page.locator("#password"), s.page.locator('[class*="_eyeBtn_"]').first()], { crop: card(s), pad: 32 });
        await s.shot(4, turnstile(s), { crop: card(s), pad: 32 });
        await s.click(s.page.locator("[data-fake-turnstile]"), { wait: 300 });
        await s.shot(5, loginButton(s), { crop: card(s), pad: 32 });
        await s.click(loginButton(s), { wait: 600 });
        await shotNew(s, 6, s.page.locator("#totp-code"), { crop: card(s), pad: 32 });
      },
    },
    // s02 Sign in with a campus account (LDAP)
    {
      mocks(m) {
        m.use([["GET", /^\/api\/v1\/login\/methods$/, () => methods({ ldap: true, turnstile_site_key: TURNSTILE_KEY })]]);
      },
      run: async (s) => {
        await openLogin(s);
        const tab = s.page.getByRole("group", { name: s.t("Log in method", "登入方式") }).getByRole("button", { name: s.t("Campus Account", "校園帳號") });
        await s.click(tab, { wait: 500 });
        await s.shot(1, tab, { crop: card(s), pad: 32 });
        await s.fill(s.page.locator("#ldap-username"), "11156001");
        await s.fill(s.page.locator("#ldap-password"), "Ntub#11156001");
        await s.shot(2, [s.page.locator("#ldap-username"), s.page.locator("#ldap-password")], { crop: card(s), pad: 32 });
        await s.shot(3, turnstile(s), { crop: card(s), pad: 32 });
        await s.click(s.page.locator("[data-fake-turnstile]"), { wait: 300 });
        await s.shot(4, loginButton(s), { crop: card(s), pad: 32 });
      },
    },
    // s03 Sign in with Google
    {
      mocks(m) {
        m.use([["POST", /^\/api\/v1\/login\/google$/, () => ({ __delay: 30000, __body: tokens(m) })]]);
      },
      run: async (s) => {
        await openLogin(s);
        const google = s.page.locator("[data-fake-gsi]");
        await google.waitFor();
        await s.shot(1, google, { crop: card(s), pad: 32 });
        await s.shot(2, google, { crop: card(s), pad: 32 });
        await google.click();
        const hint = s.page.getByText(s.t("Signing in with Google…", "Google 登入中…"));
        await hint.waitFor();
        await s.page.waitForTimeout(400);
        await shotNew(s, 3, hint, { crop: await band(s, [google, hint], 44) });
      },
    },
    // s04 Enter the two-factor code
    {
      mocks(m) {
        m.use([["POST", /^\/api\/v1\/login\/access-token$/, () => totpChallenge]]);
      },
      run: async (s) => {
        await openLogin(s);
        await fillPassword(s);
        await s.click(loginButton(s), { wait: 600 });
        const help = s.page.getByText(s.t("Open Google Authenticator", "打開 Google Authenticator"));
        await s.shot(1, help, { crop: card(s), pad: 32 });
        await s.fill(s.page.locator("#totp-code"), "482 913");
        await s.shot(2, s.page.locator("#totp-code"), { crop: card(s), pad: 32 });
        await s.shot(3, loginButton(s), { crop: card(s), pad: 32 });
        await s.shot(4, s.page.getByRole("button", { name: s.t("Back to log in", "返回登入") }), { crop: card(s), pad: 32 });
      },
    },
    // s05 Reset a forgotten password
    {
      mocks(m) {
        m.use([
          ["POST", /^\/api\/v1\/password-recovery\/.+$/, () => ({ message: "Password recovery email sent" })],
          ["POST", /^\/api\/v1\/reset-password\/$/, () => ({ message: "Password updated successfully" })],
        ]);
      },
      run: async (s) => {
        await openLogin(s);
        const forgot = s.page.getByRole("button", { name: s.t("Forgot your password?", "忘記密碼？") });
        await s.shot(1, forgot, { crop: await band(s, [s.page.locator('label[for="password"]'), loginButton(s)], 20) });
        await s.click(forgot, { wait: 400 });
        await s.fill(s.page.locator("#forgot-email"), "11156001@ntub.edu.tw");
        await shotNew(s, 2, [s.page.locator("#forgot-email"), loginButton(s)], { crop: card(s), pad: 32 });

        await s.go("/reset-password?token=rst.mock-reset-token", { wait: 800 });
        await s.fill(s.page.locator("#reset-password"), "Cloud#Lab2026");
        await s.fill(s.page.locator("#reset-confirm"), "Cloud#Lab2026");
        await shotNew(s, 3, s.page.locator("h1").first(), { crop: card(s), pad: 32 });
        await s.shot(4, [s.page.locator("#reset-password"), s.page.locator('[class*="_rules_"]').first(), s.page.locator("#reset-confirm")], { crop: card(s), pad: 32 });
        await s.shot(5, loginButton(s), { crop: card(s), pad: 32 });
        await s.click(loginButton(s), { wait: 500 });
        await shotNew(s, 5, s.page.getByRole("button", { name: s.t("Go to Log In", "前往登入") }), { crop: s.page.locator('[class*="_successBox_"]').first(), pad: 32 });
      },
    },
    // s06 Register a new account
    {
      mocks(m) {
        m.use([
          ["GET", /^\/api\/v1\/login\/methods$/, () => methods({ signup_enabled: true, turnstile_site_key: TURNSTILE_KEY })],
          ["POST", /^\/api\/v1\/users\/signup$/, () => ({ message: "verification_sent" })],
          ["POST", /^\/api\/v1\/users\/signup\/confirm$/, () => ({ message: "pending_review" })],
        ]);
      },
      run: async (s) => {
        await openLogin(s);
        const signup = s.page.getByRole("button", { name: s.t("Sign up now", "立即註冊") });
        await center(signup);
        await s.page.waitForTimeout(200);
        await s.shot(1, signup, { crop: await band(s, [s.page.locator("[data-fake-gsi]"), signup], 44) });
        await s.click(signup, { wait: 600 });
        await s.fill(s.page.locator("#full-name"), s.m.L("Emily Chang", "張雅婷"));
        await s.fill(s.page.locator("#reg-email"), "11256003@ntub.edu.tw");
        await s.click(s.page.locator("[data-fake-turnstile]"), { wait: 300 });
        await shotNew(s, 2, [s.page.locator("#full-name"), s.page.locator("#reg-email"), turnstile(s), loginButton(s)], { crop: card(s), pad: 32 });
        await s.click(loginButton(s), { wait: 500 });
        await shotNew(s, 3, s.page.locator('[class*="_successBox_"] p').first(), { crop: s.page.locator('[class*="_successBox_"]').first(), pad: 32 });

        await s.go("/verify-signup#token=signup.mock-token", { wait: 800 });
        await s.fill(s.page.locator("#signup-password"), "Cloud#Lab2026");
        await s.fill(s.page.locator("#signup-confirm"), "Cloud#Lab2026");
        await shotNew(s, 4, [s.page.locator("#signup-password"), s.page.locator('[class*="_rules_"]').first(), s.page.locator("#signup-confirm")], { crop: card(s), pad: 32 });
        const activate = s.page.getByRole("button", { name: s.t("Verify and complete registration", "驗證並完成註冊") });
        await s.shot(5, activate, { crop: card(s), pad: 32 });
        await s.click(activate, { wait: 500 });
        await shotNew(s, 6, textOf(s.page.getByRole("status").first()), { crop: card(s), pad: 32 });
      },
    },
    // s07 Confirm a new email address
    {
      role: "student",
      mocks(m) {
        m.use([["POST", /^\/api\/v1\/users\/email-change\/confirm$/, () => new Promise((resolve) => {
          m.state.releaseEmail = () => resolve({ message: "Email updated" });
        })]]);
      },
      run: async (s) => {
        await s.go("/verify-email-change?token=email.mock-token", { wait: 800 });
        s.m.state.releaseEmail?.();
        await s.settle(400);
        await s.shot(1, null, { crop: card(s), pad: 48 });
        await s.shot(2, s.page.getByRole("status"), { crop: card(s), pad: 48 });
        // The link sits right under the status line: its own frame keeps badge 3 off the text.
        await shotNew(s, 3, s.page.getByRole("link", { name: s.t("Continue", "繼續") }), { crop: card(s), pad: 48 });
      },
    },
    // s08 Authorize the SkyLab Connect desktop app
    {
      mocks(m) {
        m.use([["POST", /^\/api\/v1\/desktop-client\/auth\/approve$/, () => ({ message: "approved" })]]);
      },
      run: async (s) => {
        await openLogin(s, "/login?device_code=SKY-7Q4M-2K9D");
        await s.shot(1, [s.page.locator('[class*="_subtitle_"]').first(), s.page.locator('[class*="_deviceNotice_"]').first()], { crop: card(s), pad: 32 });
        await fillPassword(s);
        await s.click(loginButton(s), { wait: 900 });
        const approve = s.page.getByRole("button", { name: s.t("Authorize this device", "授權此裝置") });
        await approve.waitFor();
        await shotNew(s, 2, s.page.locator('[class*="_deviceAccount_"]').first(), { crop: card(s), pad: 32 });
        await s.shot(3, [approve, s.page.getByRole("button", { name: s.t("Cancel", "取消") })], { crop: card(s), pad: 32 });
        await s.click(approve, { wait: 500 });
        await shotNew(s, 4, s.page.locator("h1").first(), { crop: card(s), pad: 32 });
      },
    },
    // s09 Get through the service check after signing in
    {
      role: "student",
      mocks(m) {
        m.use([["GET", /^\/api\/v1\/users\/me\/preflight$/, () => ({
          ok: false, detailed: false,
          checks: [
            { key: "database", status: "ok" }, { key: "redis", status: "ok" }, { key: "worker", status: "ok" },
            { key: "pve", status: "fail" }, { key: "gateway", status: "ok" }, { key: "ai", status: "skipped" },
          ],
        })]]);
      },
      run: async (s) => {
        await s.page.addInitScript(() => { try { sessionStorage.setItem("skylab:login-preflight", "1"); } catch {} });
        await s.go("/dashboard", { wait: 800 });
        const retry = s.page.getByRole("button", { name: s.t("Check again", "重新檢查") });
        await retry.waitFor({ timeout: 8000 });
        await s.settle(600);
        await s.shot(0, [
          s.page.locator('li[class*="_row_fail"]').first(),
          s.page.getByRole("button", { name: s.t("Sign out", "登出") }),
          retry,
          s.page.getByRole("button", { name: s.t("Continue anyway", "仍要繼續") }),
        ], { crop: card(s), pad: 32 });
      },
    },
    // s10 Set up two-factor authentication when it is required
    {
      role: "student",
      mocks(m) {
        m.state.meExtra = { totp_required: true, totp_setup_required: true };
        m.use([
          ["POST", /^\/api\/v1\/users\/me\/totp\/setup$/, () => totpSetup(m.users.student.email)],
          ["POST", /^\/api\/v1\/users\/me\/totp\/confirm$/, () => ({ message: "ok" })],
        ]);
      },
      run: async (s) => {
        await s.page.setViewportSize({ width: 1440, height: 1180 });
        await s.go("/dashboard", { wait: 1000 });
        const steps = s.page.locator('ol[class*="_steps_"]').first();
        await steps.waitFor();
        const code = s.page.locator('input[autocomplete="one-time-code"]').first();
        await s.fill(code, "105 382");
        const box = card(s);
        await s.shot(1, steps, { crop: box, pad: 24 });
        await s.shot(2, [s.page.locator('img[class*="_qrImg_"]').first(), s.page.locator('[class*="_secretRow_"]').first()], { crop: box, pad: 24 });
        await s.shot(3, code, { crop: box, pad: 24 });
        await s.shot(4, s.page.getByRole("button", { name: s.t("Enable and continue", "啟用並進入系統") }), { crop: box, pad: 24 });
      },
    },
    // s11 Complete the first-time setup tour
    {
      role: "student",
      mocks(m) {
        m.state.meExtra = { onboarding_completed: false };
        m.use([
          ["PATCH", /^\/api\/v1\/users\/me$/, ({ body }) => ({ ...m.users.student, ...body })],
          ["POST", /^\/api\/v1\/users\/me\/avatar$/, () => ({ avatar_url: m.state.avatar })],
        ]);
      },
      run: async (s) => {
        await s.go("/dashboard", { wait: 1200 });
        const langs = s.page.getByRole("group", { name: s.t("Language", "語言") });
        const cont = s.page.getByRole("button", { name: s.t("Continue", "繼續") });
        const box = card(s);
        await s.shot(1, [langs, cont], { crop: box, pad: 24 });
        await s.click(cont, { wait: 600 });

        const { buffer, dataUrl } = await avatarPng(s);
        s.m.state.avatar = dataUrl;
        await s.page.locator('input[type="file"]').first().setInputFiles({ name: "me.png", mimeType: "image/png", buffer });
        await s.settle(800);
        await s.page.waitForTimeout(3500); // let the "Avatar updated" toast go away
        const name = s.page.getByRole("textbox", { name: s.t("Name", "姓名") });
        const next = s.page.getByRole("button", { name: s.t("Next", "下一步") });
        await shotNew(s, 2, [s.page.locator("label:has(input[type=file])").first(), name, next], { crop: box, pad: 24 });
        await s.click(next, { wait: 600 });

        const preset = s.page.locator('button[class*="_presetBtn_"]').nth(2);
        await s.click(preset, { wait: 400 });
        const style = s.page.getByRole("group", { name: s.t("Style", "風格"), exact: true });
        const mode = s.page.getByRole("group", { name: s.t("Color Mode", "明暗模式") });
        await shotNew(s, 3, [preset, style, mode, next], { crop: box, pad: 24 });
        await s.click(next, { wait: 600 });

        await shotNew(s, 4, [
          s.page.getByRole("button", { name: s.t("Set up two-factor authentication", "設定兩步驟驗證") }),
          s.page.getByRole("button", { name: s.t("Skip for now", "先略過") }),
        ], { crop: box, pad: 24 });
        await s.click(s.page.getByRole("button", { name: s.t("Skip for now", "先略過") }), { wait: 600 });
        await shotNew(s, 5, [s.page.locator("dl").first(), s.page.getByRole("button", { name: s.t("Start using SkyLab", "開始使用 SkyLab") })], { crop: box, pad: 24 });
      },
    },
  ],
};
