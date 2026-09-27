/**
 * AuthContext.jsx
 * 提供全域認證狀態，並區分「登入確實失效」與「暫時無法連線」。
 */

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import { AuthStorage, loginLdap, loginTotp } from "../services/auth";
import { apiPost, apiPostForm, refreshTokens } from "../services/api";
import {
  AuthSessionStatus,
  restoreStoredSession,
} from "../services/authSession";
import { unsubscribePush } from "../services/webPush";

const AuthContext = createContext(null);

/** access token 到期前多久觸發續期 */
const REFRESH_MARGIN_MS = 60 * 1000;
/** 暫時無法 refresh 時保留 session，稍後再試。 */
const REFRESH_RETRY_MS = 30 * 1000;
const REFRESH_WARNING_ID = "auth-refresh-unavailable";

/**
 * 登入後的服務檢查畫面：每次「登入」才跑一次（重新整理、其他分頁同步不算）。
 * 旗標放 sessionStorage，學生卡在「請通知管理員」時重新整理也不會直接略過。
 */
const LOGIN_PREFLIGHT_KEY = "skylab:login-preflight";

function readLoginPreflightFlag() {
  try {
    return sessionStorage.getItem(LOGIN_PREFLIGHT_KEY) === "1";
  } catch {
    return false;
  }
}

function writeLoginPreflightFlag(pending) {
  try {
    if (pending) sessionStorage.setItem(LOGIN_PREFLIGHT_KEY, "1");
    else sessionStorage.removeItem(LOGIN_PREFLIGHT_KEY);
  } catch {
    // 無法存取 sessionStorage（隱私模式等）：只靠記憶體狀態
  }
}

const INITIAL_SESSION = {
  status: AuthSessionStatus.CHECKING,
  sessionId: null,
  user: null,
  error: null,
};

