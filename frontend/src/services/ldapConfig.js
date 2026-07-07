import { apiGet, apiPost, apiPut } from "./api";

/** LDAP 連線設定（管理員）。bind_password 只在有輸入時送出（後端才會覆寫）。 */
export const LdapConfigService = {
  get() {
    return apiGet("/api/v1/admin/ldap-config");
  },

  update(body) {
    return apiPut("/api/v1/admin/ldap-config", body);
  },

  /** 測試 service bind；body 可帶未儲存的欄位覆寫（不落 DB） */
  test(body = null) {
    return apiPost("/api/v1/admin/ldap-config/test", body);
  },
};
