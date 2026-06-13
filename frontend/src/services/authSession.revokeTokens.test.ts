import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { AuthSessionService } from "./authSession"

const ACCESS_KEY = "access_token"
const REFRESH_KEY = "refresh_token"

class MemoryStorage {
  private store = new Map<string, string>()
  getItem(k: string) {
    return this.store.get(k) ?? null
  }
  setItem(k: string, v: string) {
    this.store.set(k, v)
  }
  removeItem(k: string) {
    this.store.delete(k)
  }
  clear() {
    this.store.clear()
  }
  get length() {
    return this.store.size
  }
  key(i: number) {
    return Array.from(this.store.keys())[i] ?? null
  }
}

describe("AuthSessionService.revokeTokens", () => {
  beforeEach(() => {
    // @ts-expect-error - jsdom shim
    globalThis.window = globalThis as unknown as Window
    globalThis.localStorage = new MemoryStorage()
  })

  afterEach(() => {
    vi.restoreAllMocks()
    // @ts-expect-error - cleanup
    delete globalThis.window
    // @ts-expect-error - cleanup
    delete globalThis.localStorage
  })

  it("does nothing when no access token is stored", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(null, { status: 200 }))

    await AuthSessionService.revokeTokens()

    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it("posts access + refresh token to /login/logout when both present", async () => {
    localStorage.setItem(ACCESS_KEY, "access-abc")
    localStorage.setItem(REFRESH_KEY, "refresh-xyz")

    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(null, { status: 200 }))

    await AuthSessionService.revokeTokens()

    expect(fetchSpy).toHaveBeenCalledTimes(1)
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit]
    expect(url).toMatch(/\/api\/v1\/login\/logout$/)
    expect(init.method).toBe("POST")
    expect((init.headers as Record<string, string>).Authorization).toBe(
      "Bearer access-abc",
    )
    expect(JSON.parse(init.body as string)).toEqual({
      refresh_token: "refresh-xyz",
    })
    expect(init.keepalive).toBe(true)
  })

  it("sends an empty body when only access token exists", async () => {
    localStorage.setItem(ACCESS_KEY, "access-only")

    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(null, { status: 200 }))

    await AuthSessionService.revokeTokens()

    const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit]
    expect(JSON.parse(init.body as string)).toEqual({})
  })

  it("never throws even if fetch rejects", async () => {
    localStorage.setItem(ACCESS_KEY, "access-fail")
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network down"))

    await expect(AuthSessionService.revokeTokens()).resolves.toBeUndefined()
  })
})
