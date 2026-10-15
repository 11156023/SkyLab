// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
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
let navigate;

function Location() {
  navigate = useNavigate();
  return <output>{useLocation().pathname}</output>;
}

async function renderAt(path, collapsed = false) {
  await act(async () => {
    root.render(<MemoryRouter key={`${path}-${collapsed}`} initialEntries={[path]}>
      <Sidebar collapsed={collapsed} mobileOpen={false} /><Location />
    </MemoryRouter>);
  });
}

const button = (label) => host.querySelector(`button[aria-label="Sidebar.${label}"]`);

beforeEach(async () => {
  vi.clearAllMocks();
  confirmLeave.mockResolvedValue(true);
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

test("admin landing shows the settings home and toggles between both workspaces", async () => {
  auth.user.role = "admin";
  await renderAt("/dashboard");
  expect(button("itemAdmin")).not.toBeNull();
  expect(button("groupResource")).toBeNull();
  expect(button("topDashboard").className).toContain("active");
  expect(button("backToConsole")).toBeNull();

  await act(async () => button("generalPages").click());
  expect(host.querySelector("output").textContent).toBe("/home");
  expect(button("itemAdmin")).toBeNull();
  expect(button("groupResource")).not.toBeNull();
  expect(button("topDashboard").className).toContain("active");

  await act(async () => button("adminSettings").click());
  expect(host.querySelector("output").textContent).toBe("/dashboard");
  expect(button("itemAdmin")).not.toBeNull();

  await act(async () => navigate(-1));
  expect(button("adminSettings")).not.toBeNull();
  await act(async () => navigate(1));
  expect(button("generalPages")).not.toBeNull();
});

test.each(["/admin", "/pve-connections", "/gpu-mgmt"])("collapsed settings at %s can switch back to general pages", async (path) => {
  auth.user.is_superuser = true;
  await renderAt(path, true);
  expect(button("generalPages").title).toBe("Sidebar.generalPages");
  await act(async () => button("generalPages").click());
  expect(host.querySelector("output").textContent).toBe("/home");
  expect(button("adminSettings").title).toBe("Sidebar.adminSettings");
});

test("declining unsaved changes keeps the admin workspace and route", async () => {
  auth.user.role = "admin";
  await renderAt("/admin");
  confirmLeave.mockResolvedValue(false);
  await act(async () => button("generalPages").click());
  expect(host.querySelector("output").textContent).toBe("/admin");
  expect(button("generalPages")).not.toBeNull();
});

test.each(["student", "teacher"])("%s keeps the existing home and has no admin switch", async (role) => {
  auth.user.role = role;
  await renderAt("/dashboard");
  expect(button("groupResource")).not.toBeNull();
  expect(button("adminSettings")).toBeNull();
  expect(button("generalPages")).toBeNull();
  await act(async () => button("topDashboard").click());
  expect(host.querySelector("output").textContent).toBe("/dashboard");
});
