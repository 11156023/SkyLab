import type { ApiRequestOptions } from "@/client/core/ApiRequestOptions"

import { AuthSessionService } from "./authSession"

const AUTH_TOKEN_BYPASS_PREFIXES = [
  "/api/v1/login/access-token",
  "/api/v1/login/google",
  "/api/v1/login/refresh-token",
  "/api/v1/password-recovery",
  "/api/v1/reset-password",
]

const ACCESS_TOKEN_REFRESH_SKEW_MS = 30_000

function decodeBase64Url(value: string): string | null {
  try {
    const normalized = value.replace(/-/g, "+").replace(/_/g, "/")
    const paddingLength = (4 - (normalized.length % 4)) % 4
    const padded = `${normalized}${"=".repeat(paddingLength)}`

    return atob(padded)
  } catch {
    return null
  }
}

export function isTokenExpiredOrNearExpiry(
  token: string,
  now = Date.now(),
  skewMs = ACCESS_TOKEN_REFRESH_SKEW_MS,
): boolean {
  const [, payloadPart] = token.split(".")
  if (!payloadPart) {
    return true
  }

  const payloadJson = decodeBase64Url(payloadPart)
  if (!payloadJson) {
    return true
  }

  try {
    const payload = JSON.parse(payloadJson) as { exp?: number }
    if (typeof payload.exp !== "number") {
      return true
    }

    return payload.exp * 1000 <= now + skewMs
  } catch {
    return true
  }
}

export function shouldBypassOpenApiToken(url: string): boolean {
  return AUTH_TOKEN_BYPASS_PREFIXES.some(
    (prefix) => url === prefix || url.startsWith(`${prefix}/`),
  )
}

export async function prepareOpenApiRequestAuth(
  options: ApiRequestOptions<unknown>,
): Promise<void> {
  if (shouldBypassOpenApiToken(options.url)) {
    return
  }

  const accessToken = AuthSessionService.getAccessToken()
  if (!accessToken) {
    return
  }

  if (!isTokenExpiredOrNearExpiry(accessToken)) {
    return
  }

  const refreshed = await AuthSessionService.refreshAccessToken()
  if (!refreshed && isTokenExpiredOrNearExpiry(accessToken, Date.now(), 0)) {
    AuthSessionService.clearTokens()
  }
}

export function resolveOpenApiToken(
  options: ApiRequestOptions<unknown>,
): Promise<string> {
  if (shouldBypassOpenApiToken(options.url)) {
    return Promise.resolve("")
  }

  return Promise.resolve(AuthSessionService.getAccessToken() || "")
}
