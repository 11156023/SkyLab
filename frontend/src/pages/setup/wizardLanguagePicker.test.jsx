// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const { setLanguage, i18nState } = vi.hoisted(() => ({
  setLanguage: vi.fn(),
  i18nState: { language: "en" },
}));

// 保留真正的 currentLanguage（語言退回規則的唯一來源），只替換會改全域狀態的 setLanguage
vi.mock("../../i18n", async (importOriginal) => ({
  ...(await importOriginal()),
  setLanguage,
}));

vi.mock("react-i18next", async (importOriginal) => ({
  ...(await importOriginal()),
  useTranslation: () => ({ t: (key) => key, i18n: i18nState }),
}));

import { DEFAULT_LANGUAGE } from "../../i18n";
import { LanguagePicker } from "./wizardParts";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let host;
let root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  setLanguage.mockClear();
});

afterEach(async () => {
  await act(async () => root.unmount());
  document.body.innerHTML = "";
});

function checkedLang() {
  return host.querySelector('button[aria-pressed="true"]').getAttribute("lang");
}

test("精靈語言選擇沿用 i18n 的 currentLanguage：支援的語言原樣勾選", async () => {
  i18nState.language = "ja";
  await act(async () => root.render(<LanguagePicker ariaLabel="lang" />));
  expect(checkedLang()).toBe("ja");
});

test("精靈語言選擇遇到不支援或空白的語言時勾選預設語言", async () => {
  for (const lang of ["fr", "zh", ""]) {
    i18nState.language = lang;
    await act(async () => root.render(<LanguagePicker ariaLabel="lang" key={lang || "empty"} />));
    expect(checkedLang()).toBe(DEFAULT_LANGUAGE);
  }
});
