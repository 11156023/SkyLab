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
import {
  BLOB_REQUEST_TIMEOUT_MS,
  DEFAULT_REQUEST_TIMEOUT_MS,
  LOGIN_REQUEST_TIMEOUT_MS,
  fetchWithTimeout,
} from "./fetchWithTimeout";
import { readResponseMessage } from "./responseMessage";

const BASE_URL = import.meta.env.VITE_API_URL ?? "";
const REFRESH_PATH = "/api/v1/login/refresh-token";
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
  // 只有發出請求的 session 仍登入時才重試；session generation 不同代表已登出
  // 或切換帳號，不可重播舊 POST/DELETE。
  const retryIfSameSession = async () => (
    AuthStorage.isSameSession(requestSnapshot) && AuthStorage.isLoggedIn()
      ? { recovered: true, value: await retry() }
      : { recovered: false, authExpired: false }
  );

  if (!AuthStorage.matchesSnapshot(requestSnapshot)) {
    // 同一 session 的另一請求可能剛完成 token 輪替；可安全用新 token 重試。
    if (authRetryCount < MAX_AUTH_RETRIES) return retryIfSameSession();
    return { recovered: false, authExpired: false };
  }

  if (authRetryCount < MAX_AUTH_RETRIES) {
    const outcome = await refreshTokens();
    if (outcome.kind === "refreshed" || outcome.kind === "superseded") {
      return retryIfSameSession();
    }
    if (outcome.kind === "unavailable") throw authRecoveryError(outcome);

    // invalid：清掉失效的 token；沒清到代表別處已換上新 token，照樣可以重試。
    if (!invalidateCurrentSession(outcome.snapshot)) return retryIfSameSession();
    return { recovered: false, authExpired: true };
  }

  return {
    recovered: false,
    authExpired: invalidateCurrentSession(requestSnapshot),
  };
}

/** 一般 JSON 回應（204 No Content 不會有 body） */
const JSON_RESPONSE = {
  defaultTimeoutMs: DEFAULT_REQUEST_TIMEOUT_MS,
  readBody: (res) => (res.status === 204 ? null : res.json()),
};

/** 檔案下載：回傳 Blob，逾時放寬（匯出卡住時仍會拋 408，按鈕不會永遠停在「匯出中」） */
const BLOB_RESPONSE = {
  defaultTimeoutMs: BLOB_REQUEST_TIMEOUT_MS,
  readBody: (res) => res.blob(),
};

/**
 * 統一處理 response；401 時先嘗試續期再重試一次。
 * responseKind 決定預設逾時與成功時怎麼讀 body（JSON_RESPONSE／BLOB_RESPONSE）。
 */
async function send(path, init, responseKind, authRetryCount = 0) {
  const { timeoutMs = responseKind.defaultTimeoutMs, ...fetchInit } = init;
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
    const body = await responseKind.readBody(res);
    assertResponseSession(requestSnapshot);
    return body;
  }

  let authExpired = false;
  if (res.status === 401) {
    const recovery = await recoverUnauthorized({
      requestSnapshot,
      authRetryCount,
      retry: () => send(path, init, responseKind, authRetryCount + 1),
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

function request(path, init) {
  return send(path, init, JSON_RESPONSE);
}

function requestBlob(path, init) {
  return send(path, init, BLOB_RESPONSE);
}

/** GET */
export function apiGet(path, options = {}) {
  return request(path, {
    method: "GET",
    signal: options.signal,
    timeoutMs: options.timeoutMs,
  });
}

/** GET（回傳 Blob，檔案下載用；同樣支援 401 續期重試） */
export function apiGetBlob(path) {
  return requestBlob(path, { method: "GET" });
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

/** POST（JSON body）；options.headers 可加額外標頭（例如註冊的機器人驗證 token） */
export function apiPost(path, body, options = {}) {
  return request(path, {
    method: "POST",
    body: JSON.stringify(body),
    headers: options.headers,
    signal: options.signal,
    timeoutMs: options.timeoutMs,
  });
}

/** POST（form-urlencoded，登入用，不帶 Authorization 也不重試）
 *  走 fetchWithTimeout：後端沒回應時登入按鈕會收到 408，不會一直轉圈。 */
export async function apiPostForm(path, params, options = {}) {
  const res = await fetchWithTimeout(
    `${BASE_URL}${path}`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept-Language": i18n.language ?? "zh-TW",
        ...options.headers,
      },
      body: new URLSearchParams(params).toString(),
      signal: options.signal,
    },
    options.timeoutMs ?? LOGIN_REQUEST_TIMEOUT_MS,
  );
  if (res.ok) return res.status === 204 ? null : res.json();
  throw { status: res.status, message: await readResponseMessage(res) };
}

/** API 錯誤是否為 404（資源不存在）：頁面據此顯示「找不到」而非一般錯誤 */
export const isNotFound = (err) => err?.status === 404;

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

/** PUT；options.timeoutMs 給會等外部系統的請求放寬逾時 */
export function apiPut(path, body, options = {}) {
  return request(path, {
    method: "PUT",
    body: JSON.stringify(body),
    timeoutMs: options.timeoutMs,
  });
}
