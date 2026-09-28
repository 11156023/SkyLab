// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import Sidebar from "./Sidebar";

const logout = vi.fn();
const confirmLeave = vi.fn();

vi.mock("../../contexts/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u1", email: "a@example.com", full_name: "A", role: "student" }, logout }),
}));
vi.mock("../../contexts/UnsavedChangesContext", () => ({ useUnsavedChanges: () => ({ confirmLeave }) }));
vi.mock("../../i18n", () => ({
  SUPPORTED_LANGUAGES: ["zh-TW", "en", "ja"],
  currentLanguage: (lang) => (["zh-TW", "en", "ja"].includes(lang) ? lang : "zh-TW"),
  setLanguage: vi.fn(),
}));
vi.mock("../Jobs/JobsButton", () => ({ default: () => null }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...(await importOriginal()),
  useTranslation: () => ({ t: (key) => key, i18n: { language: "zh-TW" } }),
}));

let host;
let root;

beforeEach(async () => {
  vi.clearAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => {
    root.render(<MemoryRouter><Sidebar collapsed={false} mobileOpen={false} /></MemoryRouter>);
  });
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

async function clickLogout() {
  const userBtn = document.querySelector('button[aria-label="Sidebar.userMenuAriaLabel"]');
  await act(async () => { userBtn.click(); });
  const logoutBtn = [...document.querySelectorAll("button")].find((b) => b.textContent.includes("Sidebar.logOut"));
  await act(async () => { logoutBtn.click(); });
}

test("logout waits for the unsaved-changes confirmation and stays when declined", async () => {
  confirmLeave.mockResolvedValue(false);
  await clickLogout();
  expect(confirmLeave).toHaveBeenCalledTimes(1);
  expect(logout).not.toHaveBeenCalled();
});

test("logout proceeds once leaving is confirmed", async () => {
  confirmLeave.mockResolvedValue(true);
  await clickLogout();
  expect(logout).toHaveBeenCalledTimes(1);
});
