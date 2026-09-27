import { describe, expect, it } from "vitest";
import { isAdminUser, isStaffUser } from "./requestRoles";

describe("request page roles", () => {
  it("treats superusers and admins as admins", () => {
    expect(isAdminUser({ is_superuser: true, role: "student" })).toBe(true);
    expect(isAdminUser({ role: "admin" })).toBe(true);
    expect(isAdminUser({ role: "teacher" })).toBe(false);
    expect(isAdminUser(null)).toBe(false);
  });

  it("adds teachers to the staff check but not students", () => {
    expect(isStaffUser({ role: "teacher" })).toBe(true);
    expect(isStaffUser({ role: "admin" })).toBe(true);
    expect(isStaffUser({ is_superuser: true })).toBe(true);
    expect(isStaffUser({ role: "student" })).toBe(false);
    expect(isStaffUser(undefined)).toBe(false);
  });
});
