/**
 * api.js
 * 統一的 API 請求入口。
 * - 自動帶入 Authorization header
 * - 401 時自動用 refresh token 續期並重試一次，失敗才強制登出
 * - 統一錯誤格式：失敗時 throw { status, message }
 *
 * 使用方式：
 *   import { apiGet, apiPost } from "@/services/api";
 *   const user = await apiGet("/api/v1/users/me");
 */

import { AuthStorage } from "./auth";

const BASE_URL = import.meta.env.VITE_API_URL ?? "";
const REFRESH_PATH = "/api/v1/login/refresh-token";

/** 進行中的 refresh 請求；多個 401 同時發生時共用同一次 refresh */
let refreshPromise = null;

/**
 * 用 refresh token 換一組新的 access + refresh token。
 * @returns {Promise<boolean>} 成功儲存新 token 回傳 true
 */
export function refreshTokens() {
  if (!refreshPromise) {
    refreshPromise = doRefresh().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

async function doRefresh() {
  const refreshToken = AuthStorage.getRefreshToken();
  if (!refreshToken) return false;

  try {
    const res = await fetch(`${BASE_URL}${REFRESH_PATH}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!res.ok) return false;
    AuthStorage.setTokens(await res.json());
    return true;
  } catch {
    return false;
  }
}

/** 建立共用 headers（每次重建，重試時才會帶到新 token） */
function buildHeaders(extra = {}, isFormData = false) {
  // FormData 由瀏覽器自動帶 multipart boundary，不能手動設 Content-Type
  const headers = isFormData
    ? { ...extra }
    : { "Content-Type": "application/json", ...extra };
  const token = AuthStorage.getAccessToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return headers;
}

/** 統一處理 response；401 時先嘗試續期再重試一次 */
async function request(path, init, isRetry = false) {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: buildHeaders(init.headers, init.body instanceof FormData),
  });

  if (res.ok) {
    // 204 No Content 不會有 body
    return res.status === 204 ? null : res.json();
  }

  if (res.status === 401) {
    if (!isRetry && (await refreshTokens())) {
      return request(path, init, true);
    }
    // 續期失敗（或重試後仍 401）→ 清除 token，通知 AuthContext 強制登出
    AuthStorage.clearTokens();
    window.dispatchEvent(new Event("auth:unauthorized"));
  }

  let message = `HTTP ${res.status}`;
  try {
    const body = await res.json();
    message = body?.detail ?? body?.message ?? message;
  } catch {
    // 若 body 不是 JSON 就用預設訊息
  }

  throw { status: res.status, message };
}

/** GET */
export function apiGet(path) {
  return request(path, { method: "GET" });
}

/** GET（回傳 Blob，檔案下載用；同樣支援 401 續期重試） */
export async function apiGetBlob(path, isRetry = false) {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: "GET",
    headers: buildHeaders({}, true),
  });
  if (res.ok) return res.blob();

  if (res.status === 401 && !isRetry && (await refreshTokens())) {
    return apiGetBlob(path, true);
  }
  throw { status: res.status, message: `HTTP ${res.status}` };
}

/** POST（JSON body，回傳 Blob，報表匯出用；同樣支援 401 續期重試） */
export async function apiPostBlob(path, body, isRetry = false) {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers: buildHeaders(),
    body: JSON.stringify(body),
  });
  if (res.ok) return res.blob();

  if (res.status === 401 && !isRetry && (await refreshTokens())) {
    return apiPostBlob(path, body, true);
  }

  let message = `HTTP ${res.status}`;
  try {
    const errBody = await res.json();
    message = errBody?.detail ?? errBody?.message ?? message;
  } catch {
    // 若 body 不是 JSON 就用預設訊息
  }
  throw { status: res.status, message };
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
  });
}

/** POST（form-urlencoded，登入用，不帶 Authorization 也不重試） */
export async function apiPostForm(path, params) {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
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
export function apiPostMultipart(path, formData) {
  return request(path, { method: "POST", body: formData });
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
