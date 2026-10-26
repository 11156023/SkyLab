/**
 * feedback.test.js
 * 驗證使用者回饋 service 的 URL／method／query string，以及類型對照 helper。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import {
  FEEDBACK_CATEGORIES,
  FEEDBACK_CONTENT_MAX,
  FEEDBACK_CONTENT_MIN,
  FeedbackService,
  feedbackCategoryMeta,
} from "./feedback";

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

describe("FeedbackService", () => {
  test("submit 以 POST 打 /feedback/ 並帶 JSON body", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(201, { id: "f1", status: "open" }));

    const res = await FeedbackService.submit({ category: "bug", content: "按鈕沒反應", app_version: "1.0.0" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/feedback/");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ category: "bug", content: "按鈕沒反應", app_version: "1.0.0" });
    expect(res.id).toBe("f1");
  });

  test("listAll 預設帶 skip/limit，只帶有值的篩選", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { data: [], count: 0 }));
    await FeedbackService.listAll();
    expect(fetchMock.mock.calls[0][0]).toContain("/api/v1/feedback/?skip=0&limit=50");
    expect(fetchMock.mock.calls[0][0]).not.toContain("status=");

    fetchMock.mockResolvedValueOnce(jsonRes(200, { data: [], count: 0 }));
    await FeedbackService.listAll({ skip: 20, limit: 20, status: "open", category: "" });
    const url = fetchMock.mock.calls[1][0];
    expect(url).toContain("skip=20");
    expect(url).toContain("limit=20");
    expect(url).toContain("status=open");
    expect(url).not.toContain("category=");
    expect(fetchMock.mock.calls[1][1].method ?? "GET").toBe("GET");
  });

  test("setStatus 以 PATCH 打 /{id} 並帶 status", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { id: "f1", status: "resolved" }));

    await FeedbackService.setStatus("f1", "resolved");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/feedback/f1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body)).toEqual({ status: "resolved" });
  });

  test("remove 以 DELETE 打 /{id}，204 回 null", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, status: 204 });

    const res = await FeedbackService.remove("f1");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/feedback/f1");
    expect(init.method).toBe("DELETE");
    expect(res).toBeNull();
  });
});

describe("feedback helpers", () => {
  test("長度限制與後端一致", () => {
    expect(FEEDBACK_CONTENT_MIN).toBe(5);
    expect(FEEDBACK_CONTENT_MAX).toBe(2000);
  });

  test("類型對照：三種類型各有文案 key 與顏色，未知值退回「其他」", () => {
    expect(FEEDBACK_CATEGORIES.map((c) => c.value)).toEqual(["bug", "suggestion", "other"]);
    expect(feedbackCategoryMeta("bug")).toMatchObject({ labelKey: "Feedback.categoryBug", color: "danger" });
    expect(feedbackCategoryMeta("nope").value).toBe("other");
  });
});
