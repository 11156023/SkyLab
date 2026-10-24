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
vi.mock("../../../../services/quickPractice", () => ({ QuickPracticeService: { listMySessions: vi.fn().mockResolvedValue([]) } }));
vi.mock("../../resources/TerminalDialog", () => ({ default: () => null }));
vi.mock("../../resources/VncDialog", () => ({ default: () => null }));

const { default: StudentCoursePage } = await import("./StudentCoursePage");
const { default: StudentWeekPage } = await import("../../courses/StudentWeekPage");
const { createTestQueryClient, withQueryClient } = await import("../../../../queries/testing");
const { courseKeys } = await import("../../../../queries/courses");

let host, root, client;
function Location() { return <output>{useLocation().pathname}</output>; }
/* TanStack Query 以 macrotask 批次通知，等一個 timer tick 再看畫面 */
async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}
async function render(path) {
  client = createTestQueryClient();
  await act(async () => root.render(withQueryClient(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/courses/:pathId" element={<StudentCoursePage />} />
        <Route path="/courses/:pathId/weeks/:weekId" element={<StudentWeekPage />} />
        <Route path="*" element={null} />
      </Routes>
      <Location />
    </MemoryRouter>,
    client,
  )));
  /* 兩段載入：課程清單回來才知道要抓哪門課的任務與機器 */
  await flush();
  await flush();
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
  /* 週任務頁與課程頁共用快取：點進來當下就有內容，不再整頁載入 */
  expect(host.textContent).not.toContain("StudentWeekPage.loading");
  const feedback = host.querySelector('[data-guide="course-week-feedback"]');
  expect(feedback.textContent).toContain("Configure daily backups");
  expect(feedback.textContent).toContain("Run pg_dump at 02:00");
  expect(feedback.textContent).not.toContain("StudentWeekPage.noCheckpointsTitle");
});

it("checks a week off in place without reloading the course", async () => {
  courses.listPaths.mockResolvedValue([{ id: "path-1", title: "Linux course" }]);
  courses.getAiAssignments.mockResolvedValue([{
    id: "assignment-1", teaching_class_week_id: "week-1", items: [],
    completion: { completed: false },
  }]);
  courses.getWeeklyTasks.mockResolvedValue([{
    id: "week-1", week_number: 1, title: "Backup week", session_date: "2026-09-09", checkpoints: [], files: [],
  }]);
  courses.updateAssignmentCompletion.mockResolvedValue({ completed: true });
  await render("/courses/path-1");

  const checkbox = host.querySelector('[data-guide="home-tasks"] input[type="checkbox"]');
  expect(checkbox.checked).toBe(false);
  await act(async () => checkbox.click());
  await flush();

  expect(courses.updateAssignmentCompletion).toHaveBeenCalledWith("path-1", "assignment-1", true);
  expect(courses.getAiAssignments).toHaveBeenCalledTimes(1);
  expect(client.getQueryData(courseKeys.aiAssignments("path-1"))[0].completion).toEqual({ completed: true });
  expect(host.querySelector('[data-guide="home-tasks"] input[type="checkbox"]').checked).toBe(true);
});

it("shows the guide demo course without calling the course APIs", async () => {
  await render("/courses/demo");
  expect(courses.listPaths).not.toHaveBeenCalled();
  expect(courses.getWeeklyTasks).not.toHaveBeenCalled();
  expect(host.textContent).toContain("StudentCoursePage.guideDemoCourseTitle");
});
