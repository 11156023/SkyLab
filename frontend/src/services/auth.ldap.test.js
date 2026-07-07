/**
 * auth.ldap.test.js
 * 驗證 LDAP 登入相關 service：
 *   - getLoginMethods() 取得後端啟用的登入方式（公開端點，不帶 token）
 *   - loginLdap() 以校園帳號登入，成功後儲存 tokens；失敗不觸發 401 續期流程
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import { AuthStorage, getLoginMethods, loginLdap } from "./auth";

/** 假 localStorage */
function fakeStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
  };
}

/** 模擬 fetch Response */
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

describe("getLoginMethods", () => {
  test("呼叫公開端點並回傳登入方式", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonRes(200, { password: true, google: false, ldap: true }),
    );

    const methods = await getLoginMethods();

    expect(methods).toEqual({ password: true, google: false, ldap: true });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/login/methods");
  });

  test("端點失敗時 throw { status }", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(500));

    await expect(getLoginMethods()).rejects.toMatchObject({ status: 500 });
  });
});

describe("loginLdap", () => {
  test("以 JSON body 呼叫端點，成功後儲存 tokens 並回傳", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonRes(200, { access_token: "ldap-a", refresh_token: "ldap-r" }),
    );

    const tokens = await loginLdap("s1234567", "secret");

    expect(tokens).toEqual({ access_token: "ldap-a", refresh_token: "ldap-r" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/login/ldap");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      username: "s1234567",
      password: "secret",
    });
    // 公開端點不應帶 Authorization
    expect(init.headers["Authorization"]).toBeUndefined();
    expect(AuthStorage.getAccessToken()).toBe("ldap-a");
    expect(AuthStorage.getRefreshToken()).toBe("ldap-r");
  });

  test("帳密錯誤（401）throw { status, message }，不儲存 token 也不重試", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(401, { detail: "帳號或密碼錯誤" }));

    await expect(loginLdap("s1234567", "wrong")).rejects.toMatchObject({
      status: 401,
      message: "帳號或密碼錯誤",
    });
    // 只發一次請求（不像 apiPost 會走 refresh 重試）
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(AuthStorage.getAccessToken()).toBe(null);
    expect(AuthStorage.getRefreshToken()).toBe(null);
  });
});
