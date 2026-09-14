/**
 * api.js
 * 統一的 API 請求入口。
 * - 自動帶入 Authorization header
 * - 401 時自動用 refresh token 續期並重試一次，只有憑證明確失效才強制登出
 * - 統一錯誤格式：失敗時 throw { status, message }
 *
 * 使用方式：
 *   import { apiGet, apiPost } from "@/services/api";
 *   const user = await apiGet("/api/v1/users/me");
 */

import { AuthStorage } from "./auth";
import i18n from "../i18n";

const BASE_URL = import.meta.env.VITE_API_URL ?? "";
const REFRESH_PATH = "/api/v1/login/refresh-token";
const DEFAULT_REQUEST_TIMEOUT_MS = 15_000;
const MAX_AUTH_RETRIES = 1;

/** 進行中的 refresh 請求；同一個 refresh token 的多個 401 共用一次請求。 */
let refreshPromise = null;

/**
 * 用 refresh token 換一組新的 access + refresh token。
 * 暫時性錯誤不會清除 token；呼叫端只有在 kind=invalid 時才可登出。
 * @returns {Promise<{kind: "refreshed"|"invalid"|"unavailable"|"superseded", [key: string]: any}>}
 */
export function refreshTokens() {
  const snapshot = AuthStorage.getSnapshot();
  if (!snapshot.refreshToken) {
    return Promise.resolve({ kind: "invalid", reason: "missing", snapshot });
  }

  if (
    refreshPromise?.sessionId === snapshot.sessionId
    && refreshPromise.refreshToken === snapshot.refreshToken
  ) {
    return refreshPromise.promise;
  }

  const promise = doRefresh(snapshot).finally(() => {
    if (refreshPromise?.promise === promise) refreshPromise = null;
  });
  refreshPromise = {
    sessionId: snapshot.sessionId,
    refreshToken: snapshot.refreshToken,
    promise,
  };
  return promise;
}

