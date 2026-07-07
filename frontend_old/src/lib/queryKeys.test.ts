/**
 * Tests for the queryKeys factory.
 *
 * These keys form the cache contract for TanStack Query — accidentally
 * changing the shape causes silent cache misses across the app.
 * Pin the structure of the most-used keys to catch refactor regressions.
 */

import { describe, expect, it } from "vitest"

import { queryKeys } from "./queryKeys"

describe("queryKeys", () => {
  describe("static keys (cache stability)", () => {
    it("auth.currentUser is a fixed tuple", () => {
      expect(queryKeys.auth.currentUser).toEqual(["currentUser"])
    })

    it("users.all is a fixed tuple", () => {
      expect(queryKeys.users.all).toEqual(["users"])
    })

    it("resources.all and resources.my are distinct keys", () => {
      expect(queryKeys.resources.all).toEqual(["resources"])
      expect(queryKeys.resources.my).toEqual(["my-resources"])
      expect(queryKeys.resources.all).not.toEqual(queryKeys.resources.my)
    })

    it("vmRequests.adminList includes status discriminator", () => {
      expect(queryKeys.vmRequests.adminList("pending")).toEqual([
        "vm-requests-admin",
        "pending",
      ])
      expect(queryKeys.vmRequests.adminList("approved")).toEqual([
        "vm-requests-admin",
        "approved",
      ])
    })
  })

  describe("dynamic keys (parameter inclusion)", () => {
    it("groups.detail returns a stable key for the same id", () => {
      expect(queryKeys.groups.detail("g1")).toEqual(["group", "g1"])
      expect(queryKeys.groups.detail("g1")).toEqual(
        queryKeys.groups.detail("g1"),
      )
    })

    it("groups.detail produces distinct keys for distinct ids", () => {
      expect(queryKeys.groups.detail("a")).not.toEqual(
        queryKeys.groups.detail("b"),
      )
    })

    it("teacher judge script run key includes script and run ids", () => {
      expect(queryKeys.groups.teacherJudgeScriptRun("g1", "s1", "r1")).toEqual([
        "group",
        "g1",
        "teacher-judge-script-run",
        "s1",
        "r1",
      ])
    })

    it("resources.rrdStats includes both vmid and timeframe", () => {
      expect(queryKeys.resources.rrdStats(100, "hour")).toEqual([
        "rrdStats",
        100,
        "hour",
      ])
      expect(queryKeys.resources.rrdStats(100, "day")).not.toEqual(
        queryKeys.resources.rrdStats(100, "hour"),
      )
    })

    it("resources.detail keys are tuple-of-two", () => {
      const key = queryKeys.resources.detail(42)
      expect(key).toHaveLength(2)
      expect(key[0]).toBe("resource")
      expect(key[1]).toBe(42)
    })
  })

  describe("namespace isolation", () => {
    it("resources and vmRequests do not share keys", () => {
      expect(queryKeys.resources.all[0]).not.toBe(queryKeys.vmRequests.all[0])
    })

    it("aiApi keys are namespaced under ai-api", () => {
      expect(queryKeys.aiApi.all[0]).toBe("ai-api")
      expect(queryKeys.aiApi.myRequests[0]).toBe("ai-api")
    })
  })
})
