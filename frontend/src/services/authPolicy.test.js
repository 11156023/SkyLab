/**
 * authPolicy.test.js
 * 驗證登入安全政策 service：讀取政策、管理員更新強制 2FA 開關。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import { AuthPolicyService } from "./authPolicy";

function fakeStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
  };
}

const jsonRes = (body, status = 200) => ({
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

describe("AuthPolicyService", () => {
  test("get() 讀取 /auth-policy", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes({ totp_required: true, updated_at: null }));

    const policy = await AuthPolicyService.get();

    expect(policy).toEqual({ totp_required: true, updated_at: null });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/auth-policy");
    expect(init?.method ?? "GET").toBe("GET");
  });

  test("update() 以 PUT 送出 totp_required 到管理員端點", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes({ totp_required: false, updated_at: null }));

    const policy = await AuthPolicyService.update({ totp_required: false });

    expect(policy.totp_required).toBe(false);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/admin/auth-policy");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body)).toEqual({ totp_required: false });
  });
});
