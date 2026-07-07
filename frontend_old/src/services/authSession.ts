import { OpenAPI } from "@/client"
import { request as __request } from "@/client/core/request"

type AuthTokens = {
  access_token: string
  refresh_token?: string | null
}

const ACCESS_TOKEN_KEY = "access_token"
const REFRESH_TOKEN_KEY = "refresh_token"

let _refreshPromise: Promise<boolean> | null = null
let _lastSuccessfulRefreshAt = 0

export const AuthSessionService = {
  getAccessToken() {
    return typeof window === "undefined"
      ? null
      : localStorage.getItem(ACCESS_TOKEN_KEY)
  },

  getRefreshToken() {
    return typeof window === "undefined"
      ? null
      : localStorage.getItem(REFRESH_TOKEN_KEY)
  },

  setTokens(tokens: AuthTokens) {
    localStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token)
    if (tokens.refresh_token) {
      localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token)
    }
  },

  clearTokens() {
    localStorage.removeItem(ACCESS_TOKEN_KEY)
    localStorage.removeItem(REFRESH_TOKEN_KEY)
    _lastSuccessfulRefreshAt = 0
  },

  /**
   * Best-effort: notify the backend to revoke the current access + refresh
   * token JTIs in Redis. Always swallows errors — the local clearTokens()
   * call below is the source of truth for the client-side session state.
   */
  async revokeTokens(): Promise<void> {
    const accessToken = this.getAccessToken()
    if (!accessToken) return
    const refreshToken = this.getRefreshToken()
    try {
      const base = (OpenAPI.BASE ?? "").replace(/\/+$/, "")
      await fetch(`${base}/api/v1/login/logout`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${accessToken}`,
        },
        body: JSON.stringify(
          refreshToken ? { refresh_token: refreshToken } : {},
        ),
        // Don't keep the user waiting; this is fire-and-forget.
        keepalive: true,
      })
    } catch {
      // Intentional no-op: logout must always succeed locally.
    }
  },

  wasRefreshedRecently(windowMs = 3000) {
    return (
      _lastSuccessfulRefreshAt > 0 &&
      Date.now() - _lastSuccessfulRefreshAt <= windowMs
    )
  },

  async loginWithGoogle(idToken: string): Promise<AuthTokens> {
    const tokens = await __request<AuthTokens>(OpenAPI, {
      method: "POST",
      url: "/api/v1/login/google",
      body: { id_token: idToken },
      mediaType: "application/json",
    })
    this.setTokens(tokens)
    return tokens
  },

  async refreshAccessToken(): Promise<boolean> {
    if (_refreshPromise) return _refreshPromise

    _refreshPromise = (async () => {
      const refreshToken = this.getRefreshToken()
      if (!refreshToken) return false

      // Use bare fetch to bypass OpenAPI.TOKEN — otherwise this call would
      // re-enter the token resolver and cause infinite recursion when the
      // access token is near expiry.
      try {
        const base = (OpenAPI.BASE ?? "").replace(/\/+$/, "")
        const res = await fetch(`${base}/api/v1/login/refresh-token`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: refreshToken }),
        })
        if (!res.ok) return false
        const tokens = (await res.json()) as AuthTokens
        this.setTokens(tokens)
        _lastSuccessfulRefreshAt = Date.now()
        return true
      } catch {
        return false
      }
    })().finally(() => {
      _refreshPromise = null
    })

    return _refreshPromise
  },
}
