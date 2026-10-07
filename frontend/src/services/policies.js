/**
 * 平台政策 API：Linux VM 登入帳號（cloud-init ciuser）命名規則。
 * 規則唯一來源在後端 backend/config/username_policy.yaml，前端不另寫死一份。
 */

import { apiGet, apiPost } from "./api";

export const PoliciesService = {
  /** 規則（regex、長度、保留字、平台保留名稱、會提示警告的預設帳號） */
  getUsernameRules(options = {}) {
    return apiGet("/api/v1/policies/username", options);
  },

  /** 檢查帳號 → { ok, violations: [{ code, message, severity }] } */
  checkUsername(username, options = {}) {
    return apiPost("/api/v1/policies/username/check", { username }, options);
  },
};
