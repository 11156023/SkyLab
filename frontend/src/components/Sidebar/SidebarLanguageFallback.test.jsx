// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const { i18nState } = vi.hoisted(() => ({ i18nState: { language: "zh-TW" } }));

vi.mock("../../contexts/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u1", email: "a@example.com", full_name: "A", role: "student" }, logout: vi.fn() }),
}));
vi.mock("../../contexts/UnsavedChangesContext", () => ({ useUnsavedChanges: () => ({ confirmLeave: vi.fn() }) }));
// 保留真正的 currentLanguage（語言退回規則的唯一來源），只替換會改全域狀態的 setLanguage
vi.mock("../../i18n", async (importOriginal) => ({
  ...(await importOriginal()),
  setLanguage: vi.fn(),
}));
vi.mock("../Jobs/JobsButton", () => ({ default: () => null }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...(await importOriginal()),
  useTranslation: () => ({ t: (key) => key, i18n: i18nState }),
}));

import Sidebar from "./Sidebar";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let host;
let root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

async function renderWith(language) {
  i18nState.language = language;
  await act(async () => {
    root.render(<MemoryRouter><Sidebar collapsed={false} mobileOpen={false} /></MemoryRouter>);
  });
  const langBtn = host.querySelector('button[aria-label="語言 / Language"]');
  return langBtn.textContent;
}

test("側欄語言按鈕顯示目前支援的語言", async () => {
  expect(await renderWith("ja")).toContain("日本語");
});

test("側欄遇到不支援的語言時退回預設的繁體中文", async () => {
  expect(await renderWith("fr")).toContain("繁體中文");
});
