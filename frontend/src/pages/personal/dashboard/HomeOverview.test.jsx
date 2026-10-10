// @vitest-environment happy-dom
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { recordMachineUse } from "../../../services/recentMachines";
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

const allCoursesButton = () => [...host.querySelectorAll("button")].find((button) => button.textContent.includes("HomeOverview.allCourses"));

it("shows real empty states and hides all courses when there is no course", async () => {
  await render();
  expect(host.textContent).toContain("HomeOverview.noMachines");
  expect(host.textContent).toContain("StudentHomePage.noPublishedCoursesTitle");
  expect(host.textContent).toContain("StudentHomePage.noQuickTemplatesTitle");
  expect(allCoursesButton()).toBeUndefined();
});

it("keeps all courses reachable once there are courses or the schedule failed to load", async () => {
  await render({ paths: [{ id: "c1", title: "Linux 101", state: "upcoming", completed_questions: 0, total_questions: 3 }] });
  await act(async () => allCoursesButton().click());
  expect(host.querySelector("output").textContent).toBe("/courses");

  await render({ coursesError: true });
  expect(allCoursesButton()).toBeDefined();
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

it("prints small machine facts as terminal output and keeps them inside the card terminal", async () => {
  await render({ resources: [{ vmid: 104, name: "ml-lab-03", type: "qemu", status: "running",
    guest_os: { pretty_name: "Ubuntu 24.04.1 LTS" }, ip_address: "10.20.3.41", uptime: 8040 }] });
  const terminal = host.querySelector('[aria-label="HomeOverview.machineTerminalLabel"]');
  expect(terminal.textContent).toContain("# vm 104  Ubuntu 24.04.1 LTS");
  expect(terminal.textContent).toContain("10.20.3.41");
  expect(terminal.textContent).toContain("2 h 14 min");
  /* VMID 只在終端裡出現一次，卡片上不再另列 #104 */
  expect(host.textContent.match(/104/g)).toHaveLength(1);
});

it("prints the last connection recorded in this browser without reordering machines", async () => {
  const resources = [
    { vmid: 101, name: "First", type: "lxc", status: "running" },
    { vmid: 102, name: "Second", type: "lxc", status: "stopped" },
  ];
  await render({ resources, userId: "alice" });
  const terminals = () => [...host.querySelectorAll('[aria-label="HomeOverview.machineTerminalLabel"]')];
  expect(terminals()[1].textContent).not.toContain("last");
  await act(async () => recordMachineUse("alice", 102));
  expect(terminals()[1].textContent).toMatch(/last\s+\S/);
  expect(terminals()[0].textContent).not.toContain("last");
  expect([...host.querySelectorAll("article h3")].map((heading) => heading.textContent)).toEqual(["First", "Second"]);
});

it("keeps the console closed while a machine is still booting", async () => {
  await render({ resources: [{ vmid: 438, name: "GPU lab", type: "qemu", status: "starting" }] });
  expect(host.textContent).toContain("HomeOverview.machineStatus.starting");
  const launch = [...host.querySelectorAll("button")].find((button) => button.textContent.includes("StudentHomePage.actionStarting"));
  expect(launch.disabled).toBe(true);
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

it("titles class machines with the course environment name, keeping the hostname on hover", async () => {
  await render({ resources: [
    { vmid: 201, name: "cls-59cd29db-1-1", course_environment_name: "Linux 系統管理", type: "lxc", status: "stopped" },
    { vmid: 202, name: "jerry", type: "lxc", status: "running" },
  ] });
  const headings = [...host.querySelectorAll("article h3")];
  expect(headings.map((heading) => heading.textContent)).toEqual(["Linux 系統管理", "jerry"]);
  expect(headings[0].getAttribute("title")).toBe("cls-59cd29db-1-1");
  expect(headings[1].hasAttribute("title")).toBe(false);
});

it("shows a plain empty state without its own create button when no machines exist", async () => {
  await render({ resourcesError: true });
  expect(host.textContent).toContain("HomeOverview.resourcesFailed");
  await render();
  const section = host.querySelector('[aria-labelledby="recent-machines-title"]');
  expect(section.querySelector("[data-empty-state]").textContent).toContain("HomeOverview.noMachines");
  // 建機器的入口只留頁底「建立自己的研究環境」
  expect(section.querySelector("button")).toBeNull();
});

it("sends independent research straight to the request form, not the request list", async () => {
  await render();
  const research = [...host.querySelectorAll("button")].find((button) => button.textContent.includes("StudentHomePage.buildResearchEnv"));
  await act(async () => research.click());
  expect(host.querySelector("output").textContent).toBe("/my-requests");
  expect(host.querySelector("output").getAttribute("data-create")).toBe("true");
});

it("prints each course as a ticket with only the schedule fields the API has", async () => {
  vi.useFakeTimers({ now: new Date("2026-09-30T15:22:00+08:00"), toFake: ["Date"] });
  try {
    await render({ paths: [
      { id: "course-1", title: "雲端系統實作", state: "now", teacher: "林致遠", location: "E301",
        session_date: "2026-09-30", start_at: "2026-09-30T14:10:00+08:00", end_at: "2026-09-30T17:00:00+08:00",
        total_questions: 24, completed_questions: 10 },
      { id: "course-2", title: "資料庫系統", state: "available", teacher: "db@campus.edu", location: null,
        session_date: "2026-10-09", start_at: "2026-10-09T09:10:00+08:00", end_at: "2026-10-09T12:00:00+08:00",
        total_questions: 0, completed_questions: 0 },
    ] });
  } finally {
    vi.useRealTimers();
  }
  const ticket = (title) => [...host.querySelectorAll("button")].find((button) => button.textContent.includes(title));
  const live = ticket("雲端系統實作");
  expect(live.dataset.state).toBe("now");
  expect(live.textContent).toContain("CourseTicket.status.now");
  expect(live.textContent).toContain("CourseTicket.teacher");
  expect(live.textContent).toContain("今天");
  expect(live.textContent).toContain("E301");
  expect(live.textContent).toContain("CourseTicket.solved");
  /* 條碼一題一條：24 條加上中間 23 個間隔 */
  expect(live.querySelector('[aria-hidden="true"]').children).toHaveLength(47);

  const practice = ticket("資料庫系統");
  expect(practice.textContent).toContain("CourseTicket.status.available");
  expect(practice.textContent).toContain("10/9");
  expect(practice.textContent).toContain("db@campus.edu");
  expect(practice.textContent).not.toContain("CourseTicket.teacher");
  expect(practice.textContent).not.toContain("CourseTicket.room");
  expect(practice.textContent).toContain("CourseTicket.noQuestions");
  expect(practice.querySelector('[aria-hidden="true"]')).toBeNull();
});

it("files each quick-practice machine as a sheet in the environment's folder", async () => {
  const machine = (name, role, type = "lxc") => ({ id: name, name, role, type, resource_type: type, cpu: 1, memory_mb: 1024 });
  await render({ templates: [
    { id: "web", name: "Web 攻防演練", description: "練習 SQL injection", duration_hours: 4,
      per_student: { machines: 2, cpu_cores: 3, memory_mb: 5120 },
      nodes: [machine("kali", "攻擊機", "qemu"), machine("db", "db")] },
    { id: "lb", name: "負載平衡", duration_hours: 4,
      nodes: ["lb", "web-1", "web-2", "web-3", "redis", "db"].map((name) => machine(name, name)) },
  ] });
  const folder = (name) => [...host.querySelectorAll("button")].find((button) => button.textContent.includes(name));

  const web = folder("Web 攻防演練");
  expect(web.getAttribute("aria-label")).toBe("QuickTemplateFolder.aria");
  expect(web.textContent).toContain("kali");
  expect(web.textContent).toContain("攻擊機");
  /* 角色跟名稱一樣時只寫一次 */
  expect(web.textContent.match(/db/g)).toHaveLength(1);
  expect(web.textContent).toContain("QuickTemplateFolder.cpu");
  expect(web.textContent).toContain("QuickTemplateFolder.memory");
  expect(host.querySelector(`#${web.getAttribute("aria-describedby")}`).textContent).toBe("練習 SQL injection");

  const lb = folder("負載平衡");
  expect(lb.textContent).toContain("web-2");
  expect(lb.textContent).not.toContain("web-3");
  expect(lb.textContent).toContain("+HomeOverview.machineCount");
  expect(lb.hasAttribute("aria-describedby")).toBe(false);
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
