// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import PasswordRules from "./PasswordRules";
import { WINDOWS_PASSWORD_RULES } from "../../utils/windowsPassword";
import translations from "../../locales/zh-TW/common.json";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key, params = {}) =>
      (translations[key] ?? key).replace(/\{\{(\w+)\}\}/g, (_, name) => String(params[name])),
    i18n: { language: "zh-TW" },
  }),
}));

let host, root;
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const render = (password) =>
  act(() => root.render(<PasswordRules password={password} rules={WINDOWS_PASSWORD_RULES} />));
const items = () =>
  [...host.querySelectorAll('[role="listitem"] > span:last-child')].map((el) => el.textContent);

test("hides the character-type hint until something is typed", () => {
  render("");
  expect(items()).toEqual(["至少 8 個字元", "不可包含帳號名稱 Admin"]);
});

test("names only the missing types while typing", () => {
  render("password1");
  expect(items()).toContain("再加入大寫英文字母或符號其中一種");
  render("password");
  expect(items()).toContain("再加入大寫英文字母、數字或符號其中兩種");
  render("Passw0rd");
  expect(items()).toContain("字元種類足夠");
});
