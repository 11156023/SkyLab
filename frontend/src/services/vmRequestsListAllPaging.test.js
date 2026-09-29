/**
 * vmRequestsListAllPaging.test.js
 * VmRequestsService.listAll 的第二個參數（分頁）要向後相容：
 * 只帶狀態的舊呼叫端（管理員儀表板）維持 limit=100，skip 只有非 0 才帶。
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

describe("VmRequestsService.listAll 分頁參數", () => {
  test("只帶狀態時預設 limit=100、不帶 skip", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { data: [], count: 0 }));

    await VmRequestsService.listAll("pending");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("status=pending");
    expect(url).toContain("limit=100");
    expect(url).not.toContain("skip=");
  });

  test("status 為 all 或未給時不帶狀態篩選", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonRes(200, { data: [], count: 0 }))
      .mockResolvedValueOnce(jsonRes(200, { data: [], count: 0 }));

    await VmRequestsService.listAll("all");
    await VmRequestsService.listAll();

    for (const [url] of fetchMock.mock.calls) {
      expect(url).not.toContain("status=");
      expect(url).toContain("limit=100");
    }
  });

  test("有給 skip 時一併帶上", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { data: [], count: 0 }));

    await VmRequestsService.listAll("approved", { skip: 20, limit: 10 });

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("status=approved");
    expect(url).toContain("limit=10");
    expect(url).toContain("skip=20");
  });
});
