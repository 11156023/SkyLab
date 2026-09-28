/**
 * courses.test.js
 * 驗證 CoursesService / CourseAdminService 的 URL、method 與 body。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import { CourseAdminService, CoursesService } from "./courses";

function fakeStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
  };
}

const jsonRes = (status, body = {}) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
});

const blobRes = (status, body) => ({
  ok: status >= 200 && status < 300,
  status,
  blob: async () => body,
});

let fetchMock;

beforeEach(() => {
  vi.stubGlobal("localStorage", fakeStorage());
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

describe("CoursesService", () => {
  test("首頁課表與提醒使用各自的學生 API", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonRes(200, []))
      .mockResolvedValueOnce(jsonRes(200, []));

    await CoursesService.listSchedule();
    await CoursesService.listReminders();

    expect(fetchMock.mock.calls[0][0]).toContain("/api/v1/courses/schedule");
    expect(fetchMock.mock.calls[1][0]).toContain("/api/v1/courses/reminders");
    expect(fetchMock.mock.calls[0][1].method).toBe("GET");
    expect(fetchMock.mock.calls[1][1].method).toBe("GET");
  });

  test("listPaths 以 GET 打 /courses/paths", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, []));
    await CoursesService.listPaths();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/courses/paths");
    expect(init.method).toBe("GET");
  });

  test("學生以整週任務切換自己的完成狀態", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { completed: true }));

    await CoursesService.updateAssignmentCompletion("path-1", "assignment-1", true);

    expect(fetchMock.mock.calls[0][0]).toContain(
      "/api/v1/courses/paths/path-1/ai-assignments/assignment-1/completion",
    );
    expect(fetchMock.mock.calls[0][1].method).toBe("PUT");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      completed: true,
    });
  });

  test("學生取得已發布的每週任務並透過受保護端點預覽 PDF", async () => {
    const pdf = new Blob(["weekly-pdf"], { type: "application/pdf" });
    fetchMock
      .mockResolvedValueOnce(jsonRes(200, [{ id: "week-1", title: "Linux 權限" }]))
      .mockResolvedValueOnce(blobRes(200, pdf));

    await CoursesService.getWeeklyTasks("path-1");
    const result = await CoursesService.getWeeklyTaskDocument("path-1", "week-1", "file-1");

    expect(fetchMock.mock.calls[0][0]).toContain("/api/v1/courses/paths/path-1/weekly-tasks");
    expect(fetchMock.mock.calls[0][1].method).toBe("GET");
    expect(fetchMock.mock.calls[1][0]).toContain(
      "/api/v1/courses/paths/path-1/weekly-tasks/week-1/files/file-1",
    );
    expect(fetchMock.mock.calls[1][1].method).toBe("GET");
    expect(result).toBe(pdf);
  });

  test("getPracticeMachines 取得課程的所有班級機器", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, []));

    await CoursesService.getPracticeMachines("path-1");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/courses/paths/path-1/practice-machines");
    expect(init.method).toBe("GET");
  });

});

describe("CourseAdminService", () => {
  test("createQuestion 以 POST 送 flag 明文 body", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(201, { id: "q1" }));
    await CourseAdminService.createQuestion({
      task_id: "t-1",
      prompt: "找出 root 目錄的 flag",
      question_type: "flag",
      flag: "FLAG{root}",
      points: 10,
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/admin/courses/questions");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body).flag).toBe("FLAG{root}");
  });

  test("updateRoom 以 PUT 打 /rooms/{id} 並帶 body", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { id: "r-1" }));
    await CourseAdminService.updateRoom("r-1", { title: "新標題", difficulty: "hard" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/admin/courses/rooms/r-1");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body)).toEqual({ title: "新標題", difficulty: "hard" });
  });

  test("updateQuestion 以 PUT 打 /questions/{id} 並帶 body", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { id: "q-1" }));
    await CourseAdminService.updateQuestion("q-1", { prompt: "改過的題目", points: 20 });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/admin/courses/questions/q-1");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body)).toEqual({ prompt: "改過的題目", points: 20 });
  });

  test("publishPath 以 PUT 送 published 布林", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { status: "published" }));
    await CourseAdminService.publishPath("p-1", true);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/admin/courses/paths/p-1/publish");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body)).toEqual({ published: true });
  });

  test("getPathProgress 以 GET 打 /paths/{id}/progress", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { students: [] }));
    await CourseAdminService.getPathProgress("p-1");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/admin/courses/paths/p-1/progress");
    expect(init.method).toBe("GET");
  });
});
