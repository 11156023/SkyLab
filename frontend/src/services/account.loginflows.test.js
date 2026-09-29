/**
 * account.loginflows.test.js
 * 驗證登入頁用到的帳號端點：requestPasswordRecovery / resetPassword / signup /
 * approveDesktopDevice（URL、method 與 body 必須和後端路由一致）。
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

describe("AccountService 登入頁流程", () => {
  test("requestPasswordRecovery(email) POST 到 /password-recovery/{email}，email 會編碼", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes({ message: "ok" }));

    await expect(AccountService.requestPasswordRecovery("a+b@x.y")).resolves.toEqual({
      message: "ok",
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/password-recovery/a%2Bb%40x.y");
    expect(init.method).toBe("POST");
    expect(init.body).toBe("null");
  });

  test("resetPassword(token, newPassword) 送出 new_password 與 token", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes({ message: "ok" }));

    await AccountService.resetPassword("tok-123", "N3w-Passw0rd!");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/reset-password/");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ new_password: "N3w-Passw0rd!", token: "tok-123" });
  });

  test("signup() 送出 email、full_name、password；後端拒絕時 throw", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes({ id: "u1", email: "u@x.y" }));

    await expect(
      AccountService.signup({ email: "u@x.y", full_name: "User", password: "pw123456" }),
    ).resolves.toEqual({ id: "u1", email: "u@x.y" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/users/signup");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      email: "u@x.y",
      full_name: "User",
      password: "pw123456",
    });

    fetchMock.mockResolvedValueOnce(jsonRes({ detail: "已存在" }, 400));
    await expect(
      AccountService.signup({ email: "u@x.y", full_name: "User", password: "pw123456" }),
    ).rejects.toMatchObject({ status: 400 });
  });

  test("approveDesktopDevice(deviceCode) 送出 device_code", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes({ status: "approved" }));

    await AccountService.approveDesktopDevice("ABCD-1234");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/desktop-client/auth/approve");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ device_code: "ABCD-1234" });
  });
});