export function AuthProvider({ children }) {
  const { t } = useTranslation("common");
  const [session, setSession] = useState(INITIAL_SESSION);
  const [loginPreflightPending, setLoginPreflightPending] = useState(readLoginPreflightFlag);
  const expiryTimerRef = useRef(null);
  const refreshGenerationRef = useRef(0);
  const sessionAbortRef = useRef(null);

  const clearExpiryTimer = useCallback(() => {
    refreshGenerationRef.current += 1;
    if (expiryTimerRef.current !== null) {
      clearTimeout(expiryTimerRef.current);
      expiryTimerRef.current = null;
    }
  }, []);

  const cancelSessionCheck = useCallback(() => {
    sessionAbortRef.current?.abort();
    sessionAbortRef.current = null;
  }, []);

  const setLoginPreflight = useCallback((pending) => {
    writeLoginPreflightFlag(pending);
    setLoginPreflightPending(pending);
  }, []);

  /** 服務檢查通過（或管理員略過）：進入系統。 */
  const finishLoginPreflight = useCallback(
    () => setLoginPreflight(false),
    [setLoginPreflight],
  );

  /** 使用者主動登出。 */
  const logout = useCallback(() => {
    cancelSessionCheck();
    clearExpiryTimer();
    toast.dismiss(REFRESH_WARNING_ID);

    // 先請伺服器把 access/refresh token 的 JTI 加入黑名單，否則登出後
    // token 在到期前仍然有效。必須在 clearTokens() 之前呼叫：apiPost 是同步
    // wrapper，request() 會在第一個 await 之前就取好 token snapshot。
    // 撤銷失敗（離線、後端不可用）不應阻擋本機登出，因此僅記錄不中斷。
    const { refreshToken } = AuthStorage.getSnapshot();
    apiPost(
      "/api/v1/login/logout",
      refreshToken ? { refresh_token: refreshToken } : {},
    ).catch(() => {});

    // 登出後這台瀏覽器不該再收到這個帳號的推播：解除本機訂閱（fire-and-forget）。
    // 後端那筆訂閱即使來不及刪，endpoint 失效後推播服務會回 410，後端自行清掉。
    unsubscribePush().catch(() => {});

    AuthStorage.clearTokens();
    setLoginPreflight(false);
    setSession({
      status: AuthSessionStatus.ANONYMOUS,
      sessionId: null,
      user: null,
      error: null,
    });
  }, [cancelSessionCheck, clearExpiryTimer, setLoginPreflight]);

  /** API 已確認 token 失效；token 已由發出事件的請求條件式清除。 */
  const finishExpiredSession = useCallback(() => {
    cancelSessionCheck();
    clearExpiryTimer();
    toast.dismiss(REFRESH_WARNING_ID);
    setLoginPreflight(false);
    setSession({
      status: AuthSessionStatus.ANONYMOUS,
      sessionId: null,
      user: null,
      error: null,
    });
    toast.error(t("AuthContext.sessionExpired"));
  }, [cancelSessionCheck, clearExpiryTimer, setLoginPreflight, t]);

  /**
   * 依 access token 的 exp 排程 refresh。
   * 只有 refresh 端點明確回 401 才登出；暫時斷線或 5xx 會保留 session 重試。
   */
  const scheduleTokenRefresh = useCallback((retryDelayMs = null) => {
    clearExpiryTimer();

    const expiry = AuthStorage.getTokenExpiry();
    if (retryDelayMs === null && !expiry) return;
    const delay = retryDelayMs ?? Math.max(expiry - Date.now() - REFRESH_MARGIN_MS, 0);
    const generation = refreshGenerationRef.current;

    expiryTimerRef.current = setTimeout(async () => {
      expiryTimerRef.current = null;
      let outcome;
      try {
        outcome = await refreshTokens();
      } catch (error) {
        outcome = { kind: "unavailable", status: 0, error };
      }

      // timer 啟動後若已登出、重新排程或卸載，不再更新狀態或建立新 timer。
      if (refreshGenerationRef.current !== generation) return;

      if (outcome.kind === "refreshed" || outcome.kind === "superseded") {
        toast.dismiss(REFRESH_WARNING_ID);
        if (AuthStorage.isLoggedIn()) scheduleTokenRefresh();
        return;
      }

      if (outcome.kind === "invalid") {
        if (AuthStorage.clearTokensIfCurrent(outcome.snapshot)) {
          finishExpiredSession();
        } else if (AuthStorage.isLoggedIn()) {
          scheduleTokenRefresh();
        }
        return;
      }

      if (!AuthStorage.isLoggedIn()) return;
      toast.warning(t("AuthContext.connectionInterrupted"), {
        id: REFRESH_WARNING_ID,
      });
      scheduleTokenRefresh(REFRESH_RETRY_MS);
    }, delay);
  }, [clearExpiryTimer, finishExpiredSession, t]);

  /** 驗證 localStorage 中的 session；暫時性錯誤會進 unavailable，不會刪 token。 */
  const verifyStoredSession = useCallback(async ({ showChecking = true } = {}) => {
    cancelSessionCheck();
    const controller = new AbortController();
    sessionAbortRef.current = controller;
    const checkSessionId = AuthStorage.getSnapshot().sessionId;

    if (showChecking) {
      setSession((current) => {
        const sameSession = current.sessionId === checkSessionId
          && AuthStorage.isSameSession({ sessionId: checkSessionId })
          && AuthStorage.isLoggedIn();
        return {
          status: AuthSessionStatus.CHECKING,
          sessionId: checkSessionId,
          user: sameSession ? current.user : null,
          error: sameSession ? current.error : null,
        };
      });
    }

    let result;
    try {
      result = await restoreStoredSession({ signal: controller.signal });
    } catch (error) {
      if (controller.signal.aborted || error?.cancelled) return null;
      result = { status: AuthSessionStatus.UNAVAILABLE, user: null, error };
    }

    if (controller.signal.aborted || sessionAbortRef.current !== controller) return null;
    sessionAbortRef.current = null;

    if (result.status === AuthSessionStatus.AUTHENTICATED) {
      setSession({
        status: result.status,
        sessionId: checkSessionId,
        user: result.user,
        error: null,
      });
      scheduleTokenRefresh();
    } else if (result.status === AuthSessionStatus.ANONYMOUS) {
      clearExpiryTimer();
      setSession({
        status: result.status,
        sessionId: null,
        user: null,
        error: null,
      });
    } else {
      setSession((current) => {
        const sameSession = current.sessionId === checkSessionId
          && AuthStorage.isSameSession({ sessionId: checkSessionId })
          && AuthStorage.isLoggedIn();
        return {
          status: AuthSessionStatus.UNAVAILABLE,
          sessionId: checkSessionId,
          user: sameSession ? current.user : null,
          error: result.error,
        };
      });
    }

    return result;
  }, [cancelSessionCheck, clearExpiryTimer, scheduleTokenRefresh]);

  useEffect(() => {
    void verifyStoredSession();
    return cancelSessionCheck;
  }, [cancelSessionCheck, verifyStoredSession]);

  useEffect(() => {
    window.addEventListener("auth:unauthorized", finishExpiredSession);
    return () => window.removeEventListener("auth:unauthorized", finishExpiredSession);
  }, [finishExpiredSession]);

  /** 網路恢復時自動重新驗證，不要求使用者再次輸入帳密。 */
  useEffect(() => {
    const handleOnline = () => {
      if (AuthStorage.isLoggedIn()) {
        void verifyStoredSession();
      }
    };
    window.addEventListener("online", handleOnline);
    return () => window.removeEventListener("online", handleOnline);
  }, [verifyStoredSession]);

  /** 其他分頁登入、登出或 refresh 後，同步畫面身份與實際使用的 token。 */
  useEffect(() => {
    let syncTimer = null;
    const handleStorage = (event) => {
      if (!AuthStorage.isRelevantStorageKey(event.key)) return;
      if (syncTimer !== null) clearTimeout(syncTimer);
      syncTimer = setTimeout(() => {
        syncTimer = null;
        void verifyStoredSession();
      }, 0);
    };

    window.addEventListener("storage", handleStorage);
    return () => {
      window.removeEventListener("storage", handleStorage);
      if (syncTimer !== null) clearTimeout(syncTimer);
    };
  }, [verifyStoredSession]);

  useEffect(() => () => {
    cancelSessionCheck();
    clearExpiryTimer();
  }, [cancelSessionCheck, clearExpiryTimer]);

  const completeLogin = useCallback(async () => {
    // 先立旗標再載入使用者：畫面從登入頁直接切到服務檢查，不會閃過首頁。
    // 裝置授權（device_code）是替桌面端核准登入，不跑檢查。
    const isDeviceApproval = new URLSearchParams(window.location.search).has("device_code");
    if (!isDeviceApproval) setLoginPreflight(true);
    const result = await verifyStoredSession({ showChecking: false });
    if (result?.status === AuthSessionStatus.ANONYMOUS) {
      setLoginPreflight(false);
      throw { status: 401, message: t("AuthContext.loginVerificationFailed") };
    }
    return result;
  }, [verifyStoredSession, setLoginPreflight, t]);

  /**
   * 帳號已綁定兩步驟驗證時，第一階段只會拿到挑戰 token（沒有 access_token）：
   * 回傳 { totpRequired, totpToken } 讓登入頁切到驗證碼步驟，再呼叫 totpLogin。
   */
  const toTotpChallenge = (tokens) =>
    tokens?.totp_required ? { totpRequired: true, totpToken: tokens.totp_token } : null;

  const login = useCallback(async (username, password) => {
    const tokens = await apiPostForm("/api/v1/login/access-token", {
      username,
      password,
    });
    const challenge = toTotpChallenge(tokens);
    if (challenge) return challenge;
    AuthStorage.setTokens(tokens);
    await completeLogin();
    return null;
  }, [completeLogin]);

  const googleLogin = useCallback(async (idToken) => {
    const tokens = await apiPost("/api/v1/login/google", { id_token: idToken });
    const challenge = toTotpChallenge(tokens);
    if (challenge) return challenge;
    AuthStorage.setTokens(tokens);
    await completeLogin();
    return null;
  }, [completeLogin]);

  const ldapLogin = useCallback(async (username, password) => {
    const tokens = await loginLdap(username, password);
    const challenge = toTotpChallenge(tokens);
    if (challenge) return challenge;
    await completeLogin();
    return null;
  }, [completeLogin]);

  /** 兩步驟驗證第二階段：驗證碼通過後才真正登入 */
  const totpLogin = useCallback(async (totpToken, code) => {
    await loginTotp(totpToken, code);
    await completeLogin();
  }, [completeLogin]);

  const updateUser = useCallback((patch) => {
    setSession((current) => ({
      ...current,
      user: current.user ? { ...current.user, ...patch } : current.user,
    }));
  }, []);

  const retrySession = useCallback(
    () => verifyStoredSession(),
    [verifyStoredSession],
  );

  return (
    <AuthContext.Provider
      value={{
        user: session.user,
        loading: session.status === AuthSessionStatus.CHECKING,
        authError: session.error,
        authStatus: session.status,
        login,
        googleLogin,
        ldapLogin,
        totpLogin,
        logout,
        retrySession,
        updateUser,
        loginPreflightPending,
        finishLoginPreflight,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
