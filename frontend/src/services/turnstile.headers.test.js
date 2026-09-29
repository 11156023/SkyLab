/**
 * turnstile.headers.test.js
 * Cloudflare Turnstile 機器人驗證 token 走 X-Turnstile-Token 標頭：
 * 密碼登入（apiPostForm）、LDAP 登入（loginLdap）、註冊（AccountService.signup）
 * 有 token 才帶，沒有 token（後端未啟用）時完全不帶，請求其餘部分不變。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import { apiPostForm } from "./api";
import { AccountService } from "./account";
import { TURNSTILE_HEADER, loginLdap, turnstileHeaders } from "./auth";

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

describe("turnstileHeaders", () => {
  test("有 token 才產生標頭", () => {
    expect(TURNSTILE_HEADER).toBe("X-Turnstile-Token");
    expect(turnstileHeaders("tok")).toEqual({ "X-Turnstile-Token": "tok" });
    expect(turnstileHeaders("")).toEqual({});
    expect(turnstileHeaders(undefined)).toEqual({});
  });
});

describe("密碼登入 apiPostForm", () => {
  test("額外標頭與 form 內容類型並存", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes({ access_token: "a" }));

    await apiPostForm(
      "/api/v1/login/access-token",
      { username: "u", password: "p" },
      { headers: turnstileHeaders("tok") },
    );

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/login/access-token");
    expect(init.headers["Content-Type"]).toBe("application/x-www-form-urlencoded");
    expect(init.headers["X-Turnstile-Token"]).toBe("tok");
    expect(init.body).toBe("username=u&password=p");
  });

  test("沒有 token 時不帶標頭", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes({ access_token: "a" }));

    await apiPostForm("/api/v1/login/access-token", { username: "u", password: "p" });

    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers).not.toHaveProperty("X-Turnstile-Token");
  });
});

describe("LDAP 登入 loginLdap", () => {
  test("帶 turnstileToken 時加標頭，body 不變", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes({ access_token: "a", refresh_token: "r" }));

    await loginLdap("s1234567", "secret", { turnstileToken: "tok" });

    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers["X-Turnstile-Token"]).toBe("tok");
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body)).toEqual({ username: "s1234567", password: "secret" });
  });

  test("不給 options 時不帶標頭", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes({ access_token: "a", refresh_token: "r" }));

    await loginLdap("s1234567", "secret");

    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers).not.toHaveProperty("X-Turnstile-Token");
  });
});

describe("註冊 AccountService.signup", () => {
  test("帶 turnstileToken 時加標頭，body 不變", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes({ id: "u1" }));

    await AccountService.signup(
      { email: "u@x.y", full_name: "User", password: "pw123456" },
      { turnstileToken: "tok" },
    );

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/users/signup");
    expect(init.headers["X-Turnstile-Token"]).toBe("tok");
    expect(JSON.parse(init.body)).toEqual({
      email: "u@x.y",
      full_name: "User",
      password: "pw123456",
    });
  });

  test("不給 options 時不帶標頭", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes({ id: "u1" }));

    await AccountService.signup({ email: "u@x.y", full_name: "User", password: "pw123456" });

    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers).not.toHaveProperty("X-Turnstile-Token");
  });
});
