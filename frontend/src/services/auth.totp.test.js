/**
 * auth.totp.test.js
 * 驗證兩步驟驗證的登入 service：
 *   - loginLdap() 收到挑戰回應（totp_required）時不儲存 tokens、原樣回傳
 *   - loginTotp() 以挑戰 token + 驗證碼換取正式 tokens 並儲存
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import { AuthStorage, loginLdap, loginTotp } from "./auth";

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

describe("loginLdap 遇到兩步驟驗證挑戰", () => {
  test("回傳挑戰物件且不寫入 tokens", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonRes(200, { totp_required: true, totp_token: "challenge-jwt" }),
    );

    const result = await loginLdap("s1234", "pw");

    expect(result).toEqual({ totp_required: true, totp_token: "challenge-jwt" });
    expect(AuthStorage.isLoggedIn()).toBe(false);
  });
});

describe("loginTotp", () => {
  test("以 JSON body 送出挑戰 token 與驗證碼，成功後儲存 tokens", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonRes(200, { access_token: "a", refresh_token: "r" }),
    );

    const tokens = await loginTotp("challenge-jwt", "123456");

    expect(tokens).toEqual({ access_token: "a", refresh_token: "r" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/login/totp");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ totp_token: "challenge-jwt", code: "123456" });
    expect(AuthStorage.isLoggedIn()).toBe(true);
    expect(AuthStorage.getSnapshot().accessToken).toBe("a");
  });

  test("驗證碼錯誤時 throw { status, message }，不寫入 tokens", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(400, { detail: "驗證碼錯誤" }));

    await expect(loginTotp("challenge-jwt", "000000")).rejects.toMatchObject({
      status: 400,
      message: "驗證碼錯誤",
    });
    expect(AuthStorage.isLoggedIn()).toBe(false);
  });

  test("挑戰 token 逾時回 401 → throw { status: 401 }，且不會嘗試 refresh（只打一次）", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(401, { detail: "expired" }));

    await expect(loginTotp("stale", "123456")).rejects.toMatchObject({ status: 401 });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(AuthStorage.isLoggedIn()).toBe(false);
  });
});
