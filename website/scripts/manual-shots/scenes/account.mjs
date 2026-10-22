// Manual page: manual/account.md
import { NOW } from "../lib/mock.mjs";
import { me } from "../mocks/common.mjs";
import { avatarPng, landscapePng, shotNew, totpSetup } from "../mocks/auth.mjs";

const tab = (s, en, zh) => s.page.locator('[data-guide="account-tabs"]').getByRole("button", { name: s.t(en, zh) });
const profileCard = (s) => s.page.locator('[data-guide="account-profile-card"]');
const dialog = (s) => s.page.locator('[role="dialog"],[role="alertdialog"]').last();
const appearanceCard = (s) => s.page.locator('[data-guide="account-appearance-form"]');
const group = (s, en, zh) => s.page.getByRole("group", { name: s.t(en, zh), exact: true });

const patternsOf = (s) => group(s, "Background pattern", "背景花色");

async function openAppearance(s) {
  await s.go("/account", { wait: 600 });
  await s.click(tab(s, "Appearance", "外觀"), { wait: 500 });
}

export default {
  page: "account",
  role: "student",
  mocks(m) {
    m.use([
      ["PATCH", /^\/api\/v1\/users\/me$/, ({ body }) => me(m.currentUser(), { ...m.state.meExtra, ...body })],
      ["POST", /^\/api\/v1\/users\/me\/email-change$/, () => ({ message: "Verification email sent" })],
      // Like the backend: the file is stored and avatar_url points at the avatar endpoint.
      ["POST", /^\/api\/v1\/users\/me\/avatar$/, () => {
        m.state.meExtra = { ...m.state.meExtra, avatar_url: `/api/v1/users/${m.currentUser().id}/avatar?v=${Math.floor(NOW / 1000)}` };
        return me(m.currentUser(), m.state.meExtra);
      }],
      ["GET", /^\/api\/v1\/users\/[^/]+\/avatar$/, () => (m.state.avatarPng
        ? { __text: m.state.avatarPng, __contentType: "image/png" }
        : { __status: 404, __body: { detail: "Avatar not found" } })],
      ["PATCH", /^\/api\/v1\/users\/me\/password$/, () => ({ message: "Password updated successfully" })],
      ["POST", /^\/api\/v1\/users\/me\/totp\/setup$/, () => totpSetup(m.currentUser().email)],
      ["POST", /^\/api\/v1\/users\/me\/totp\/confirm$/, () => ({ message: "ok" })],
      ["POST", /^\/api\/v1\/users\/me\/totp\/disable$/, () => ({ message: "ok" })],
      ["DELETE", /^\/api\/v1\/users\/me$/, () => ({ message: "User deleted successfully" })],
    ]);
  },
  sections: [
    // s01 Change your avatar
    async (s) => {
      await s.go("/account", { wait: 600 });
      const drop = profileCard(s).locator("label:has(input[type=file])").first();
      await s.shot(1, drop, { crop: profileCard(s), pad: 20 });
      const { buffer } = await avatarPng(s);
      s.m.state.avatarPng = buffer;
      await drop.locator("input[type=file]").setInputFiles({ name: "avatar.png", mimeType: "image/png", buffer });
      const toast = s.page.locator("[data-sonner-toast]").first();
      await toast.waitFor();
      await s.page.waitForTimeout(500);
      const avatar = profileCard(s).locator('[class*="_avatarRow_"] > :first-child').first();
      await shotNew(s, 2, [avatar, toast], { crop: { x: 330, y: 0, width: 1110, height: 560 } });
    },
    // s02 Edit your name, email or avatar URL
    async (s) => {
      await s.go("/account", { wait: 600 });
      const edit = profileCard(s).getByRole("button", { name: s.t("Edit", "編輯") });
      await s.shot(1, edit, { crop: profileCard(s), pad: 20 });
      await s.click(edit, { wait: 300 });
      const inputs = profileCard(s).locator("form input:not([type=file])");
      await s.fill(inputs.nth(0), s.m.L("Alex Wang", "王小明"));
      await s.fill(inputs.nth(1), "alex.wang@ntub.edu.tw");
      await s.fill(inputs.nth(2), "https://avatars.ntub.edu.tw/11156001.png");
      await shotNew(s, 2, [inputs.nth(0), inputs.nth(1), inputs.nth(2)], { crop: profileCard(s), pad: 20 });
      await s.shot(3, [
        profileCard(s).getByRole("button", { name: s.t("Save", "儲存") }),
        profileCard(s).getByRole("button", { name: s.t("Cancel", "取消") }),
      ], { crop: profileCard(s), pad: 20 });
    },
    // s03 Change your password
    async (s) => {
      await s.go("/account", { wait: 600 });
      const card = s.page.locator('[data-guide="account-password"]');
      const pw = card.locator("input");
      await s.fill(pw.nth(0), "Skylab@2026");
      await s.fill(pw.nth(1), "Cloud#Lab2026");
      await s.fill(pw.nth(2), "Cloud#Lab2062");
      await s.shot(1, pw.nth(0), { crop: card, pad: 20 });
      await s.shot(2, [pw.nth(1), card.locator('[class*="_rules_"]').first()], { crop: card, pad: 20 });
      await s.shot(3, [pw.nth(2), card.locator('em[class*="_fieldError_"]').first()], { crop: card, pad: 20 });
      await s.shot(4, card.getByRole("button", { name: s.t("Update Password", "更新密碼") }), { crop: card, pad: 20 });
    },
    // s04 Turn on two-factor authentication
    async (s) => {
      await s.go("/account", { wait: 600 });
      const card = s.page.locator('[data-guide="account-two-factor"]');
      const link = card.getByRole("button", { name: s.t("Link authenticator app", "綁定驗證 App") });
      await s.shot(1, link, { crop: card, pad: 24 });
      await s.click(link, { wait: 800 });
      const code = dialog(s).locator('input[autocomplete="one-time-code"]');
      await s.fill(code, "731 064");
      await shotNew(s, 2, [
        dialog(s).locator('ol[class*="_steps_"]').first(),
        dialog(s).locator('img[class*="_qrImg_"]').first(),
        dialog(s).locator('[class*="_secretRow_"]').first(),
        code,
      ]);
      await s.shot(3, dialog(s).getByRole("button", { name: s.t("Enable two-factor authentication", "啟用兩步驟驗證") }));
    },
    // s05 Turn off two-factor authentication
    {
      mocks(m) { m.state.meExtra = { totp_enabled: true }; },
      run: async (s) => {
        await s.go("/account", { wait: 600 });
        const card = s.page.locator('[data-guide="account-two-factor"]');
        const btn = card.getByRole("button", { name: s.t("Disable two-factor authentication", "停用兩步驟驗證") });
        await s.shot(1, btn, { crop: card, pad: 24 });
        await s.click(btn, { wait: 600 });
        const code = dialog(s).locator("input");
        await s.fill(code, "529 418");
        await shotNew(s, 2, code);
        await s.shot(3, dialog(s).getByRole("button", { name: s.t("Disable", "確認停用"), exact: true }));
      },
    },
    // s06 Delete your account
    async (s) => {
      await s.go("/account", { wait: 600 });
      const card = s.page.locator('[data-guide="account-delete"]');
      const btn = card.getByRole("button", { name: s.t("Delete Account", "刪除帳號") });
      await s.shot(1, btn, { crop: card, pad: 16 });
      await s.click(btn, { wait: 600 });
      const input = dialog(s).locator("input");
      await s.fill(input, s.t("DELETE", "刪除"));
      await shotNew(s, 2, input);
      await s.shot(3, dialog(s).getByRole("button", { name: s.t("Confirm Delete", "確定刪除") }));
    },
    // s07 Change colors and background
    async (s) => {
      await openAppearance(s);
      const card = appearanceCard(s);
      await s.click(group(s, "Background color", "背景色").getByRole("button", { name: s.t("Custom", "自訂") }), { wait: 300 });
      const { buffer } = await landscapePng(s);
      await card.locator("input[type=file]").setInputFiles({ name: "campus-sunset.png", mimeType: "image/png", buffer });
      await s.page.getByRole("button", { name: s.t("Remove Background Image", "移除背景圖") }).waitFor();
      await s.page.waitForTimeout(4000); // toast fades
      const primary = card.locator('[class*="_field_"]').filter({ has: s.page.getByText(s.t("Primary Color", "主色"), { exact: true }) }).first();
      await s.shot(1, primary, { crop: card, pad: 20 });
      await s.shot(2, card.locator('[class*="_bgColorControls_"]').first(), { crop: card, pad: 20 });
      const patterns = group(s, "Background pattern", "背景花色");
      await s.shot(3, [0, 1, 2].map((i) => patterns.getByRole("button").nth(i)), { crop: card, pad: 20 });
      await s.shot(4, [
        patterns.locator("label").first(),
        patterns.getByRole("button", { name: s.t("Custom Image", "自訂圖片") }),
        s.page.getByRole("button", { name: s.t("Remove Background Image", "移除背景圖") }),
      ], { crop: card, pad: 20 });
    },
    // s08 Change the interface style and color mode
    async (s) => {
      await openAppearance(s);
      const card = appearanceCard(s);
      await s.shot(1, group(s, "Style", "風格"), { crop: card, pad: 20 });
      await s.shot(2, group(s, "Color Mode", "明暗模式"), { crop: card, pad: 20 });
    },
    // s09 Apply a color theme
    async (s) => {
      await openAppearance(s);
      const presets = s.page.locator('[data-guide="account-theme-presets"]');
      const theme = presets.locator('button[class*="_presetBtn_"]').nth(3);
      await s.click(theme, { wait: 500 });
      await s.page.evaluate(() => window.scrollTo(0, 0));
      await s.page.waitForTimeout(200);
      // Badge 1 on the theme that was clicked; the outlines show what it changed.
      await s.shot(1, [theme, appearanceCard(s).locator('[class*="_colorRow_"]').first(), patternsOf(s)], { crop: "viewport", scroll: false });
      await s.shot(2, theme, { crop: "viewport", scroll: false });
    },
    // s10 Reset the appearance
    async (s) => {
      await openAppearance(s);
      const reset = s.page.getByRole("button", { name: s.t("Reset to Defaults", "重設為系統預設值") });
      await s.shot(1, reset, { crop: { x: 330, y: 0, width: 1110, height: 400 } });
      await s.click(reset, { wait: 500 });
      await shotNew(s, 2, dialog(s).getByRole("button", { name: s.t("Reset", "重設"), exact: true }));
    },
    // s11 Change the interface language
    async (s) => {
      await s.go("/account", { wait: 600 });
      const lang = s.page.getByRole("button", { name: "語言 / Language" });
      await s.click(lang, { wait: 500 });
      const menu = s.page.locator('[class*="_appearancePopup_"]').first();
      await s.shot(0, [lang, menu], { crop: { x: 0, y: 380, width: 350, height: 520 } });
    },
    // s12 Check the version and licenses
    async (s) => {
      await s.go("/account", { wait: 600 });
      await s.click(tab(s, "About", "關於"), { wait: 600 });
      const about = s.page.locator('[data-guide="account-about"]');
      await s.shot(0, [about.locator('[class*="_card_"]').first(), about.locator('[class*="_aboutMain_"]').first()], { crop: "viewport" });
    },
  ],
};
