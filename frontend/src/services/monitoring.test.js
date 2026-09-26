/**
 * monitoring.test.js
 * 驗證 MonitoringService 的 URL 組裝（timeframe / active / limit 參數）。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import { MonitoringService } from "./monitoring";

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

describe("MonitoringService", () => {
  test("getNodeRrd 組出節點 RRD 路徑與 timeframe 參數", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, []));

    await MonitoringService.getNodeRrd("pve-01", "day");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/monitoring/nodes/pve-01/rrd?timeframe=day");
  });

  test("listAlerts 帶 active 與 limit 參數", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, []));

    await MonitoringService.listAlerts({ active: true, limit: 50 });

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/monitoring/alerts?");
    expect(url).toContain("active=true");
    expect(url).toContain("limit=50");
  });

  test("getSystemHealth 呼叫平台健康端點並回傳內容", async () => {
    const body = { status: "ok", components: [], loops: [], tasks: [] };
    fetchMock.mockResolvedValueOnce(jsonRes(200, body));

    const result = await MonitoringService.getSystemHealth();

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/monitoring/system-health");
    expect(init?.method ?? "GET").toBe("GET");
    expect(result).toEqual(body);
  });
});
