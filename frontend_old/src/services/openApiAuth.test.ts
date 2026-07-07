import { afterEach, describe, expect, it, vi } from "vitest"

import { AuthSessionService } from "@/services/authSession"

import {
  isTokenExpiredOrNearExpiry,
  prepareOpenApiRequestAuth,
  resolveOpenApiToken,
  shouldBypassOpenApiToken,
} from "./openApiAuth"

function createJwt(payload: Record<string, unknown>) {
  const encode = (value: Record<string, unknown>) => {
    return Buffer.from(JSON.stringify(value), "utf-8").toString("base64url")
  }

  return `${encode({ alg: "HS256", typ: "JWT" })}.${encode(payload)}.signature`
}

describe("openApiAuth", () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("bypasses auth headers for refresh and login endpoints", async () => {
    const getAccessTokenSpy = vi.spyOn(AuthSessionService, "getAccessToken")

    expect(shouldBypassOpenApiToken("/api/v1/login/refresh-token")).toBe(true)
    await expect(
      resolveOpenApiToken({
        method: "POST",
        url: "/api/v1/login/refresh-token",
      }),
    ).resolves.toBe("")
    expect(getAccessTokenSpy).not.toHaveBeenCalled()
  })

  it("returns the stored token for protected endpoints", async () => {
    vi.spyOn(AuthSessionService, "getAccessToken").mockReturnValue("token-123")

    await expect(
      resolveOpenApiToken({ method: "GET", url: "/api/v1/users/me" }),
    ).resolves.toBe("token-123")
  })

  it("detects tokens that are close to expiry", () => {
    const soonExpiringToken = createJwt({
      exp: Math.floor((Date.now() + 10_000) / 1000),
    })

    expect(isTokenExpiredOrNearExpiry(soonExpiringToken)).toBe(true)
  })

  it("refreshes the session before protected requests when the token is expired", async () => {
    vi.spyOn(AuthSessionService, "getAccessToken").mockReturnValue(
      createJwt({ exp: Math.floor((Date.now() - 60_000) / 1000) }),
    )
    const refreshSpy = vi
      .spyOn(AuthSessionService, "refreshAccessToken")
      .mockResolvedValue(true)

    await prepareOpenApiRequestAuth({ method: "GET", url: "/api/v1/users/me" })

    expect(refreshSpy).toHaveBeenCalledTimes(1)
  })

  it("skips refresh for auth bootstrap endpoints", async () => {
    vi.spyOn(AuthSessionService, "getAccessToken").mockReturnValue(
      createJwt({ exp: Math.floor((Date.now() - 60_000) / 1000) }),
    )
    const refreshSpy = vi
      .spyOn(AuthSessionService, "refreshAccessToken")
      .mockResolvedValue(true)

    await prepareOpenApiRequestAuth({
      method: "POST",
      url: "/api/v1/login/refresh-token",
    })

    expect(refreshSpy).not.toHaveBeenCalled()
  })
})
