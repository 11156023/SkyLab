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

import { LANG_OPTIONS, LanguagePicker, Notice, Stepper } from "./wizardParts";
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

test("Stepper 標出目前步驟，已完成的步驟不顯示序號", async () => {
  await act(async () => root.render(<Stepper current={1} steps={["A", "B", "C"]} />));
  const items = [...host.querySelectorAll("li")];
  expect(items).toHaveLength(3);
  expect(items[1].getAttribute("aria-current")).toBe("step");
  expect(items[0].getAttribute("aria-current")).toBeNull();
  expect(items[0].textContent).not.toContain("1");
  expect(items[2].textContent).toContain("3");
});

test("Notice 渲染內容", async () => {
  await act(async () => root.render(<Notice tone="success">hello</Notice>));
  expect(host.textContent).toContain("hello");
});

test("LanguagePicker 勾選目前語言，點選即切換", async () => {
  await act(async () => root.render(<LanguagePicker ariaLabel="lang" />));
  const group = host.querySelector('[role="radiogroup"]');
  expect(group.getAttribute("aria-label")).toBe("lang");
  const radios = [...host.querySelectorAll('[role="radio"]')];
  expect(radios.map((r) => r.getAttribute("lang"))).toEqual(LANG_OPTIONS.map((o) => o.key));
  expect(radios.find((r) => r.getAttribute("aria-checked") === "true").getAttribute("lang")).toBe("en");

  await act(async () => radios[2].click());
  expect(setLanguage).toHaveBeenCalledWith("ja");
});

test("LanguagePicker 遇到不支援的語言時勾選預設語言", async () => {
  i18nState.language = "fr";
  await act(async () => root.render(<LanguagePicker ariaLabel="lang" />));
  const checked = host.querySelector('[aria-checked="true"]');
  expect(checked.getAttribute("lang")).toBe("zh-TW");
});

test("登入頁 PageShell 把 cardClassName 加到卡片上", async () => {
  await act(async () => root.render(<PageShell cardClassName="wide-extra">body</PageShell>));
  const card = [...host.querySelectorAll("div")].find((d) => d.textContent === "body" && d.children.length === 0);
  expect(card.className).toContain("wide-extra");
  expect(host.querySelector('[aria-hidden="true"]').children).toHaveLength(3);
});
