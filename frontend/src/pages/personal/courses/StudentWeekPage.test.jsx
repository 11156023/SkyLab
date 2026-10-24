// @vitest-environment happy-dom
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { toast } from "sonner";
import { CoursesService } from "../../../services/courses";
import { withQueryClient } from "../../../queries/testing";
import StudentWeekPage from "./StudentWeekPage";

const translation = { t: (key) => key, i18n: { language: "zh-TW" } };
vi.mock("react-i18next", async (original) => ({ ...await original(),
  useTranslation: () => translation,
}));
vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));

let host, root, preview;
const week = (extra) => ({
  id: "week-1", title: "Linux", week_number: 1, session_date: "2026-10-16",
  checkpoints: [], files: [], ...extra,
});
/* TanStack Query 以 macrotask 批次通知，等一個 timer tick 再看畫面 */
async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}
async function render(extra) {
  vi.spyOn(CoursesService, "getWeeklyTasks").mockResolvedValue([week(extra)]);
  await act(async () => {
    root.render(withQueryClient(<MemoryRouter initialEntries={["/courses/path-1/weeks/week-1"]}>
      <Routes><Route path="/courses/:pathId/weeks/:weekId" element={<StudentWeekPage />} /></Routes>
    </MemoryRouter>));
  });
  await flush();
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.spyOn(CoursesService, "getPath").mockResolvedValue({ title: "Course" });
  vi.spyOn(CoursesService, "getPracticeMachines").mockResolvedValue([]);
  preview = { location: "", close: vi.fn() };
  vi.spyOn(window, "open").mockReturnValue(preview);
  vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:task-pdf");
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

test("AI 任務上傳的 PDF 出現在教材，透過作業端點開啟", async () => {
  const source = vi.spyOn(CoursesService, "getAiAssignmentSourceDocument")
    .mockResolvedValue(new Blob(["%PDF-1.4"], { type: "application/pdf" }));
  const weekly = vi.spyOn(CoursesService, "getWeeklyTaskDocument");
  await render({ source_documents: [{ assignment_id: "assignment-1", filename: "task.pdf", display_name: "任務文件" }] });
  const materials = host.querySelector('[data-guide="course-week-materials"]');
  expect(materials.textContent).toContain("task.pdf");
  await act(async () => materials.querySelector("button").click());
  expect(source).toHaveBeenCalledExactlyOnceWith("path-1", "assignment-1");
  expect(weekly).not.toHaveBeenCalled();
  expect(preview.location).toBe("blob:task-pdf");
});

test("每週教材與 AI 任務 PDF 同時顯示，各自走正確的預覽端點", async () => {
  const source = vi.spyOn(CoursesService, "getAiAssignmentSourceDocument");
  const weekly = vi.spyOn(CoursesService, "getWeeklyTaskDocument")
    .mockResolvedValue(new Blob(["%PDF-1.4"], { type: "application/pdf" }));
  await render({ files: [{ id: "file-1", filename: "notes.pdf" }],
    source_documents: [{ assignment_id: "assignment-1", filename: "task.pdf" }] });
  const buttons = host.querySelectorAll('[data-guide="course-week-materials"] button');
  expect(buttons).toHaveLength(2);
  await act(async () => buttons[0].click());
  expect(weekly).toHaveBeenCalledExactlyOnceWith("path-1", "week-1", "file-1");
  expect(source).not.toHaveBeenCalled();
});

test("每週任務載不到時顯示錯誤狀態，不當成找不到這週", async () => {
  vi.spyOn(CoursesService, "getWeeklyTasks").mockRejectedValue(new Error("500"));
  await act(async () => {
    root.render(withQueryClient(<MemoryRouter initialEntries={["/courses/path-1/weeks/week-1"]}>
      <Routes><Route path="/courses/:pathId/weeks/:weekId" element={<StudentWeekPage />} /></Routes>
    </MemoryRouter>));
  });
  await flush();
  expect(host.textContent).not.toContain("StudentWeekPage.loading");
  expect(host.textContent).not.toContain("StudentWeekPage.notFoundTitle");
  expect(host.textContent).toContain("StudentWeekPage.back");
});

test("PDF 無法開啟時關閉空白預覽並恢復按鈕", async () => {
  vi.spyOn(CoursesService, "getAiAssignmentSourceDocument").mockRejectedValue(new Error("not found"));
  await render({ source_documents: [{ assignment_id: "assignment-1", filename: "task.pdf" }] });
  const button = host.querySelector('[data-guide="course-week-materials"] button');
  await act(async () => button.click());
  expect(preview.close).toHaveBeenCalledOnce();
  expect(toast.error).toHaveBeenCalledWith("not found");
  expect(button.disabled).toBe(false);
});
