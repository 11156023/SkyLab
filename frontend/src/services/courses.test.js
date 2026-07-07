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

let fetchMock;

beforeEach(() => {
  vi.stubGlobal("localStorage", fakeStorage());
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

describe("CoursesService", () => {
  test("listPaths 以 GET 打 /courses/paths", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, []));
    await CoursesService.listPaths();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/courses/paths");
    expect(init.method).toBe("GET");
  });

  test("deployRoom 以 POST 打 /rooms/{id}/deploy", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(202, { id: "d1" }));
    await CoursesService.deployRoom("room-1");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/courses/rooms/room-1/deploy");
    expect(init.method).toBe("POST");
  });

  test("submitAnswer 以 POST 送 answer body", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { correct: true }));
    await CoursesService.submitAnswer("q-1", "FLAG{x}");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/courses/questions/q-1/submit");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ answer: "FLAG{x}" });
  });

  test("terminateDeployment 以 DELETE 打 /deployments/{id}", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { status: "expired" }));
    await CoursesService.terminateDeployment("d-1");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/courses/deployments/d-1");
    expect(init.method).toBe("DELETE");
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
