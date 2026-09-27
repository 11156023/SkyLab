/**
 * account.preflight.test.js
 * 驗證登入後服務檢查的端點：preflight()。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import { AccountService } from "./account";

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

describe("AccountService 登入服務檢查", () => {
  test("preflight() GET /users/me/preflight 並回傳檢查結果", async () => {
    const body = { ok: true, detailed: false, checks: [{ key: "database", status: "ok" }] };
    fetchMock.mockResolvedValueOnce(jsonRes(body));

    const result = await AccountService.preflight();

    expect(result).toEqual(body);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/api\/v1\/users\/me\/preflight$/);
    expect(init?.method ?? "GET").toBe("GET");
  });

  test("preflight({ refresh: true }) 帶 refresh=true", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes({ ok: true, detailed: true, checks: [] }));

    await AccountService.preflight({ refresh: true });

    expect(fetchMock.mock.calls[0][0]).toContain("/api/v1/users/me/preflight?refresh=true");
  });

  test("preflight() 後端錯誤時 throw { status }", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes({ detail: "Not Found" }, 404));
    await expect(AccountService.preflight()).rejects.toMatchObject({ status: 404 });
  });
});
