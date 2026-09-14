/**
 * firewall.services.test.js
 * 驗證單台 VM 的迷你拓撲／對外服務端點，以及反向代理的網域可用性檢查。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import {
  getVmTopology,
  listPublishedServices,
  publishService,
  replacePublishedService,
  unpublishService,
} from "./firewall";
import { ReverseProxyService } from "./reverseProxy";

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

function lastCall() {
  const [url, init] = fetchMock.mock.calls.at(-1);
  return { url, init, body: init?.body ? JSON.parse(init.body) : undefined };
}

describe("firewall 單台 VM 端點", () => {
  test("getVmTopology 與 listPublishedServices 的路徑", async () => {
    fetchMock.mockResolvedValue(jsonRes(200, { nodes: [], edges: [] }));
    await getVmTopology(105);
    expect(lastCall().url).toContain("/api/v1/firewall/105/topology");
    await listPublishedServices(105);
    expect(lastCall().url).toContain("/api/v1/firewall/105/services");
  });

  test("publishService 送出 mode 與網域", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, {}));
    await publishService(105, { port: 80, mode: "domain", domain: "app.example.com", enable_https: true });
    const { url, init, body } = lastCall();
    expect(url).toContain("/api/v1/firewall/105/services");
    expect(init.method).toBe("POST");
    expect(body).toEqual({ port: 80, mode: "domain", domain: "app.example.com", enable_https: true });
  });

  test("replacePublishedService 包成 current + replacement", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, {}));
    await replacePublishedService(105, { port: 80, protocol: "tcp" }, { port: 80, mode: "port_forward", external_port: 8080 });
    const { init, body } = lastCall();
    expect(init.method).toBe("PUT");
    expect(body).toEqual({
      current: { port: 80, protocol: "tcp" },
      replacement: { port: 80, mode: "port_forward", external_port: 8080 },
    });
  });

  test("unpublishService 用 DELETE 帶 port/protocol，protocol 預設 tcp", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, {}));
    await unpublishService(105, { port: 22 });
    const { init, body } = lastCall();
    expect(init.method).toBe("DELETE");
    expect(body).toEqual({ port: 22, protocol: "tcp" });
  });
});

describe("ReverseProxyService.checkDomainAvailability", () => {
  test("把網域放進 query，並可排除自己的規則", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { available: true }));
    await ReverseProxyService.checkDomainAvailability("app.example.com", "rule-1");
    const { url } = lastCall();
    expect(url).toContain("/api/v1/reverse-proxy/domain-availability?");
    expect(url).toContain("domain=app.example.com");
    expect(url).toContain("exclude_rule_id=rule-1");
  });
});
