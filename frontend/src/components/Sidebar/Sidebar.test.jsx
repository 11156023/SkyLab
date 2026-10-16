// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import Sidebar from "./Sidebar";

const logout = vi.fn();
const confirmLeave = vi.fn();
const auth = vi.hoisted(() => ({ user: null }));

vi.mock("../../contexts/AuthContext", () => ({
  useAuth: () => ({ user: auth.user, logout }),
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

function CurrentLocation() {
  return <output>{useLocation().pathname}</output>;
}

async function renderAt(path) {
  await act(async () => {
    root.render(<MemoryRouter key={path} initialEntries={[path]}><Sidebar collapsed={false} mobileOpen={false} /><CurrentLocation /></MemoryRouter>);
  });
}

const buttons = (label) => host.querySelectorAll(`button[aria-label="Sidebar.${label}"]`);

beforeEach(async () => {
  vi.clearAllMocks();
  auth.user = { id: "u1", email: "a@example.com", full_name: "A", role: "student" };
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await renderAt("/dashboard");
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

/* 管理員只有一個首頁（/dashboard 管理首頁），管理員設定側欄裡不再另放首頁 */
test.each(["/dashboard", "/admin"])("admin sees at most one home entry at %s", async (path) => {
  auth.user = { ...auth.user, role: "admin" };
  await renderAt(path);
  expect(buttons("topDashboard").length).toBe(path === "/dashboard" ? 1 : 0);
  expect(buttons("generalPages").length).toBe(0);
});

test("管理者的課程入口可開啟學生課程頁", async () => {
  auth.user = { ...auth.user, role: "admin" };
  confirmLeave.mockResolvedValue(true);
  await renderAt("/dashboard");
  expect(buttons("topCourses")).toHaveLength(1);
  await act(async () => buttons("topCourses")[0].click());
  expect(host.querySelector("output").textContent).toBe("/courses");
});

test("學生的課程入口維持連到學生總覽", async () => {
  confirmLeave.mockResolvedValue(true);
  await renderAt("/dashboard");
  expect(buttons("topCourses")).toHaveLength(1);
  await act(async () => buttons("topCourses")[0].click());
  expect(host.querySelector("output").textContent).toBe("/courses");
});

test("老師使用班級管理入口", async () => {
  auth.user = { ...auth.user, role: "teacher" };
  await renderAt("/dashboard");
  expect(buttons("topCourses")).toHaveLength(0);
  expect(buttons("itemClassManagement")).toHaveLength(1);
});
