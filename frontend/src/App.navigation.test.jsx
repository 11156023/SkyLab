// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "./App";

const auth = vi.hoisted(() => ({ user: null }));
vi.mock("./contexts/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("./hooks/useBodyScrollLock", () => ({ useModalScrollLock: vi.fn() }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: (key) => key }),
}));
vi.mock("./layout/DashboardLayout", async () => {
  const { Outlet } = await import("react-router-dom");
  return { default: () => <Outlet /> };
});
vi.mock("./pages/login/LoginPage", () => ({ default: () => <div>Login</div> }));
vi.mock("./pages/login/LoginPreflightPage", () => ({ default: () => <div>Preflight</div> }));
vi.mock("./pages/personal/dashboard/admin/AdminDashboardPage", () => ({ default: () => <div>Admin home</div> }));
vi.mock("./pages/personal/dashboard/teacher/TeacherDashboardPage", () => ({ default: () => <div>Teacher home</div> }));
vi.mock("./pages/personal/dashboard/StudentHomePage", () => ({ default: () => <div>Personal home</div> }));
vi.mock("./pages/personal/courses/StudentCoursesPage", () => ({ default: () => <div>Personal courses</div> }));
vi.mock("./pages/personal/dashboard/student/StudentCoursePage", () => ({ default: () => <div>Personal course</div> }));
vi.mock("./pages/personal/courses/StudentWeekPage", () => ({ default: () => <div>Personal week</div> }));
vi.mock("./pages/not-found/NotFoundPage", () => ({ default: () => <div>Not found</div> }));

let host, root;
function Location() { return <output>{useLocation().pathname}</output>; }
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  auth.user = { id: "admin-1", role: "admin" };
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

async function renderAt(path) {
  await act(async () => root.render(<MemoryRouter initialEntries={[path]}><App /><Location /></MemoryRouter>));
}

test.each(["/", "/login", "/dashboard"])("admin enters the management home from %s", async (path) => {
  await renderAt(path);
  expect(host.textContent).toContain("Admin home");
  expect(host.querySelector("output").textContent).toBe("/dashboard");
});

test.each([
  ["/home", "Personal home"],
  ["/courses", "Personal courses"],
  ["/courses/course-1", "Personal course"],
  ["/courses/course-1/weeks/week-1", "Personal week"],
])("admin can open the general workspace at %s", async (path, content) => {
  await renderAt(path);
  expect(host.textContent).toContain(content);
  expect(host.querySelector("output").textContent).toBe(path);
});

test("superusers use the same admin entry even with a different role", async () => {
  auth.user = { id: "superuser-1", role: "student", is_superuser: true };
  await renderAt("/");
  expect(host.textContent).toContain("Admin home");
});

test.each([["student", "Personal home"], ["teacher", "Teacher home"]])("%s retains the existing default home", async (role, content) => {
  auth.user.role = role;
  await renderAt("/home");
  expect(host.textContent).toContain(content);
  expect(host.querySelector("output").textContent).toBe("/dashboard");
});

test("students still cannot open admin-only pages", async () => {
  auth.user.role = "student";
  await renderAt("/admin");
  expect(host.textContent).toContain("Not found");
});
