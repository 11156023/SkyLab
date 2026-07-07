import { afterEach, describe, expect, it, vi } from "vitest"

import { ApiError } from "@/client/core/ApiError"
import { AuthSessionService } from "@/services/authSession"

import { handleApiError, queryClient } from "./queryClient"

function createUnauthorizedError() {
  return new ApiError(
    { method: "GET", url: "/api/v1/users/me" },
    {
      body: { detail: "Unauthorized" },
      ok: false,
      status: 401,
      statusText: "Unauthorized",
      url: "/api/v1/users/me",
    },
    "Unauthorized",
  )
}

describe("queryClient unauthorized recovery", () => {
  afterEach(() => {
    vi.restoreAllMocks()
    queryClient.clear()
  })

  it("deduplicates concurrent refresh attempts", async () => {
    vi.spyOn(AuthSessionService, "wasRefreshedRecently").mockReturnValue(false)
    const refreshSpy = vi
      .spyOn(AuthSessionService, "refreshAccessToken")
      .mockResolvedValue(true)
    const invalidateSpy = vi
      .spyOn(queryClient, "invalidateQueries")
      .mockResolvedValue(undefined)

    await Promise.all([
      handleApiError(createUnauthorizedError()),
      handleApiError(createUnauthorizedError()),
    ])

    expect(refreshSpy).toHaveBeenCalledTimes(1)
    expect(invalidateSpy).toHaveBeenCalledTimes(1)
  })

  it("skips a second refresh right after a successful refresh", async () => {
    const recentRefreshSpy = vi
      .spyOn(AuthSessionService, "wasRefreshedRecently")
      .mockReturnValueOnce(false)
      .mockReturnValueOnce(true)
    const refreshSpy = vi
      .spyOn(AuthSessionService, "refreshAccessToken")
      .mockResolvedValue(true)
    const invalidateSpy = vi
      .spyOn(queryClient, "invalidateQueries")
      .mockResolvedValue(undefined)

    await handleApiError(createUnauthorizedError())
    await handleApiError(createUnauthorizedError())

    expect(recentRefreshSpy).toHaveBeenCalledTimes(2)
    expect(refreshSpy).toHaveBeenCalledTimes(1)
    expect(invalidateSpy).toHaveBeenCalledTimes(1)
  })
})
