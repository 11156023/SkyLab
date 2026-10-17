// @vitest-environment happy-dom
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const courses = {
  listPaths: vi.fn(),
  listSchedule: vi.fn(),
  getPath: vi.fn(),
  getAiAssignments: vi.fn(),
  getWeeklyTasks: vi.fn(),
  getPracticeMachines: vi.fn(),
  updateAssignmentCompletion: vi.fn(),
};
const resources = { list: vi.fn(), get: vi.fn(), start: vi.fn() };

/* t 必須是穩定參考：頁面的載入 effect 依賴 t */
const translation = { t: (key) => key, i18n: { language: "zh-TW" } };
vi.mock("react-i18next", async (original) => ({ ...await original(),
  useTranslation: () => translation,
}));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), info: vi.fn(), success: vi.fn() } }));
vi.mock("../../../../services/courses", () => ({ CoursesService: courses }));
vi.mock("../../../../services/resources", () => ({ ResourcesService: resources }));
vi.mock("../../resources/TerminalDialog", () => ({ default: () => null }));
vi.mock("../../resources/VncDialog", () => ({ default: () => null }));

const { default: StudentCoursePage } = await import("./StudentCoursePage");
const { default: StudentWeekPage } = await import("../../courses/StudentWeekPage");

let host, root;
function Location() { return <output>{useLocation().pathname}</output>; }
async function render(path) {
  await act(async () => root.render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/courses/:pathId" element={<StudentCoursePage />} />
        <Route path="/courses/:pathId/weeks/:weekId" element={<StudentWeekPage />} />
        <Route path="*" element={null} />
      </Routes>
      <Location />
    </MemoryRouter>,
  ));
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  courses.listSchedule.mockResolvedValue([]);
  courses.getPath.mockResolvedValue({ rooms: [] });
  courses.getAiAssignments.mockResolvedValue([]);
  courses.getWeeklyTasks.mockResolvedValue([]);
  courses.getPracticeMachines.mockResolvedValue([]);
  resources.list.mockResolvedValue([]);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

it("shows the load-failure notice when only the course list fails", async () => {
  courses.listPaths.mockRejectedValue(new Error("500"));
  await render("/courses/1");
  expect(host.textContent).toContain("StudentCoursePage.noticeTitle");
});

it("opens weeks of the course actually shown after falling back from an unknown course id", async () => {
  courses.listPaths.mockResolvedValue([{ id: 2, title: "Course B", progress_percent: 0 }]);
  courses.getWeeklyTasks.mockResolvedValue([{ id: 9, week_number: 1, title: "Week one", session_date: "2026-09-09", checkpoints: [], files: [] }]);
  await render("/courses/1");
  expect(host.textContent).toContain("Course B");
  expect(courses.getWeeklyTasks).toHaveBeenCalledWith(2);

  const weekButton = [...host.querySelectorAll("button")].find((button) => button.textContent.includes("Week one"));
  await act(async () => weekButton.click());
  expect(host.querySelector("output").textContent).toBe("/courses/2/weeks/9");
});


it("opens the current week's approved task content from the course overview", async () => {
  const items = [{ id: "backup", title: "Configure daily backups", description: "Run pg_dump at 02:00", detectable: "auto" }];
  courses.listPaths.mockResolvedValue([{ id: "path-1", title: "Linux course" }]);
  courses.getAiAssignments.mockResolvedValue([{
    id: "assignment-1", teaching_class_week_id: "week-1", items,
    completion: { completed: false },
  }]);
  courses.getWeeklyTasks.mockResolvedValue([{
    id: "week-1", week_number: 1, title: "Backup week", session_date: "2026-09-09",
    checkpoints: items, files: [],
  }]);
  await render("/courses/path-1");
  expect(host.querySelector('[data-guide="home-tasks"] input[type="checkbox"]')).toBeTruthy();
  const openWeek = [...host.querySelectorAll("button")]
    .find((button) => button.textContent.includes("StudentCoursePage.openThisWeek"));
  await act(async () => openWeek.click());
  expect(host.querySelector("output").textContent).toBe("/courses/path-1/weeks/week-1");
  const feedback = host.querySelector('[data-guide="course-week-feedback"]');
  expect(feedback.textContent).toContain("Configure daily backups");
  expect(feedback.textContent).toContain("Run pg_dump at 02:00");
  expect(feedback.textContent).not.toContain("StudentWeekPage.noCheckpointsTitle");
});