async function doRefresh(snapshot) {
  let res;
  try {
    res = await fetchWithTimeout(
      `${BASE_URL}${REFRESH_PATH}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: snapshot.refreshToken }),
      },
    );
  } catch (error) {
    if (!AuthStorage.matchesSnapshot(snapshot)) {
      return { kind: "superseded", snapshot };
    }
    return {
      kind: "unavailable",
      status: error?.status ?? 0,
      message: error?.message,
      error,
      snapshot,
    };
  }

  if (!AuthStorage.matchesSnapshot(snapshot)) {
    return { kind: "superseded", snapshot };
  }
  if (res.status === 401) {
    return { kind: "invalid", reason: "rejected", status: 401, snapshot };
  }
  if (!res.ok) {
    return {
      kind: "unavailable",
      status: res.status,
      message: await readResponseMessage(res),
      snapshot,
    };
  }

  let tokens;
  try {
    tokens = await res.json();
  } catch (error) {
    return {
      kind: "unavailable",
      status: 502,
      message: "Refresh response was not valid JSON",
      error,
      snapshot,
    };
  }
  if (!tokens?.access_token || !tokens?.refresh_token) {
    return {
      kind: "unavailable",
      status: 502,
      message: "Refresh response did not contain a complete token pair",
      snapshot,
    };
  }

  try {
    if (!AuthStorage.setTokensIfCurrent(snapshot, tokens)) {
      return { kind: "superseded", snapshot };
    }
  } catch (error) {
    return { kind: "unavailable", status: 0, error, snapshot };
  }
  return { kind: "refreshed", snapshot };
}

/** 建立共用 headers（每次重建，重試時才會帶到新 token） */
function buildHeaders(extra = {}, isFormData = false, accessToken = AuthStorage.getAccessToken()) {
  // FormData 由瀏覽器自動帶 multipart boundary，不能手動設 Content-Type
  const headers = isFormData
    ? { ...extra }
    : { "Content-Type": "application/json", ...extra };
  if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;
  // 讓後端依目前介面語言回傳對應語系的錯誤訊息
  headers["Accept-Language"] = i18n.language ?? "zh-TW";
  return headers;
}

async function readResponseMessage(res) {
  let message = `HTTP ${res.status}`;
  try {
    const body = await res.json();
    const rawMessage = body?.detail ?? body?.message;
    if (typeof rawMessage === "string") {
      message = rawMessage;
    } else if (rawMessage && typeof rawMessage.message === "string") {
      message = rawMessage.message;
    }
  } catch {
    // 若 body 不是 JSON 就用預設訊息
  }
  return message;
}

function invalidateCurrentSession(snapshot) {
  if (!snapshot?.accessToken && !snapshot?.refreshToken) return false;
  if (!AuthStorage.clearTokensIfCurrent(snapshot)) return false;
  window.dispatchEvent(new Event("auth:unauthorized"));
  return true;
}

function authRecoveryError(outcome) {
  return {
    status: outcome.status ?? 0,
    message: outcome.message ?? i18n.t("api.authRecoveryMessage", { ns: "services" }),
    authUnavailable: true,
    retryable: true,
  };
}

function assertResponseSession(snapshot) {
  if (
    snapshot.accessToken
    && (!AuthStorage.isSameSession(snapshot) || !AuthStorage.isLoggedIn())
  ) {
    throw {
      status: 409,
      message: i18n.t("api.sessionChangedIgnored", { ns: "services" }),
      sessionChanged: true,
      unknownOutcome: true,
      retryable: false,
    };
  }
}

async function recoverUnauthorized({ requestSnapshot, authRetryCount, retry }) {
  if (!AuthStorage.matchesSnapshot(requestSnapshot)) {
    // 同一 session 的另一請求可能剛完成 token 輪替；可安全用新 token 重試。
    if (
      authRetryCount < MAX_AUTH_RETRIES
      && AuthStorage.isSameSession(requestSnapshot)
      && AuthStorage.isLoggedIn()
    ) {
      return { recovered: true, value: await retry() };
    }
    // session generation 不同代表已登出或切換帳號，不可重播舊 POST/DELETE。
    return { recovered: false, authExpired: false };
  }

  if (authRetryCount < MAX_AUTH_RETRIES) {
    const outcome = await refreshTokens();
    if (outcome.kind === "refreshed") {
      if (AuthStorage.isSameSession(requestSnapshot) && AuthStorage.isLoggedIn()) {
        return { recovered: true, value: await retry() };
      }
      return { recovered: false, authExpired: false };
    }
    if (outcome.kind === "superseded") {
      if (AuthStorage.isSameSession(requestSnapshot) && AuthStorage.isLoggedIn()) {
        return { recovered: true, value: await retry() };
      }
      return { recovered: false, authExpired: false };
    }
    if (outcome.kind === "unavailable") throw authRecoveryError(outcome);

    if (!invalidateCurrentSession(outcome.snapshot)) {
      if (AuthStorage.isSameSession(requestSnapshot) && AuthStorage.isLoggedIn()) {
        return { recovered: true, value: await retry() };
      }
      return { recovered: false, authExpired: false };
    }
    return { recovered: false, authExpired: true };
  }

  return {
    recovered: false,
    authExpired: invalidateCurrentSession(requestSnapshot),
  };
}

async function fetchWithTimeout(url, init, timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS) {
  const controller = new AbortController();
  const upstreamSignal = init.signal;
  let timedOut = false;

  const abortFromUpstream = () => controller.abort(upstreamSignal?.reason);
  if (upstreamSignal?.aborted) abortFromUpstream();
  else upstreamSignal?.addEventListener("abort", abortFromUpstream, { once: true });

  const timeoutId = timeoutMs > 0
    ? setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, timeoutMs)
    : null;

  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } catch (error) {
    if (controller.signal.aborted) {
      if (timedOut) {
        throw { status: 408, message: "Request timed out", timeout: true };
      }
      throw { status: 0, message: "Request cancelled", cancelled: true };
    }
    throw error;
  } finally {
    if (timeoutId !== null) clearTimeout(timeoutId);
    upstreamSignal?.removeEventListener("abort", abortFromUpstream);
  }
}

/** 統一處理 response；401 時先嘗試續期再重試一次 */
async function request(path, init, authRetryCount = 0) {
  const { timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS, ...fetchInit } = init;
  const requestSnapshot = AuthStorage.getSnapshot();
  const res = await fetchWithTimeout(
    `${BASE_URL}${path}`,
    {
      ...fetchInit,
      headers: buildHeaders(
        fetchInit.headers,
        fetchInit.body instanceof FormData,
        requestSnapshot.accessToken,
      ),
    },
    timeoutMs,
  );

  if (res.ok) {
    assertResponseSession(requestSnapshot);
    // 204 No Content 不會有 body
    if (res.status === 204) return null;
    const body = await res.json();
    assertResponseSession(requestSnapshot);
    return body;
  }

  let authExpired = false;
  if (res.status === 401) {
    const recovery = await recoverUnauthorized({
      requestSnapshot,
      authRetryCount,
      retry: () => request(path, init, authRetryCount + 1),
    });
    if (recovery.recovered) return recovery.value;
    authExpired = recovery.authExpired;
  }

  throw {
    status: res.status,
    message: await readResponseMessage(res),
    ...(authExpired ? { authExpired: true } : {}),
  };
}

/** GET */
export function apiGet(path, options = {}) {
  return request(path, {
    method: "GET",
    signal: options.signal,
    timeoutMs: options.timeoutMs,
  });
}

async function requestBlob(path, init, authRetryCount = 0) {
  const requestSnapshot = AuthStorage.getSnapshot();
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: buildHeaders(
      init.headers,
      init.body instanceof FormData,
      requestSnapshot.accessToken,
    ),
  });
  if (res.ok) {
    assertResponseSession(requestSnapshot);
    const blob = await res.blob();
    assertResponseSession(requestSnapshot);
    return blob;
  }

  let authExpired = false;
  if (res.status === 401) {
    const recovery = await recoverUnauthorized({
      requestSnapshot,
      authRetryCount,
      retry: () => requestBlob(path, init, authRetryCount + 1),
    });
    if (recovery.recovered) return recovery.value;
    authExpired = recovery.authExpired;
  }

  throw {
    status: res.status,
    message: await readResponseMessage(res),
    ...(authExpired ? { authExpired: true } : {}),
  };
}

/** GET（回傳 Blob，檔案下載用；同樣支援 401 續期重試） */
export function apiGetBlob(path) {
  return requestBlob(path, { method: "GET" });
}

/** POST（JSON body，回傳 Blob，報表匯出用；同樣支援 401 續期重試） */
export function apiPostBlob(path, body) {
  return requestBlob(path, { method: "POST", body: JSON.stringify(body) });
}

/** 觸發瀏覽器下載 Blob */
export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

/** POST（JSON body） */
export function apiPost(path, body, options = {}) {
  return request(path, {
    method: "POST",
    body: JSON.stringify(body),
    signal: options.signal,
    timeoutMs: options.timeoutMs,
  });
}

/** POST（form-urlencoded，登入用，不帶 Authorization 也不重試） */
export async function apiPostForm(path, params) {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      "Accept-Language": i18n.language ?? "zh-TW",
    },
    body: new URLSearchParams(params).toString(),
  });
  if (res.ok) return res.status === 204 ? null : res.json();

  let message = `HTTP ${res.status}`;
  try {
    const body = await res.json();
    message = body?.detail ?? body?.message ?? message;
  } catch {
    // 若 body 不是 JSON 就用預設訊息
  }
  throw { status: res.status, message };
}

/** POST（multipart/form-data，檔案上傳用；formData 為 FormData 實例） */
export function apiPostMultipart(path, formData, options = {}) {
  return request(path, {
    method: "POST",
    body: formData,
    signal: options.signal,
    timeoutMs: options.timeoutMs,
  });
}

/** PATCH */
export function apiPatch(path, body) {
  return request(path, { method: "PATCH", body: JSON.stringify(body) });
}

/** DELETE（無 body） */
export function apiDelete(path) {
  return request(path, { method: "DELETE" });
}

/** DELETE（帶 JSON body，用於需要傳送條件的刪除） */
export function apiDeleteJson(path, body) {
  return request(path, { method: "DELETE", body: JSON.stringify(body) });
}

/** PUT */
export function apiPut(path, body) {
  return request(path, { method: "PUT", body: JSON.stringify(body) });
}
