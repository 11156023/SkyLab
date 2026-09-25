/**
 * setup.test.js
 * 驗證 SetupService 各函式的 URL、method 與 body 組裝。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import { SetupService } from "./setup";

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

describe("SetupService", () => {
  test("getStatus 走 GET /setup/status 且不帶 Authorization", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonRes(200, { completed: false, steps: { admin: false, proxmox: false, subnet: false } }),
    );

    const result = await SetupService.getStatus();

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/setup/status");
    expect(options.method).toBe("GET");
    expect(options.headers.Authorization).toBeUndefined();
    expect(result.completed).toBe(false);
  });

  test("createAdmin 走 POST /setup/admin 帶 JSON body", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { email: "a@b.c", created: true }));

    await SetupService.createAdmin({ email: "a@b.c", password: "secret-123" });

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/setup/admin");
    expect(options.method).toBe("POST");
    expect(JSON.parse(options.body)).toEqual({ email: "a@b.c", password: "secret-123" });
  });

  test("testProxmox 與 createProxmox 各走自己的端點", async () => {
    fetchMock.mockResolvedValue(jsonRes(200, { success: true, nodes: [] }));

    await SetupService.testProxmox({ host: "pve", user: "root@pam", password: "x" });
    await SetupService.createProxmox({ name: "lab", host: "pve", user: "root@pam", password: "x" });

    expect(fetchMock.mock.calls[0][0]).toContain("/api/v1/setup/proxmox/test");
    expect(fetchMock.mock.calls[1][0]).toContain("/api/v1/setup/proxmox");
    expect(fetchMock.mock.calls[1][1].method).toBe("POST");
  });

  test("configureSubnet 走 POST /setup/subnet", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { cidr: "10.0.0.0/24" }));

    await SetupService.configureSubnet({ cidr: "10.0.0.0/24", gateway: "10.0.0.1" });

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/setup/subnet");
    expect(JSON.parse(options.body).cidr).toBe("10.0.0.0/24");
  });

  test("complete 走 POST /setup/complete", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { completed: true }));

    const result = await SetupService.complete();

    expect(fetchMock.mock.calls[0][0]).toContain("/api/v1/setup/complete");
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
    expect(result.completed).toBe(true);
  });

  test("失敗時丟出 { status, message }", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(403, { detail: "已完成初始化" }));

    await expect(SetupService.complete()).rejects.toMatchObject({
      status: 403,
      message: "已完成初始化",
    });
  });
});
