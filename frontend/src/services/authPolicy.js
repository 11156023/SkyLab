import { apiGet, apiPut } from "./api";

/**
 * 登入安全政策：
 * - get()：所有登入者可讀（前端據 totp_required 顯示提示）
 * - update()：管理員開關「強制所有使用者啟用兩步驟驗證」
 */
export const AuthPolicyService = {
  get() {
    return apiGet("/api/v1/auth-policy");
  },

  update({ totp_required }) {
    return apiPut("/api/v1/admin/auth-policy", { totp_required });
  },
};
