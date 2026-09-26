/**
 * account.totp.test.js
 * 驗證帳號自助的兩步驟驗證 service：setupTotp / confirmTotp / disableTotp。
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

describe("AccountService 兩步驟驗證", () => {
  test("setupTotp() POST 到 /users/me/totp/setup 並回傳金鑰與 otpauth URI", async () => {
    const setup = {
      secret: "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567",
      otpauth_uri: "otpauth://totp/SkyLab%3Au%40x.y?secret=ABCDEFGHIJKLMNOPQRSTUVWXYZ234567",
      issuer: "SkyLab",
      account: "u@x.y",
    };
    fetchMock.mockResolvedValueOnce(jsonRes(setup));

    const result = await AccountService.setupTotp();

    expect(result).toEqual(setup);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/users/me/totp/setup");
    expect(init.method).toBe("POST");
  });

  test("confirmTotp(code) 以 JSON 送出驗證碼", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes({ totp_enabled: true }));

    const result = await AccountService.confirmTotp("123456");

    expect(result).toEqual({ totp_enabled: true });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/users/me/totp/confirm");
    expect(JSON.parse(init.body)).toEqual({ code: "123456" });
  });

  test("disableTotp(code) 以 JSON 送出驗證碼；後端拒絕時 throw", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes({ totp_enabled: false }));
    await expect(AccountService.disableTotp("654321")).resolves.toEqual({ totp_enabled: false });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/users/me/totp/disable");
    expect(JSON.parse(init.body)).toEqual({ code: "654321" });

    fetchMock.mockResolvedValueOnce(jsonRes({ detail: "驗證碼錯誤" }, 400));
    await expect(AccountService.disableTotp("000000")).rejects.toMatchObject({ status: 400 });
  });
});
