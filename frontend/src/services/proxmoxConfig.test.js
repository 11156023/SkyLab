/**
 * proxmoxConfig.test.js
 * 驗證 ProxmoxConfigService 多連線相關函式的 URL 與 method 組裝。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import { ProxmoxConfigService } from "./proxmoxConfig";

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

describe("ProxmoxConfigService 多連線", () => {
  test("listConnections 走 GET /connections", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, []));

    await ProxmoxConfigService.listConnections();

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/proxmox-config/connections");
    expect(options.method).toBe("GET");
  });

  test("createConnection 走 POST /connections 並帶 body", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { id: 1 }));

    await ProxmoxConfigService.createConnection({
      name: "機房A",
      host: "10.0.0.10",
      user: "root@pam",
      password: "secret",
    });

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/proxmox-config/connections");
    expect(options.method).toBe("POST");
    expect(JSON.parse(options.body).host).toBe("10.0.0.10");
  });

  test("updateConnection 走 PUT /connections/{id}", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { id: 3 }));

    await ProxmoxConfigService.updateConnection(3, { name: "lab" });

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/proxmox-config/connections/3");
    expect(options.method).toBe("PUT");
  });

  test("deleteConnection 走 DELETE /connections/{id}", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { success: true }));

    await ProxmoxConfigService.deleteConnection(3);

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/proxmox-config/connections/3");
    expect(url).not.toContain("force");
    expect(options.method).toBe("DELETE");
  });

  test("testConnectionById 走 POST /connections/{id}/test", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { success: true }));

    await ProxmoxConfigService.testConnectionById(2);

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/proxmox-config/connections/2/test");
    expect(options.method).toBe("POST");
  });

  test("syncConnection 走 POST /connections/{id}/sync", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { success: true, nodes: [] }));

    await ProxmoxConfigService.syncConnection(2);

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/proxmox-config/connections/2/sync");
    expect(options.method).toBe("POST");
  });
});
