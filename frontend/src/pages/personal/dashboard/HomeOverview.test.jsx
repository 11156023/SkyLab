// @vitest-environment happy-dom
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import HomeOverview from "./HomeOverview";
import { recordMachineUse } from "../../../services/recentMachines";

vi.mock("../../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { id: "student" } }) }));
vi.mock("react-i18next", async (original) => ({ ...await original(),
  useTranslation: () => ({ t: (key) => key, i18n: { language: "zh-TW" } }),
}));
let host, root;
const defaults = { paths: [], resources: [], templates: [], templatesLoading: false,
  openingMachineId: null, onOpenMachine: vi.fn(), todayLabel: "9/17" };
function Location() { const location = useLocation(); return <output>{location.pathname}</output>; }
async function render(props = {}) {
  await act(async () => root.render(<MemoryRouter><HomeOverview {...defaults} {...props} /><Location /></MemoryRouter>));
}
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  vi.clearAllMocks();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

it("shows real empty states and keeps all courses reachable", async () => {
  await render();
  expect(host.textContent).toContain("HomeOverview.noRecentMachines");
  expect(host.textContent).toContain("StudentHomePage.noPublishedCoursesTitle");
  expect(host.textContent).toContain("StudentHomePage.noQuickTemplatesTitle");
  const allCourses = [...host.querySelectorAll("button")].find((button) => button.textContent.includes("HomeOverview.allCourses"));
  await act(async () => allCourses.click());
  expect(host.querySelector("output").textContent).toBe("/courses");
});

it("refreshes recent connections and passes the actual resource to the launch action", async () => {
  const machine = { vmid: 101, name: "Linux lab", type: "lxc", status: "running" };
  await render({ resources: [machine] });
  await act(async () => recordMachineUse("student", machine.vmid));
  expect(host.textContent).toContain("Linux lab");
  const launch = [...host.querySelectorAll("button")].find((button) => button.textContent.includes("StudentHomePage.actionEnter"));
  await act(async () => launch.click());
  expect(defaults.onOpenMachine).toHaveBeenCalledWith(expect.objectContaining(machine));
});

it("disables unavailable machines and distinguishes failed loads from empty data", async () => {
  recordMachineUse("student", 101);
  await render({ resources: [{ vmid: 101, name: "Expired lab", status: "expired" }], coursesError: true, templatesError: true });
  const launch = [...host.querySelectorAll("button")].find((button) => button.textContent.includes("HomeOverview.unavailable"));
  expect(launch.disabled).toBe(true);
  expect(host.textContent).toContain("StudentHomePage.errorTitle");
  expect(host.textContent).toContain("HomeOverview.templatesFailed");
  expect(host.textContent).not.toContain("StudentHomePage.noPublishedCoursesTitle");
});

it.each([
  ["course", "/courses/course-1"], ["template", "/quick-template/template-1"],
])("opens the selected %s", async (kind, destination) => {
  await render({ paths: [{ id: "course-1", title: "Course title" }], templates: [{ id: "template-1", name: "Template title", nodes: [] }] });
  const label = kind === "course" ? "Course title" : "Template title";
  const button = [...host.querySelectorAll("button")].find((item) => item.textContent.includes(label));
  await act(async () => button.click());
  expect(host.querySelector("output").textContent).toBe(destination);
});
