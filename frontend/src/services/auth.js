/**
 * auth.js
 * 負責 token 的讀寫與清除，所有操作都集中在這裡。
 * 其他模組若需要 token，請透過這裡取得，不要直接讀 localStorage。
 *
 * 另提供登入前（無 token）的認證端點：getLoginMethods / loginLdap。
 * 這些不走 api.js 的 request()——登入失敗的 401 不該觸發 refresh 重試與
 * auth:unauthorized 強制登出事件——但仍用 fetchWithTimeout 保住逾時保護。
 */

import {
  LOGIN_REQUEST_TIMEOUT_MS,
  fetchWithTimeout,
} from "./fetchWithTimeout";

const BASE_URL = import.meta.env.VITE_API_URL ?? "";

/** 舊版儲存鍵；首次讀取時會無縫遷移到 session-scoped record。 */
const ACCESS_TOKEN_KEY  = "access_token";
const REFRESH_TOKEN_KEY = "refresh_token";
const SESSION_ID_KEY = "auth_session_id";
const SESSION_VERSION_PREFIX = "auth_session_version:";
const SESSION_REVOKED_PREFIX = "auth_session_revoked:";
const SESSION_RECORD_PREFIX = "auth_session_tokens:";

function createSessionId() {
  const cryptoObj = globalThis.crypto;
  if (cryptoObj?.randomUUID) return cryptoObj.randomUUID();
  // randomUUID 需要 secure context；getRandomValues 沒有這個限制
  const bytes = cryptoObj.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

function sessionVersionKey(sessionId) {
  return `${SESSION_VERSION_PREFIX}${sessionId}`;
}

function sessionRevokedKey(sessionId) {
  return `${SESSION_REVOKED_PREFIX}${sessionId}`;
}

function sessionRecordKey(sessionId, recordId) {
  return `${SESSION_RECORD_PREFIX}${sessionId}:${recordId}`;
}

function serializeTokenRecord({ access_token, refresh_token }) {
  return JSON.stringify({
    accessToken: access_token ?? null,
    refreshToken: refresh_token ?? null,
  });
}

function readTokenRecord(sessionId, recordId) {
  if (!sessionId || !recordId) return { accessToken: null, refreshToken: null };
  try {
    const record = JSON.parse(localStorage.getItem(sessionRecordKey(sessionId, recordId)));
    return {
      accessToken: record?.accessToken ?? null,
      refreshToken: record?.refreshToken ?? null,
    };
  } catch {
    return { accessToken: null, refreshToken: null };
  }
}

function migrateLegacyTokens() {
  const accessToken = localStorage.getItem(ACCESS_TOKEN_KEY);
  const refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY);
  if (!accessToken && !refreshToken) return null;

  const sessionId = createSessionId();
  const recordId = createSessionId();
  localStorage.setItem(
    sessionRecordKey(sessionId, recordId),
    serializeTokenRecord({ access_token: accessToken, refresh_token: refreshToken }),
  );
  localStorage.setItem(sessionVersionKey(sessionId), recordId);

  // record 先寫好，再以 pointer 作為一次性的 session commit。
  const currentSessionId = localStorage.getItem(SESSION_ID_KEY);
  if (currentSessionId) {
    localStorage.removeItem(sessionVersionKey(sessionId));
    localStorage.removeItem(sessionRecordKey(sessionId, recordId));
    localStorage.removeItem(ACCESS_TOKEN_KEY);
    localStorage.removeItem(REFRESH_TOKEN_KEY);
    return currentSessionId;
  }
  localStorage.setItem(SESSION_ID_KEY, sessionId);
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
  return sessionId;
}

function readTokenSnapshot() {
  const sessionId = localStorage.getItem(SESSION_ID_KEY) ?? migrateLegacyTokens();
  const recordId = sessionId
    ? localStorage.getItem(sessionVersionKey(sessionId))
    : null;
  const revoked = Boolean(
    sessionId && localStorage.getItem(sessionRevokedKey(sessionId)) === "1"
  );
  const record = revoked
    ? { accessToken: null, refreshToken: null }
    : readTokenRecord(sessionId, recordId);
  return {
    sessionId,
    recordId,
    revoked,
    accessToken: record.accessToken,
    refreshToken: record.refreshToken,
  };
}

