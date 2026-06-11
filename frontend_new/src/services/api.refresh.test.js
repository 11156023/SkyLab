/**
 * api.refresh.test.js
 * 驗證 token 自動續期：
 *   - refreshTokens() 用 refresh token 換新 token（single-flight）
 *   - API 收到 401 時先 refresh 再重試，失敗才發 auth:unauthorized
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import { AuthStorage } from "./auth";
import { apiGet, refreshTokens } from "./api";

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
let dispatched;

beforeEach(() => {
  vi.stubGlobal("localStorage", fakeStorage());

  dispatched = [];
  const target = new EventTarget();
  vi.stubGlobal("window", {
    addEventListener: (...a) => target.addEventListener(...a),
    removeEventListener: (...a) => target.removeEventListener(...a),
    dispatchEvent: (e) => {
      dispatched.push(e.type);
      return target.dispatchEvent(e);
    },
  });

  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

describe("refreshTokens", () => {
  test("用 refresh token 呼叫端點並儲存新 token，回傳 true", async () => {
    AuthStorage.setTokens({ access_token: "old-a", refresh_token: "old-r" });
    fetchMock.mockResolvedValueOnce(
      jsonRes(200, { access_token: "new-a", refresh_token: "new-r" }),
    );

    const ok = await refreshTokens();

    expect(ok).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/login/refresh-token");
    expect(JSON.parse(init.body)).toEqual({ refresh_token: "old-r" });
    expect(AuthStorage.getAccessToken()).toBe("new-a");
    expect(AuthStorage.getRefreshToken()).toBe("new-r");
  });

  test("沒有 refresh token 時回傳 false 且不發請求", async () => {
    const ok = await refreshTokens();
    expect(ok).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("refresh 端點回 401 時回傳 false", async () => {
    AuthStorage.setTokens({ access_token: "old-a", refresh_token: "bad-r" });
    fetchMock.mockResolvedValueOnce(jsonRes(401, { detail: "expired" }));

    const ok = await refreshTokens();
    expect(ok).toBe(false);
  });

  test("併發呼叫共用同一次 refresh 請求", async () => {
    AuthStorage.setTokens({ access_token: "old-a", refresh_token: "old-r" });
    fetchMock.mockResolvedValue(
      jsonRes(200, { access_token: "new-a", refresh_token: "new-r" }),
    );

    const [a, b] = await Promise.all([refreshTokens(), refreshTokens()]);

    expect(a).toBe(true);
    expect(b).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("apiGet 的 401 自動續期", () => {
  test("401 → refresh 成功 → 用新 token 重試成功", async () => {
    AuthStorage.setTokens({ access_token: "old-a", refresh_token: "old-r" });
    fetchMock
      .mockResolvedValueOnce(jsonRes(401)) // 原請求
      .mockResolvedValueOnce(
        jsonRes(200, { access_token: "new-a", refresh_token: "new-r" }),
      ) // refresh
      .mockResolvedValueOnce(jsonRes(200, { id: 1 })); // 重試

    const data = await apiGet("/api/v1/users/me");

    expect(data).toEqual({ id: 1 });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    // 重試需帶新 token
    const retryInit = fetchMock.mock.calls[2][1];
    expect(retryInit.headers["Authorization"]).toBe("Bearer new-a");
    // 成功續期不應觸發登出
    expect(dispatched).not.toContain("auth:unauthorized");
  });

  test("401 → refresh 失敗 → 清除 token、發出 auth:unauthorized、throw 401", async () => {
    AuthStorage.setTokens({ access_token: "old-a", refresh_token: "bad-r" });
    fetchMock
      .mockResolvedValueOnce(jsonRes(401)) // 原請求
      .mockResolvedValueOnce(jsonRes(401)); // refresh 失敗

    await expect(apiGet("/api/v1/users/me")).rejects.toMatchObject({
      status: 401,
    });
    expect(dispatched).toContain("auth:unauthorized");
    expect(AuthStorage.getAccessToken()).toBe(null);
    expect(AuthStorage.getRefreshToken()).toBe(null);
  });

  test("重試後仍 401 → 只 refresh 一次，不無限循環", async () => {
    AuthStorage.setTokens({ access_token: "old-a", refresh_token: "old-r" });
    fetchMock
      .mockResolvedValueOnce(jsonRes(401)) // 原請求
      .mockResolvedValueOnce(
        jsonRes(200, { access_token: "new-a", refresh_token: "new-r" }),
      ) // refresh 成功
      .mockResolvedValueOnce(jsonRes(401)); // 重試仍 401

    await expect(apiGet("/api/v1/users/me")).rejects.toMatchObject({
      status: 401,
    });
    // 原請求 + refresh + 重試 = 3 次，不會有第二次 refresh
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(dispatched).toContain("auth:unauthorized");
  });

  test("非 401 錯誤不觸發 refresh", async () => {
    AuthStorage.setTokens({ access_token: "a", refresh_token: "r" });
    fetchMock.mockResolvedValueOnce(jsonRes(500, { detail: "boom" }));

    await expect(apiGet("/api/v1/users/me")).rejects.toMatchObject({
      status: 500,
      message: "boom",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(dispatched).not.toContain("auth:unauthorized");
  });
});
