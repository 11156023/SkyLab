/**
 * auth.js
 * 負責 token 的讀寫與清除，所有操作都集中在這裡。
 * 其他模組若需要 token，請透過這裡取得，不要直接讀 localStorage。
 *
 * 另提供登入前（無 token）的認證端點：getLoginMethods / loginLdap。
 * 這些走純 fetch 而非 api.js 的 request()——登入失敗的 401 不該觸發
 * refresh 重試與 auth:unauthorized 強制登出事件。
 */

const BASE_URL = import.meta.env.VITE_API_URL ?? "";

const ACCESS_TOKEN_KEY  = "access_token";
const REFRESH_TOKEN_KEY = "refresh_token";

/** 解析 JWT payload，取得 exp（毫秒）；失敗回傳 null */
function parseJwtExpiry(token) {
  try {
    const payload = JSON.parse(atob(token.split(".")[1]));
    return payload.exp ? payload.exp * 1000 : null;
  } catch {
    return null;
  }
}

export const AuthStorage = {
  getAccessToken()  { return localStorage.getItem(ACCESS_TOKEN_KEY);  },
  getRefreshToken() { return localStorage.getItem(REFRESH_TOKEN_KEY); },

  /** 取得 access token 的過期時間（ms），若無法解析回傳 null */
  getTokenExpiry() {
    const token = localStorage.getItem(ACCESS_TOKEN_KEY);
    return token ? parseJwtExpiry(token) : null;
  },

  setTokens({ access_token, refresh_token }) {
    localStorage.setItem(ACCESS_TOKEN_KEY, access_token);
    if (refresh_token) {
      localStorage.setItem(REFRESH_TOKEN_KEY, refresh_token);
    }
  },

  clearTokens() {
    localStorage.removeItem(ACCESS_TOKEN_KEY);
    localStorage.removeItem(REFRESH_TOKEN_KEY);
  },

  isLoggedIn() {
    return Boolean(localStorage.getItem(ACCESS_TOKEN_KEY));
  },
};

/** 解析錯誤 response 的訊息，統一 throw { status, message } */
async function throwApiError(res) {
  let message = `HTTP ${res.status}`;
  try {
    const body = await res.json();
    message = body?.detail ?? body?.message ?? message;
  } catch {
    // 若 body 不是 JSON 就用預設訊息
  }
  throw { status: res.status, message };
}

/**
 * 取得後端啟用的登入方式（公開端點，登入頁據此決定是否顯示 LDAP 分頁）
 * @returns {Promise<{password: boolean, google: boolean, ldap: boolean}>}
 */
export async function getLoginMethods() {
  const res = await fetch(`${BASE_URL}/api/v1/login/methods`);
  if (!res.ok) await throwApiError(res);
  return res.json();
}

/**
 * 以校園 LDAP/AD 帳號登入，成功後儲存 tokens
 * @throws {{ status, message }} 登入失敗時
 */
export async function loginLdap(username, password) {
  const res = await fetch(`${BASE_URL}/api/v1/login/ldap`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) await throwApiError(res);

  const tokens = await res.json();
  AuthStorage.setTokens(tokens);
  return tokens;
}
