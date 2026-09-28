import { describe, expect, test } from "vitest";
import { canTeachUser, isAdminUser } from "./roles";

describe("isAdminUser", () => {
  test("超級使用者或 admin 角色才算管理員，一律回布林值", () => {
    expect(isAdminUser({ is_superuser: true, role: "student" })).toBe(true);
    expect(isAdminUser({ is_superuser: false, role: "admin" })).toBe(true);
    expect(isAdminUser({ role: "teacher" })).toBe(false);
    expect(isAdminUser({ role: "student" })).toBe(false);
    expect(isAdminUser(null)).toBe(false);
    expect(isAdminUser(undefined)).toBe(false);
  });
});

describe("canTeachUser", () => {
  test("管理員與老師可以教課，學生與未登入不行", () => {
    expect(canTeachUser({ is_superuser: true })).toBe(true);
    expect(canTeachUser({ role: "admin" })).toBe(true);
    expect(canTeachUser({ role: "teacher" })).toBe(true);
    expect(canTeachUser({ role: "student" })).toBe(false);
    expect(canTeachUser(null)).toBe(false);
  });
});
