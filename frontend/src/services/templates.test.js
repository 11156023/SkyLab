/**
 * templates.test.js
 * 驗證 TemplatesService 的 URL 組裝。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import { TemplatesService } from "./templates";

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

describe("TemplatesService", () => {
  test("clone 以 POST 送出克隆參數", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { tasks: [] }));

    await TemplatesService.clone("tpl-1", { hostname: "lab", count: 3, start: true });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/templates/tpl-1/clone");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ hostname: "lab", count: 3, start: true });
  });

  test("startUpdateCycle 打到 update-cycle/start", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, {}));

    await TemplatesService.startUpdateCycle("tpl-1");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/templates/tpl-1/update-cycle/start");
  });
});
