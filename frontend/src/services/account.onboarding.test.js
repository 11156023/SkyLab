/**
 * account.onboarding.test.js
 * 驗證首次登入引導精靈的完成端點：completeOnboarding()。
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

describe("AccountService 首次登入引導", () => {
  test("completeOnboarding() POST 到 /users/me/onboarding/complete 並回傳更新後的使用者", async () => {
    const me = { id: "u1", email: "u@x.y", onboarding_completed: true };
    fetchMock.mockResolvedValueOnce(jsonRes(me));

    const result = await AccountService.completeOnboarding();

    expect(result).toEqual(me);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/users/me/onboarding/complete");
    expect(init.method).toBe("POST");
  });

  test("completeOnboarding() 未登入（401）時 throw", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes({ detail: "Not authenticated" }, 401));
    await expect(AccountService.completeOnboarding()).rejects.toMatchObject({ status: 401 });
  });
});
