import { describe, expect, test } from "vitest";
import { buildUserPayload } from "./AdminPage";

const form = {
  email: " admin@example.edu ",
  full_name: " ",
  password: "",
  role: "student",
  is_active: false,
  totp_required: true,
};

describe("AdminPage 使用者表單 payload", () => {
  test("編輯自己時不送 role／is_active，避免把自己停用或降級", () => {
    const payload = buildUserPayload(form, { isSelf: true });
    expect(payload).not.toHaveProperty("role");
    expect(payload).not.toHaveProperty("is_active");
    expect(payload).toMatchObject({ email: "admin@example.edu", full_name: null, totp_required: true });
  });

  test("編輯別人時照送 role／is_active", () => {
    expect(buildUserPayload(form)).toMatchObject({ role: "student", is_active: false });
  });

  test("LDAP 帳號不送本地密碼，其餘有填才送", () => {
    const withPw = { ...form, password: "longpassword" };
    expect(buildUserPayload(withPw, { isLdap: true })).not.toHaveProperty("password");
    expect(buildUserPayload(withPw).password).toBe("longpassword");
    expect(buildUserPayload(form)).not.toHaveProperty("password");
  });
});
