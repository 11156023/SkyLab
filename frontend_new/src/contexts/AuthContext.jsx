/**
 * AuthContext.jsx
 * 提供全域的認證狀態與操作：
 *   - user        當前用戶資料（null 表示未登入）
 *   - loading     初始化時驗證 token 的 loading 狀態
 *   - login()     登入，成功後更新 user
 *   - logout()    登出，清除 token 與 user
 */

import { createContext, useContext, useEffect, useRef, useState, useCallback } from "react";
import { toast } from "sonner";
import { AuthStorage } from "../services/auth";
import { apiGet, apiPostForm, refreshTokens } from "../services/api";

const AuthContext = createContext(null);

/** access token 到期前多久觸發續期 */
const REFRESH_MARGIN_MS = 60 * 1000;

export function AuthProvider({ children }) {
  const [user, setUser]       = useState(null);
  const [loading, setLoading] = useState(true); // 啟動時驗證 token
  const expiryTimerRef        = useRef(null);

  /** 清除登出計時器 */
  const clearExpiryTimer = useCallback(() => {
    if (expiryTimerRef.current) {
      clearTimeout(expiryTimerRef.current);
      expiryTimerRef.current = null;
    }
  }, []);

  /** logout - 清除 token 與用戶狀態 */
  const logout = useCallback(() => {
    clearExpiryTimer();
    AuthStorage.clearTokens();
    setUser(null);
  }, [clearExpiryTimer]);

  /** 強制登出（token 失效），與使用者主動登出不同，會顯示提示 */
  const forceLogout = useCallback(() => {
    logout();
    toast.error("登入已過期，請重新登入");
  }, [logout]);

  /**
   * 依據 token 的 exp，在到期前自動用 refresh token 續期；
   * 續期成功就重新排程，失敗才強制登出。
   */
  const scheduleTokenRefresh = useCallback(() => {
    clearExpiryTimer();
    const expiry = AuthStorage.getTokenExpiry();
    if (!expiry) return;

    const msUntilRefresh = expiry - Date.now() - REFRESH_MARGIN_MS;
    expiryTimerRef.current = setTimeout(async () => {
      const ok = await refreshTokens();
      if (ok) {
        scheduleTokenRefresh();
      } else {
        forceLogout();
      }
    }, Math.max(msUntilRefresh, 0));
  }, [clearExpiryTimer, forceLogout]);

  /** 啟動時若有 token，嘗試取得當前用戶以確認 token 仍有效 */
  useEffect(() => {
    if (!AuthStorage.isLoggedIn()) {
      setLoading(false);
      return;
    }

    apiGet("/api/v1/users/me")
      .then((me) => {
        setUser(me);
        scheduleTokenRefresh();
      })
      .catch(() => {
        // token 無效或過期（api 層已嘗試續期失敗），清除
        AuthStorage.clearTokens();
      })
      .finally(() => setLoading(false));
  }, [scheduleTokenRefresh]);

  /** 監聽 API 層拋出的 401 事件（續期也失敗時），強制登出 */
  useEffect(() => {
    window.addEventListener("auth:unauthorized", forceLogout);
    return () => window.removeEventListener("auth:unauthorized", forceLogout);
  }, [forceLogout]);

  /** 元件卸載時清除計時器 */
  useEffect(() => () => clearExpiryTimer(), [clearExpiryTimer]);

  /**
   * login - 呼叫後端取得 token，並載入用戶資料
   * @param {string} username  email
   * @param {string} password
   * @throws {{ status, message }} 登入失敗時
   */
  const login = useCallback(async (username, password) => {
    const tokens = await apiPostForm("/api/v1/login/access-token", {
      username,
      password,
    });
    AuthStorage.setTokens(tokens);

    const me = await apiGet("/api/v1/users/me");
    setUser(me);
    scheduleTokenRefresh();
  }, [scheduleTokenRefresh]);

  return (
    <AuthContext.Provider value={{ user, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

/** useAuth - 取得認證 context，必須在 AuthProvider 內使用 */
export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
