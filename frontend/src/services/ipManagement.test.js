/**
 * ipManagement.test.js
 * 驗證 IpManagementService 各函式的 URL 與 method 組裝。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import { IpManagementService } from "./ipManagement";

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

describe("IpManagementService", () => {
  test("getSubnet 走 GET /ip-management/subnet", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, null));

    await IpManagementService.getSubnet();

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/ip-management/subnet");
    expect(options.method).toBe("GET");
  });

  test("upsertSubnet 走 PUT /ip-management/subnet 並帶 body", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { cidr: "10.10.0.0/24" }));

    await IpManagementService.upsertSubnet({
      cidr: "10.10.0.0/24",
      gateway: "10.10.0.1",
      bridge_name: "vmbr1",
      gateway_vm_ip: "10.10.0.2",
      dns_servers: null,
      extra_blocked_subnets: ["192.168.100.0/24"],
    });

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/ip-management/subnet");
    expect(options.method).toBe("PUT");
    const body = JSON.parse(options.body);
    expect(body.cidr).toBe("10.10.0.0/24");
    expect(body.extra_blocked_subnets).toEqual(["192.168.100.0/24"]);
  });

  test("deleteSubnet 走 DELETE /ip-management/subnet", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { message: "子網配置已刪除" }));

    await IpManagementService.deleteSubnet();

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/ip-management/subnet");
    expect(options.method).toBe("DELETE");
  });

  test("listAllocations 不帶後端不收的分頁參數", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { allocations: [], total: 0 }));

    await IpManagementService.listAllocations();

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/ip-management/allocations");
    expect(url).not.toContain("skip=");
    expect(url).not.toContain("limit=");
    expect(options.method).toBe("GET");
  });

  test("getStatus 走 GET /ip-management/status", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { configured: false }));

    await IpManagementService.getStatus();

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/ip-management/status");
    expect(options.method).toBe("GET");
  });
});