function snapshotsMatch(left, right) {
  return left.sessionId === right.sessionId
    && left.recordId === right.recordId
    && left.revoked === right.revoked
    && left.accessToken === right.accessToken
    && left.refreshToken === right.refreshToken;
}

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
  getAccessToken()  { return readTokenSnapshot().accessToken;  },
  getRefreshToken() { return readTokenSnapshot().refreshToken; },

  /** 取得同一時間點的 token pair，供 refresh 的競態檢查使用。 */
  getSnapshot() {
    return readTokenSnapshot();
  },

  matchesSnapshot(snapshot) {
    return snapshotsMatch(readTokenSnapshot(), snapshot);
  },

  /** token 可輪替，但同一個明確登入 session 的 generation 不會改變。 */
  isSameSession(snapshot) {
    const currentSessionId = localStorage.getItem(SESSION_ID_KEY);
    return Boolean(
      snapshot?.sessionId
      && currentSessionId === snapshot.sessionId
      && localStorage.getItem(sessionRevokedKey(currentSessionId)) !== "1"
    );
  },

  /** 判斷其他分頁的 storage event 是否會影響目前認證狀態。 */
  isRelevantStorageKey(key) {
    if (key === null || key === SESSION_ID_KEY) return true;
    if (key === ACCESS_TOKEN_KEY || key === REFRESH_TOKEN_KEY) return true;
    const currentSessionId = localStorage.getItem(SESSION_ID_KEY);
    if (!currentSessionId) return false;
    if (key === sessionVersionKey(currentSessionId)) return true;
    if (key === sessionRevokedKey(currentSessionId)) return true;
    const currentRecordId = localStorage.getItem(sessionVersionKey(currentSessionId));
    return Boolean(
      currentRecordId
      && key === sessionRecordKey(currentSessionId, currentRecordId)
    );
  },

  /** 取得 access token 的過期時間（ms），若無法解析回傳 null */
  getTokenExpiry() {
    const token = readTokenSnapshot().accessToken;
    return token ? parseJwtExpiry(token) : null;
  },

  setTokens({ access_token, refresh_token }) {
    const previousSessionId = localStorage.getItem(SESSION_ID_KEY);
    const previousRecordId = previousSessionId
      ? localStorage.getItem(sessionVersionKey(previousSessionId))
      : null;
    const sessionId = createSessionId();
    const recordId = createSessionId();
    localStorage.setItem(
      sessionRecordKey(sessionId, recordId),
      serializeTokenRecord({ access_token, refresh_token }),
    );
    localStorage.setItem(sessionVersionKey(sessionId), recordId);
    localStorage.setItem(SESSION_ID_KEY, sessionId);
    localStorage.removeItem(ACCESS_TOKEN_KEY);
    localStorage.removeItem(REFRESH_TOKEN_KEY);
    if (previousSessionId && previousSessionId !== sessionId) {
      localStorage.removeItem(sessionVersionKey(previousSessionId));
      localStorage.removeItem(sessionRevokedKey(previousSessionId));
      if (previousRecordId) {
        localStorage.removeItem(sessionRecordKey(previousSessionId, previousRecordId));
      }
    }
  },

  /** 僅在發出 refresh 後 session 尚未被登出或取代時寫入結果。 */
  setTokensIfCurrent(snapshot, tokens) {
    if (!snapshot?.sessionId || !snapshot.recordId) return false;
    if (!snapshotsMatch(readTokenSnapshot(), snapshot)) return false;
    const recordId = createSessionId();
    const key = sessionRecordKey(snapshot.sessionId, recordId);
    const serialized = serializeTokenRecord(tokens);
    localStorage.setItem(key, serialized);
    localStorage.setItem(sessionVersionKey(snapshot.sessionId), recordId);

    const isCurrent = localStorage.getItem(SESSION_ID_KEY) === snapshot.sessionId
      && localStorage.getItem(sessionVersionKey(snapshot.sessionId)) === recordId
      && localStorage.getItem(sessionRevokedKey(snapshot.sessionId)) !== "1";
    if (isCurrent) {
      localStorage.removeItem(sessionRecordKey(snapshot.sessionId, snapshot.recordId));
      return true;
    }

    // logout／新登入在寫入間發生時，只清掉舊 generation 的晚到結果。
    if (localStorage.getItem(key) === serialized) localStorage.removeItem(key);
    if (localStorage.getItem(sessionVersionKey(snapshot.sessionId)) === recordId) {
      localStorage.removeItem(sessionVersionKey(snapshot.sessionId));
    }
    return false;
  },

  clearTokens() {
    const sessionId = localStorage.getItem(SESSION_ID_KEY);
    const recordId = sessionId
      ? localStorage.getItem(sessionVersionKey(sessionId))
      : null;
    localStorage.removeItem(SESSION_ID_KEY);
    localStorage.removeItem(ACCESS_TOKEN_KEY);
    localStorage.removeItem(REFRESH_TOKEN_KEY);
    if (sessionId) localStorage.removeItem(sessionVersionKey(sessionId));
    if (sessionId) localStorage.removeItem(sessionRevokedKey(sessionId));
    if (sessionId && recordId) {
      localStorage.removeItem(sessionRecordKey(sessionId, recordId));
    }
  },

  /** 僅清除發出請求時的同一個 session，避免舊 401 刪掉新登入。 */
  clearTokensIfCurrent(snapshot) {
    if (!snapshot?.sessionId || !snapshot.recordId) return false;
    if (!snapshotsMatch(readTokenSnapshot(), snapshot)) return false;
    localStorage.setItem(sessionRevokedKey(snapshot.sessionId), "1");
    const currentRecordId = localStorage.getItem(sessionVersionKey(snapshot.sessionId));
    if (currentRecordId) {
      localStorage.removeItem(sessionRecordKey(snapshot.sessionId, currentRecordId));
    }
    return localStorage.getItem(SESSION_ID_KEY) === snapshot.sessionId
      && localStorage.getItem(sessionRevokedKey(snapshot.sessionId)) === "1";
  },

  isLoggedIn() {
    return Boolean(readTokenSnapshot().accessToken);
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
  /* 帶逾時：登入頁靠這支決定要不要顯示 LDAP 分頁，後端沒回應時不能卡住整頁 */
  const res = await fetchWithTimeout(
    `${BASE_URL}/api/v1/login/methods`,
    {},
    LOGIN_REQUEST_TIMEOUT_MS,
  );
  if (!res.ok) await throwApiError(res);
  return res.json();
}

/**
 * 以校園 LDAP/AD 帳號登入，成功後儲存 tokens
 * @throws {{ status, message }} 登入失敗時
 */
export async function loginLdap(username, password) {
  /* 帶逾時：LDAP 伺服器沒回應時登入按鈕要收得到錯誤，不能一直轉圈 */
  const res = await fetchWithTimeout(
    `${BASE_URL}/api/v1/login/ldap`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    },
    LOGIN_REQUEST_TIMEOUT_MS,
  );
  if (!res.ok) await throwApiError(res);

  const tokens = await res.json();
  AuthStorage.setTokens(tokens);
  return tokens;
}
