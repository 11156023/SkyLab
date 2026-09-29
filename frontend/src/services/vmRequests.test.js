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

  test("listAll 預設只抓一頁 100 筆並帶狀態", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { data: [], count: 0 }));

    await VmRequestsService.listAll("pending", { limit: 1 });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("status=pending");
    expect(url).toContain("limit=1");
    expect(url).not.toContain("skip=");
  });

  test("listAll 的 limit 超過後端上限 100 時用 skip 分頁補齊，較舊的待審件不會漏掉", async () => {
    const rows = (from, n) => Array.from({ length: n }, (_, i) => ({ id: `r${from + i}` }));
    fetchMock
      .mockResolvedValueOnce(jsonRes(200, { data: rows(0, 100), count: 150 }))
      .mockResolvedValueOnce(jsonRes(200, { data: rows(100, 50), count: 150 }));

    const res = await VmRequestsService.listAll("pending", { limit: 1000 });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][0]).toContain("limit=100");
    expect(fetchMock.mock.calls[0][0]).not.toContain("skip=");
    expect(fetchMock.mock.calls[1][0]).toContain("skip=100");
    expect(res.data).toHaveLength(150);
    expect(res.count).toBe(150);
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
