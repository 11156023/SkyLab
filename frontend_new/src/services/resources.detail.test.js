/**
 * resources.detail.test.js
 * 驗證 ResourcesService 詳情端點的 URL 組裝與 body。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import { ResourcesService } from "./resources";

/** 假 localStorage */
function fakeStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
  };
}

/** 模擬 fetch Response */
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

describe("ResourcesService 詳情端點", () => {
  test("getStats 組出 RRD 路徑與 timeframe", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { timeframe: "day", data: [] }));

    await ResourcesService.getStats(105, "day");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/resources/105/stats?timeframe=day");
  });

  test("createSnapshot 以 POST 送出快照參數", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { success: true }));

    await ResourcesService.createSnapshot(105, {
      snapname: "before-upgrade",
      description: "升級前",
      vmstate: false,
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/resources/105/snapshots");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      snapname: "before-upgrade",
      description: "升級前",
      vmstate: false,
    });
  });

  test("updateSpecDirect 以 PUT 送出規格", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, {}));

    await ResourcesService.updateSpecDirect(105, { cores: 4, memory: 8192, disk_size: 50 });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/resources/105/spec/direct");
    expect(init.method).toBe("PUT");
  });
});
