import { describe, expect, test } from "vitest";
import { toPayload } from "./LdapPage";

const form = {
  enabled: true,
  server_uri: "ldaps://ad.example.edu",
  use_starttls: false,
  bind_dn: "CN=svc,DC=example,DC=edu",
  bind_password: "",
  user_search_base: "DC=example,DC=edu",
  user_filter_template: "(sAMAccountName={username})",
  email_attribute: "mail",
  name_attribute: "displayName",
  teacher_group_dn: "CN=Teachers,DC=example,DC=edu",
  admin_group_dn: "CN=SkyLabAdmins,DC=example,DC=edu",
  auto_create_users: true,
  connect_timeout_seconds: 5,
};

describe("LdapPage toPayload 群組 DN 可清空", () => {
  test("清空管理員／老師群組 DN 送空字串，不送 null（null 會被後端當成不變更）", () => {
    const payload = toPayload({ ...form, admin_group_dn: "", teacher_group_dn: "   " });
    expect(payload.admin_group_dn).toBe("");
    expect(payload.teacher_group_dn).toBe("");
  });

  test("有值時去掉前後空白後照送", () => {
    const payload = toPayload({ ...form, admin_group_dn: "  CN=A,DC=x  " });
    expect(payload.admin_group_dn).toBe("CN=A,DC=x");
    expect(payload.teacher_group_dn).toBe(form.teacher_group_dn);
  });

  test("bind 密碼留空仍代表不變更", () => {
    expect(toPayload(form).bind_password).toBeNull();
  });
});
