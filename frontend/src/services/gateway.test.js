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

describe("GatewayService one-click install", () => {
  test("getInstallStatus 以 GET 打 /gateway/install", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { state: "idle", root_access: true }));

    const res = await GatewayService.getInstallStatus();

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/gateway/install");
    expect(init.method).toBe("GET");
    expect(res.state).toBe("idle");
  });

  test("startInstall 以 POST 帶安裝參數打 /gateway/install", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(202, { state: "running" }));
    const options = {
      ingress_interface: "ens18",
      vm_interface: "ens19",
      snat_address: "10.10.0.2",
      listen_port: 51821,
      forward_port_start: 30000,
      forward_port_end: 39999,
      monitoring_allow_from: ["192.168.100.20"],
    };

    const res = await GatewayService.startInstall(options);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/gateway/install");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual(options);
    expect(res.state).toBe("running");
  });
});

describe("GatewayService 平台入口", () => {
  const entry = {
    enabled: true,
    domain: "skylab.example.com",
    upstream_host: "192.168.100.20",
    upstream_port: 8082,
    enable_https: true,
  };

  test("getPlatformEntry 以 GET 打 /gateway/platform-entry", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { ...entry, gateway_ready: true }));

    const res = await GatewayService.getPlatformEntry();

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/api\/v1\/gateway\/platform-entry$/);
    expect(init.method).toBe("GET");
    expect(res.gateway_ready).toBe(true);
  });

  test("updatePlatformEntry 以 PUT 送完整設定", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, entry));

    await GatewayService.updatePlatformEntry(entry);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/api\/v1\/gateway\/platform-entry$/);
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body)).toEqual(entry);
  });

  test("testPlatformEntryUpstream 以 POST 送上游位址", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { reachable: true, detail: "ok" }));

    const res = await GatewayService.testPlatformEntryUpstream({
      upstream_host: "192.168.100.20",
      upstream_port: 8082,
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/gateway/platform-entry/test-upstream");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ upstream_host: "192.168.100.20", upstream_port: 8082 });
    expect(res.reachable).toBe(true);
  });

  test("getPlatformEntryStatus 以 GET 打 status 端點", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { applied: true }));

    const res = await GatewayService.getPlatformEntryStatus();

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/gateway/platform-entry/status");
    expect(init.method).toBe("GET");
    expect(res.applied).toBe(true);
  });
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

describe("GatewayService nginx", () => {
  test("readServiceConfig 讀 nginx 的設定檔端點", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { service: "nginx", content: "" }));

    await GatewayService.readServiceConfig("nginx");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/gateway/services/nginx/config");
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
