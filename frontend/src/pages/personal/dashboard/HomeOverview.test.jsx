// @vitest-environment happy-dom
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import HomeOverview from "./HomeOverview";

vi.mock("react-i18next", async (original) => ({ ...await original(),
  useTranslation: () => ({ t: (key) => key, i18n: { language: "zh-TW" } }),
}));
let host, root;
const defaults = { paths: [], resources: [], templates: [], templatesLoading: false,
  openingMachineId: null, onOpenMachine: vi.fn(), todayLabel: "9/17" };
function Location() { const location = useLocation(); return <output data-create={Boolean(location.state?.create)}>{location.pathname}</output>; }
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
  expect(host.textContent).toContain("HomeOverview.noMachines");
  expect(host.textContent).toContain("StudentHomePage.noPublishedCoursesTitle");
  expect(host.textContent).toContain("StudentHomePage.noQuickTemplatesTitle");
  const allCourses = [...host.querySelectorAll("button")].find((button) => button.textContent.includes("HomeOverview.allCourses"));
  await act(async () => allCourses.click());
  expect(host.querySelector("output").textContent).toBe("/courses");
});

it("shows existing machines without connection history and launches the selected resource", async () => {
  const machine = { vmid: 101, name: "Linux lab", type: "lxc", status: "running" };
  await render({ resources: [machine] });
  expect(host.textContent).toContain("Linux lab");
  expect(host.textContent).not.toContain("HomeOverview.noMachines");
  expect(host.textContent).not.toContain("HomeOverview.lastUsed");
  const launch = [...host.querySelectorAll("button")].find((button) => button.textContent.includes("StudentHomePage.actionEnter"));
  await act(async () => launch.click());
  expect(defaults.onOpenMachine).toHaveBeenCalledWith(expect.objectContaining(machine));
});

it("disables unavailable machines and distinguishes failed loads from empty data", async () => {
  await render({ resources: [{ vmid: 101, name: "Expired lab", status: "expired" }], coursesError: true, templatesError: true });
  const launch = [...host.querySelectorAll("button")].find((button) => button.textContent.includes("HomeOverview.unavailable"));
  expect(launch.disabled).toBe(true);
  expect(host.textContent).toContain("StudentHomePage.errorTitle");
  expect(host.textContent).toContain("HomeOverview.templatesFailed");
  expect(host.textContent).not.toContain("StudentHomePage.noPublishedCoursesTitle");
});

it("shows only the first four machines in resource order", async () => {
  await render({ resources: Array.from({ length: 6 }, (_, index) => ({
    vmid: 101 + index, name: `Machine ${index + 1}`, type: "lxc", status: "running",
  })) });
  expect([...host.querySelectorAll("article h3")].map((heading) => heading.textContent))
    .toEqual(["Machine 1", "Machine 2", "Machine 3", "Machine 4"]);
});

it("offers creation only when no machines exist, opening the request form", async () => {
  await render({ resourcesError: true });
  expect(host.textContent).toContain("HomeOverview.resourcesFailed");
  expect(host.textContent).not.toContain("HomeOverview.createMachine");
  await render();
  const create = [...host.querySelectorAll("button")].find((button) => button.textContent.includes("HomeOverview.createMachine"));
  await act(async () => create.click());
  expect(host.querySelector("output").textContent).toBe("/my-requests");
  expect(host.querySelector("output").getAttribute("data-create")).toBe("true");
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
