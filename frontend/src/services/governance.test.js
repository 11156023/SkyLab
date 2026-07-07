/**
 * governance.test.js
 * 驗證 governance / ldapConfig / miningIncidents 三個 service 的 URL 與 body。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import { GovernanceService } from "./governance";
import { LdapConfigService } from "./ldapConfig";
import { MiningIncidentsService } from "./miningIncidents";

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

describe("GovernanceService", () => {
  test("updateConfig 以 PUT 送 partial 欄位", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, {}));

    await GovernanceService.updateConfig({ ttl_enabled: true, expiry_warn_days: 7 });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/governance/config");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body)).toEqual({ ttl_enabled: true, expiry_warn_days: 7 });
  });
});

describe("LdapConfigService", () => {
  test("test 以 POST 打 /test 並可帶覆寫欄位", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { ok: true, message: "ok" }));

    await LdapConfigService.test({ server_uri: "ldap://dc.example.edu" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/admin/ldap-config/test");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ server_uri: "ldap://dc.example.edu" });
  });
});

describe("MiningIncidentsService", () => {
  test("list 帶 status 與 limit 參數", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, []));

    await MiningIncidentsService.list({ status: "suspended", limit: 50 });

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/mining-incidents?");
    expect(url).toContain("status=suspended");
    expect(url).toContain("limit=50");
  });

  test("dismiss 送出 exempt 與 note", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, {}));

    await MiningIncidentsService.dismiss("inc-1", { exempt: true, note: "誤判" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/mining-incidents/inc-1/dismiss");
    expect(JSON.parse(init.body)).toEqual({ exempt: true, note: "誤判" });
  });
});
