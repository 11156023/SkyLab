/**
 * vmRequests.test.js
 * 驗證 VmRequestsService 的 URL 組裝。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import { VmRequestsService } from "./vmRequests";

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

describe("VmRequestsService", () => {
  test("advise 以 POST 送出工作負載欄位", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonRes(200, { resource_type: "lxc", confidence: "high", reasons: [] }),
    );

    await VmRequestsService.advise({ reason: "跑 nginx 網站", cores: 2, memory: 2048 });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/vm-requests/advise");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      reason: "跑 nginx 網站",
      cores: 2,
      memory: 2048,
    });
  });

  test("create 打到 /api/v1/vm-requests/ 並帶 requested_mode", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, {}));

    await VmRequestsService.create({ resource_type: "vm", requested_mode: "auto" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/vm-requests/");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      resource_type: "vm",
      requested_mode: "auto",
    });
  });
});
