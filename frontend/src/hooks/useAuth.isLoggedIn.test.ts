/**
 * Tests for the isLoggedIn helper exported from useAuth.
 *
 * Pure function — only checks localStorage for the access_token key.
 * Hook itself requires React context (router/query client) so we only
 * cover the pure helper here. Hook integration tests live elsewhere.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { isLoggedIn } from "./useAuth"

class MemoryStorage implements Storage {
  private store = new Map<string, string>()
  get length(): number {
    return this.store.size
  }
  clear(): void {
    this.store.clear()
  }
  getItem(key: string): string | null {
    return this.store.get(key) ?? null
  }
  key(index: number): string | null {
    return Array.from(this.store.keys())[index] ?? null
  }
  removeItem(key: string): void {
    this.store.delete(key)
  }
  setItem(key: string, value: string): void {
    this.store.set(key, value)
  }
}

describe("isLoggedIn", () => {
  let storage: MemoryStorage

  beforeEach(() => {
    storage = new MemoryStorage()
    vi.stubGlobal("localStorage", storage)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("returns false when no access_token key exists", () => {
    expect(isLoggedIn()).toBe(false)
  })

  function tokenWithExp(exp: number) {
    const payload = btoa(JSON.stringify({ exp }))
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "")
    return `header.${payload}.signature`
  }

  it("returns true when access_token is present and not expired", () => {
    storage.setItem(
      "access_token",
      tokenWithExp(Math.floor(Date.now() / 1000) + 60),
    )
    expect(isLoggedIn()).toBe(true)
  })

  it("returns false for an empty string token", () => {
    storage.setItem("access_token", "")
    expect(isLoggedIn()).toBe(false)
  })

  it("returns false for an expired token", () => {
    storage.setItem(
      "access_token",
      tokenWithExp(Math.floor(Date.now() / 1000) - 60),
    )
    expect(isLoggedIn()).toBe(false)
  })

  it("returns false after the token is removed", () => {
    storage.setItem(
      "access_token",
      tokenWithExp(Math.floor(Date.now() / 1000) + 60),
    )
    expect(isLoggedIn()).toBe(true)
    storage.removeItem("access_token")
    expect(isLoggedIn()).toBe(false)
  })
})
