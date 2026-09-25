/**
 * users.totp.test.js
 * 驗證管理員重設使用者兩步驟驗證的 service。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import { UsersService } from "./users";

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

describe("UsersService.resetTotp", () => {
  test("DELETE /users/{id}/totp 並回傳狀態", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes({ totp_enabled: false }));

    const result = await UsersService.resetTotp("u-1");

    expect(result).toEqual({ totp_enabled: false });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/users/u-1/totp");
    expect(init.method).toBe("DELETE");
  });

  test("對方未啟用時後端回 400 → throw", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes({ detail: "尚未啟用" }, 400));

    await expect(UsersService.resetTotp("u-2")).rejects.toMatchObject({ status: 400 });
  });
});
