import { beforeEach, describe, expect, test, vi } from "vitest";
import { AuthStorage } from "./auth";
import {
  apiGet,
  apiGetBlob,
  apiPost,
  apiPostMultipart,
  refreshTokens,
} from "./api";

function fakeStorage() {
  const values = new Map();
  return {
    getItem: (key) => (values.has(key) ? values.get(key) : null),
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  };
}

const jsonRes = (status, body = {}) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
  blob: async () => body,
});

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

let fetchMock;
let dispatched;

beforeEach(() => {
  vi.stubGlobal("localStorage", fakeStorage());

  dispatched = [];
  const target = new EventTarget();
  vi.stubGlobal("window", {
    addEventListener: (...args) => target.addEventListener(...args),
    removeEventListener: (...args) => target.removeEventListener(...args),
    dispatchEvent: (event) => {
      dispatched.push(event.type);
      return target.dispatchEvent(event);
    },
  });

  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

describe("refreshTokens", () => {
  test("成功更新 token pair", async () => {
    AuthStorage.setTokens({ access_token: "old-a", refresh_token: "old-r" });
    fetchMock.mockResolvedValueOnce(
      jsonRes(200, { access_token: "new-a", refresh_token: "new-r" }),
    );

    const result = await refreshTokens();

    expect(result.kind).toBe("refreshed");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/login/refresh-token");
    expect(JSON.parse(init.body)).toEqual({ refresh_token: "old-r" });
    expect(AuthStorage.getAccessToken()).toBe("new-a");
    expect(AuthStorage.getRefreshToken()).toBe("new-r");
  });

  test("缺少 refresh token 時回 invalid，且不發請求", async () => {
    const result = await refreshTokens();
    expect(result).toMatchObject({ kind: "invalid", reason: "missing" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("refresh 端點明確回 401 時回 invalid，但不自行清 token", async () => {
    AuthStorage.setTokens({ access_token: "old-a", refresh_token: "bad-r" });
    fetchMock.mockResolvedValueOnce(jsonRes(401, { detail: "expired" }));

    const result = await refreshTokens();

    expect(result).toMatchObject({ kind: "invalid", reason: "rejected", status: 401 });
    expect(AuthStorage.getAccessToken()).toBe("old-a");
    expect(AuthStorage.getRefreshToken()).toBe("bad-r");
  });

  test("refresh 500 屬於暫時無法連線，保留 token", async () => {
    AuthStorage.setTokens({ access_token: "old-a", refresh_token: "old-r" });
    fetchMock.mockResolvedValueOnce(jsonRes(500, { detail: "backend restarting" }));

    const result = await refreshTokens();

    expect(result).toMatchObject({
      kind: "unavailable",
      status: 500,
      message: "backend restarting",
    });
    expect(AuthStorage.getAccessToken()).toBe("old-a");
    expect(AuthStorage.getRefreshToken()).toBe("old-r");
  });

  test("refresh 網路錯誤屬於暫時無法連線，保留 token", async () => {
    AuthStorage.setTokens({ access_token: "old-a", refresh_token: "old-r" });
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    const result = await refreshTokens();

    expect(result).toMatchObject({ kind: "unavailable", status: 0 });
    expect(AuthStorage.getAccessToken()).toBe("old-a");
    expect(AuthStorage.getRefreshToken()).toBe("old-r");
  });

  test("refresh 卡住時會 timeout，且保留 token", async () => {
    vi.useFakeTimers();
    try {
      AuthStorage.setTokens({ access_token: "old-a", refresh_token: "old-r" });
      fetchMock.mockImplementationOnce((_url, init) => new Promise((_resolve, reject) => {
        init.signal.addEventListener(
          "abort",
          () => reject(new DOMException("Aborted", "AbortError")),
          { once: true },
        );
      }));

      const pending = refreshTokens();
      await vi.advanceTimersByTimeAsync(15_000);

      await expect(pending).resolves.toMatchObject({
        kind: "unavailable",
        status: 408,
      });
      expect(AuthStorage.getAccessToken()).toBe("old-a");
      expect(AuthStorage.getRefreshToken()).toBe("old-r");
    } finally {
      vi.useRealTimers();
    }
  });

  test("refresh 回傳破損 JSON 時標記為 502 且保留 token", async () => {
    AuthStorage.setTokens({ access_token: "old-a", refresh_token: "old-r" });
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => { throw new SyntaxError("invalid json"); },
    });

    await expect(refreshTokens()).resolves.toMatchObject({
      kind: "unavailable",
      status: 502,
    });
    expect(AuthStorage.getAccessToken()).toBe("old-a");
    expect(AuthStorage.getRefreshToken()).toBe("old-r");
  });

  test("refresh 缺少完整 token pair 時標記為 502 且保留原 token", async () => {
    AuthStorage.setTokens({ access_token: "old-a", refresh_token: "old-r" });
    fetchMock.mockResolvedValueOnce(jsonRes(200, { access_token: "partial-a" }));

    await expect(refreshTokens()).resolves.toMatchObject({
      kind: "unavailable",
      status: 502,
    });
    expect(AuthStorage.getAccessToken()).toBe("old-a");
    expect(AuthStorage.getRefreshToken()).toBe("old-r");
  });

  test("同一分頁同一個 refresh token 共用 single-flight 請求", async () => {
    AuthStorage.setTokens({ access_token: "old-a", refresh_token: "old-r" });
    fetchMock.mockResolvedValue(
      jsonRes(200, { access_token: "new-a", refresh_token: "new-r" }),
    );

    const [first, second] = await Promise.all([refreshTokens(), refreshTokens()]);

    expect(first.kind).toBe("refreshed");
    expect(second.kind).toBe("refreshed");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  test("logout 後晚到的 refresh 200 不會復活 token", async () => {
    AuthStorage.setTokens({ access_token: "old-a", refresh_token: "old-r" });
    const response = deferred();
    fetchMock.mockReturnValueOnce(response.promise);

    const pending = refreshTokens();
    AuthStorage.clearTokens();
    response.resolve(jsonRes(200, { access_token: "late-a", refresh_token: "late-r" }));

    await expect(pending).resolves.toMatchObject({ kind: "superseded" });
    expect(AuthStorage.getAccessToken()).toBe(null);
    expect(AuthStorage.getRefreshToken()).toBe(null);
  });

  test("新登入取代 session 後，舊 refresh 200 不會覆寫新 token", async () => {
    AuthStorage.setTokens({ access_token: "old-a", refresh_token: "old-r" });
    const response = deferred();
    fetchMock.mockReturnValueOnce(response.promise);

    const pending = refreshTokens();
    AuthStorage.setTokens({ access_token: "login-a", refresh_token: "login-r" });
    response.resolve(jsonRes(200, { access_token: "late-a", refresh_token: "late-r" }));

    await expect(pending).resolves.toMatchObject({ kind: "superseded" });
    expect(AuthStorage.getAccessToken()).toBe("login-a");
    expect(AuthStorage.getRefreshToken()).toBe("login-r");
  });

  test("舊 token version 的失效處理不會刪掉同 session 已輪替的新版本", () => {
    AuthStorage.setTokens({ access_token: "old-a", refresh_token: "old-r" });
    const oldSnapshot = AuthStorage.getSnapshot();

    expect(AuthStorage.setTokensIfCurrent(oldSnapshot, {
      access_token: "new-a",
      refresh_token: "new-r",
    })).toBe(true);
    expect(AuthStorage.clearTokensIfCurrent(oldSnapshot)).toBe(false);
    expect(AuthStorage.getAccessToken()).toBe("new-a");
    expect(AuthStorage.getRefreshToken()).toBe("new-r");
  });

  test("session 已確認失效後，晚到的 refresh 不會復活 token", () => {
    AuthStorage.setTokens({ access_token: "old-a", refresh_token: "old-r" });
    const oldSnapshot = AuthStorage.getSnapshot();

    expect(AuthStorage.clearTokensIfCurrent(oldSnapshot)).toBe(true);
    expect(AuthStorage.setTokensIfCurrent(oldSnapshot, {
      access_token: "late-a",
      refresh_token: "late-r",
    })).toBe(false);
    expect(AuthStorage.getAccessToken()).toBe(null);
    expect(AuthStorage.getRefreshToken()).toBe(null);
  });
});

describe("API 的 401 refresh 流程", () => {
  test("401 後 refresh 成功，使用新 token 重試", async () => {
    AuthStorage.setTokens({ access_token: "old-a", refresh_token: "old-r" });
    fetchMock
      .mockResolvedValueOnce(jsonRes(401))
      .mockResolvedValueOnce(
        jsonRes(200, { access_token: "new-a", refresh_token: "new-r" }),
      )
      .mockResolvedValueOnce(jsonRes(200, { id: 1 }));

    const data = await apiGet("/api/v1/users/me");

    expect(data).toEqual({ id: 1 });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[2][1].headers.Authorization).toBe("Bearer new-a");
    expect(dispatched).not.toContain("auth:unauthorized");
  });

  test("同一 session 輪替 token 後，晚到的 401 會用新 token 安全重試", async () => {
    AuthStorage.setTokens({ access_token: "old-a", refresh_token: "old-r" });
    const lateResponse = deferred();
    fetchMock
      .mockReturnValueOnce(lateResponse.promise)
      .mockResolvedValueOnce(jsonRes(401))
      .mockResolvedValueOnce(
        jsonRes(200, { access_token: "new-a", refresh_token: "new-r" }),
      )
      .mockResolvedValueOnce(jsonRes(200, { request: "refresh-owner" }))
      .mockResolvedValueOnce(jsonRes(200, { request: "late" }));

    const lateRequest = apiGet("/api/v1/late");
    await expect(apiGet("/api/v1/refresh-owner")).resolves.toEqual({
      request: "refresh-owner",
    });

    lateResponse.resolve(jsonRes(401));

    await expect(lateRequest).resolves.toEqual({ request: "late" });
    expect(fetchMock).toHaveBeenCalledTimes(5);
    expect(fetchMock.mock.calls[4][1].headers.Authorization).toBe("Bearer new-a");
    expect(dispatched).not.toContain("auth:unauthorized");
  });

  test("401 後 refresh 也回 401，才清 token 並通知登出", async () => {
    AuthStorage.setTokens({ access_token: "old-a", refresh_token: "bad-r" });
    fetchMock
      .mockResolvedValueOnce(jsonRes(401))
      .mockResolvedValueOnce(jsonRes(401));

    await expect(apiGet("/api/v1/users/me")).rejects.toMatchObject({
      status: 401,
      authExpired: true,
    });
    expect(dispatched).toContain("auth:unauthorized");
    expect(AuthStorage.getAccessToken()).toBe(null);
    expect(AuthStorage.getRefreshToken()).toBe(null);
  });

  test("未登入的 401 不會誤發登出事件", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(401));

    await expect(apiGet("/api/v1/users/me")).rejects.toMatchObject({ status: 401 });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(dispatched).not.toContain("auth:unauthorized");
  });

  test("多個請求同時確認 refresh 失效時只通知登出一次", async () => {
    AuthStorage.setTokens({ access_token: "old-a", refresh_token: "bad-r" });
    fetchMock
      .mockResolvedValueOnce(jsonRes(401))
      .mockResolvedValueOnce(jsonRes(401))
      .mockResolvedValueOnce(jsonRes(401));

    const results = await Promise.allSettled([
      apiGet("/api/v1/users/me"),
      apiGet("/api/v1/users/me"),
    ]);

    expect(results.every((result) => result.status === "rejected")).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(dispatched.filter((event) => event === "auth:unauthorized")).toHaveLength(1);
  });

  test("舊 session 的 401 不會用新登入帳號重播 POST", async () => {
    AuthStorage.setTokens({ access_token: "old-a", refresh_token: "old-r" });
    const response = deferred();
    fetchMock.mockReturnValueOnce(response.promise);

    const pending = apiPost("/api/v1/side-effect", { action: "create" });
    AuthStorage.setTokens({ access_token: "new-login-a", refresh_token: "new-login-r" });
    response.resolve(jsonRes(401));

    await expect(pending).rejects.toMatchObject({ status: 401 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(AuthStorage.getAccessToken()).toBe("new-login-a");
    expect(AuthStorage.getRefreshToken()).toBe("new-login-r");
    expect(dispatched).not.toContain("auth:unauthorized");
  });

  test("切換帳號後會忽略舊 session 晚到的成功回應", async () => {
    AuthStorage.setTokens({ access_token: "old-a", refresh_token: "old-r" });
    const response = deferred();
    fetchMock.mockReturnValueOnce(response.promise);

    const pending = apiGet("/api/v1/old-user-data");
    AuthStorage.setTokens({ access_token: "new-login-a", refresh_token: "new-login-r" });
    response.resolve(jsonRes(200, { owner: "old-user" }));

    await expect(pending).rejects.toMatchObject({
      status: 409,
      sessionChanged: true,
      unknownOutcome: true,
      retryable: false,
    });
    expect(AuthStorage.getAccessToken()).toBe("new-login-a");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  test("解析 response body 期間切換帳號，也不會交付舊 session 資料", async () => {
    AuthStorage.setTokens({ access_token: "old-a", refresh_token: "old-r" });
    const bodyStarted = deferred();
    const body = deferred();
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: () => {
        bodyStarted.resolve();
        return body.promise;
      },
    });

    const pending = apiGet("/api/v1/slow-old-user-data");
    await bodyStarted.promise;
    AuthStorage.setTokens({ access_token: "new-login-a", refresh_token: "new-login-r" });
    body.resolve({ owner: "old-user" });

    await expect(pending).rejects.toMatchObject({
      status: 409,
      sessionChanged: true,
      unknownOutcome: true,
      retryable: false,
    });
    expect(AuthStorage.getAccessToken()).toBe("new-login-a");
  });

  test("refresh 成功但重試仍 401，只重試一次後登出", async () => {
    AuthStorage.setTokens({ access_token: "old-a", refresh_token: "old-r" });
    fetchMock
      .mockResolvedValueOnce(jsonRes(401))
      .mockResolvedValueOnce(
        jsonRes(200, { access_token: "new-a", refresh_token: "new-r" }),
      )
      .mockResolvedValueOnce(jsonRes(401));

    await expect(apiGet("/api/v1/users/me")).rejects.toMatchObject({
      status: 401,
      authExpired: true,
    });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(dispatched).toContain("auth:unauthorized");
  });

  test("401 後 refresh 500 不清 token，回報可重試錯誤", async () => {
    AuthStorage.setTokens({ access_token: "old-a", refresh_token: "old-r" });
    fetchMock
      .mockResolvedValueOnce(jsonRes(401))
      .mockResolvedValueOnce(jsonRes(500, { detail: "restarting" }));

    await expect(apiGet("/api/v1/users/me")).rejects.toMatchObject({
      status: 500,
      authUnavailable: true,
      retryable: true,
    });
    expect(dispatched).not.toContain("auth:unauthorized");
    expect(AuthStorage.getAccessToken()).toBe("old-a");
    expect(AuthStorage.getRefreshToken()).toBe("old-r");
  });

  test("401 後 refresh 網路錯誤不清 token", async () => {
    AuthStorage.setTokens({ access_token: "old-a", refresh_token: "old-r" });
    fetchMock
      .mockResolvedValueOnce(jsonRes(401))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"));

    await expect(apiGet("/api/v1/users/me")).rejects.toMatchObject({
      status: 0,
      authUnavailable: true,
    });
    expect(dispatched).not.toContain("auth:unauthorized");
    expect(AuthStorage.getAccessToken()).toBe("old-a");
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

  test("Blob API 的 refresh 暫時失敗也不清 token", async () => {
    AuthStorage.setTokens({ access_token: "a", refresh_token: "r" });
    fetchMock
      .mockResolvedValueOnce(jsonRes(401))
      .mockResolvedValueOnce(jsonRes(503, { detail: "maintenance" }));

    await expect(apiGetBlob("/api/v1/report")).rejects.toMatchObject({
      status: 503,
      authUnavailable: true,
    });
    expect(AuthStorage.getAccessToken()).toBe("a");
    expect(dispatched).not.toContain("auth:unauthorized");
  });
});

describe("request cancellation and timeout", () => {
  test("caller abort 會取消請求", async () => {
    const controller = new AbortController();
    fetchMock.mockImplementationOnce((_url, init) => new Promise((_resolve, reject) => {
      init.signal.addEventListener(
        "abort",
        () => reject(new DOMException("aborted", "AbortError")),
        { once: true },
      );
    }));

    const pending = apiGet("/api/v1/slow", { signal: controller.signal });
    controller.abort();

    await expect(pending).rejects.toMatchObject({ cancelled: true });
  });

  test("request timeout 會回傳標準化錯誤", async () => {
    fetchMock.mockImplementationOnce((_url, init) => new Promise((_resolve, reject) => {
      init.signal.addEventListener(
        "abort",
        () => reject(new DOMException("aborted", "AbortError")),
        { once: true },
      );
    }));

    await expect(
      apiGet("/api/v1/slow", { timeoutMs: 5 }),
    ).rejects.toMatchObject({ status: 408, timeout: true });
  });

  test("apiPost 支援 per-request timeout", async () => {
    fetchMock.mockImplementationOnce((_url, init) => new Promise((_resolve, reject) => {
      init.signal.addEventListener(
        "abort",
        () => reject(new DOMException("aborted", "AbortError")),
        { once: true },
      );
    }));

    await expect(
      apiPost("/api/v1/slow", {}, { timeoutMs: 5 }),
    ).rejects.toMatchObject({ status: 408, timeout: true });
  });

  test("apiPostMultipart 支援 per-request timeout", async () => {
    fetchMock.mockImplementationOnce((_url, init) => new Promise((_resolve, reject) => {
      init.signal.addEventListener(
        "abort",
        () => reject(new DOMException("aborted", "AbortError")),
        { once: true },
      );
    }));

    await expect(
      apiPostMultipart("/api/v1/slow", new FormData(), { timeoutMs: 5 }),
    ).rejects.toMatchObject({ status: 408, timeout: true });
  });
});
