/**
 * gateway.test.js
 * 驗證 Gateway service 的 URL 與 method。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import { GatewayService } from "./gateway";

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

describe("GatewayService host key", () => {
  test("resetHostKey 以 POST 打 /gateway/reset-host-key", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { message: "已重設" }));

    const res = await GatewayService.resetHostKey();

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/gateway/reset-host-key");
    expect(init.method).toBe("POST");
    expect(res.message).toBe("已重設");
  });
});

describe("GatewayService 連線設定", () => {
  test("generateKeypair 以 POST 打 /gateway/generate-keypair", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, {}));

    await GatewayService.generateKeypair();

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/gateway/generate-keypair");
    expect(init.method).toBe("POST");
  });

  test("updateConfig 以 PUT 送連線欄位", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, {}));

    await GatewayService.updateConfig({ host: "10.0.0.1", ssh_port: 22, ssh_user: "root" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/gateway/config");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body)).toEqual({ host: "10.0.0.1", ssh_port: 22, ssh_user: "root" });
  });
});

describe("GatewayService WireGuard", () => {
  test("getWireGuardOverview 讀取安全摘要端點", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { live_peers: 2 }));

    const result = await GatewayService.getWireGuardOverview();

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/gateway/wireguard/overview");
    expect(init.method).toBe("GET");
    expect(result.live_peers).toBe(2);
  });
});
