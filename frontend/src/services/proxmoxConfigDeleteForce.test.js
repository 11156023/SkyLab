/**
 * proxmoxConfigDeleteForce.test.js
 * 驗證 deleteConnection 的 force 旗標：預設不帶 query，force=true 才加 ?force=true。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import { ProxmoxConfigService } from "./proxmoxConfig";

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

describe("ProxmoxConfigService.deleteConnection force 旗標", () => {
  test("不帶 force 時 URL 不含 force", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { success: true }));

    await ProxmoxConfigService.deleteConnection(3);

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/proxmox-config/connections/3");
    expect(url).not.toContain("force");
    expect(options.method).toBe("DELETE");
  });

  test("force=true 時加上 ?force=true", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { success: true }));

    await ProxmoxConfigService.deleteConnection(3, { force: true });

    const [url, options] = fetchMock.mock.calls[0];
    expect(url.endsWith("/api/v1/proxmox-config/connections/3?force=true")).toBe(
      true,
    );
    expect(options.method).toBe("DELETE");
  });
});
