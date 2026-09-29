// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const { setLanguage, i18nState } = vi.hoisted(() => ({
  setLanguage: vi.fn(),
  i18nState: { language: "en" },
}));

vi.mock("../../i18n", () => ({
  DEFAULT_LANGUAGE: "zh-TW",
  SUPPORTED_LANGUAGES: ["zh-TW", "en", "ja"],
  currentLanguage: (lang) => (["zh-TW", "en", "ja"].includes(lang) ? lang : "zh-TW"),
  setLanguage,
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key) => key, i18n: i18nState }),
}));

import { LANG_OPTIONS, LanguagePicker, Notice } from "./wizardParts";
import PageShell from "../login/PageShell";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let host;
let root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  setLanguage.mockClear();
  i18nState.language = "en";
});

afterEach(async () => {
  await act(async () => root.unmount());
  document.body.innerHTML = "";
});

test("Notice 渲染內容", async () => {
  await act(async () => root.render(<Notice tone="success">hello</Notice>));
  expect(host.textContent).toContain("hello");
});

test("LanguagePicker 標出目前語言，點選即切換", async () => {
  await act(async () => root.render(<LanguagePicker ariaLabel="lang" />));
  const group = host.querySelector('[role="group"]');
  expect(group.getAttribute("aria-label")).toBe("lang");
  const buttons = [...group.querySelectorAll("button")];
  expect(buttons.map((b) => b.getAttribute("lang"))).toEqual(LANG_OPTIONS.map((o) => o.key));
  expect(buttons.find((b) => b.getAttribute("aria-pressed") === "true").getAttribute("lang")).toBe("en");

  await act(async () => buttons[2].click());
  expect(setLanguage).toHaveBeenCalledWith("ja");
});

test("LanguagePicker 遇到不支援的語言時標出預設語言", async () => {
  i18nState.language = "fr";
  await act(async () => root.render(<LanguagePicker ariaLabel="lang" />));
  const checked = host.querySelector('[aria-pressed="true"]');
  expect(checked.getAttribute("lang")).toBe("zh-TW");
});

test("登入頁 PageShell 把 cardClassName 加到卡片上，背景不疊光暈", async () => {
  await act(async () => root.render(<PageShell cardClassName="wide-extra">body</PageShell>));
  const card = [...host.querySelectorAll("div")].find((d) => d.textContent === "body" && d.children.length === 0);
  expect(card.className).toContain("wide-extra");
  expect(host.querySelector('[aria-hidden="true"]')).toBeNull();
});
