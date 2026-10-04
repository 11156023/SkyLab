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

  test("getSnapshotCapability 以 GET 查詢這台機器能否使用快照", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonRes(200, { available: false, reason: "unsupported" }),
    );

    const result = await ResourcesService.getSnapshotCapability(105);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/resources/105/snapshot-capability");
    expect(init?.method ?? "GET").toBe("GET");
    expect(result).toEqual({ available: false, reason: "unsupported" });
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

  test("getBackupCapability 以 GET 查詢這台機器能否使用備份", async () => {
    const body = { available: true, reason: null, requires_shutdown: true, max_count: 2 };
    fetchMock.mockResolvedValueOnce(jsonRes(200, body));

    const result = await ResourcesService.getBackupCapability(105);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/resources/105/backup-capability");
    expect(init?.method ?? "GET").toBe("GET");
    expect(result).toEqual(body);
  });

  test("listBackups 以 GET 取得備份清單", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, []));

    await ResourcesService.listBackups(105);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/api\/v1\/resources\/105\/backups$/);
    expect(init?.method ?? "GET").toBe("GET");
  });

  test("createBackup 以 POST 送出描述", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(202, { message: "ok", task_id: "t1" }));

    const result = await ResourcesService.createBackup(105, { description: "升級前" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/api\/v1\/resources\/105\/backups$/);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ description: "升級前" });
    expect(result.task_id).toBe("t1");
  });

  test("restoreBackup 以 POST 送出 volid", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(202, { message: "ok", task_id: "t2" }));

    await ResourcesService.restoreBackup(105, "pbs:backup/ct/105/2026-09-20T00:00:00Z");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/resources/105/backups/restore");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ volid: "pbs:backup/ct/105/2026-09-20T00:00:00Z" });
  });

  test("deleteBackup 以 DELETE 送出，volid 放在編碼過的 query", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { message: "ok" }));

    await ResourcesService.deleteBackup(105, "pbs:backup/ct/105/2026-09-20T00:00:00Z");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain(
      "/api/v1/resources/105/backups?volid=pbs%3Abackup%2Fct%2F105%2F2026-09-20T00%3A00%3A00Z",
    );
    expect(init.method).toBe("DELETE");
  });

  test("updateSpecDirect 以 PUT 送出規格", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, {}));

    await ResourcesService.updateSpecDirect(105, { cores: 4, memory: 8192, disk_size: 50 });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/resources/105/spec/direct");
    expect(init.method).toBe("PUT");
  });

  test("mySessionStatuses 一次取回本人所有機器的 session 狀態", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, [{ vmid: 105, should_warn: true }]));

    const result = await ResourcesService.mySessionStatuses();

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/resources/my/session-status");
    expect(init?.method ?? "GET").toBe("GET");
    expect(result).toEqual([{ vmid: 105, should_warn: true }]);
  });
});
