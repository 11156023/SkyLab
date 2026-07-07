import { apiGet, apiPatch, apiDelete } from "./api";

const BASE = "/api/v1/users/me";

export const AccountService = {
  /** 取得目前登入使用者資料 */
  get() {
    return apiGet(BASE);
  },

  /** 更新個人資料（full_name / email / avatar_url，皆選填，只送有變更的欄位） */
  update(payload) {
    return apiPatch(BASE, payload);
  },

  /** 變更密碼 */
  updatePassword(currentPassword, newPassword) {
    return apiPatch(`${BASE}/password`, {
      current_password: currentPassword,
      new_password: newPassword,
    });
  },

  /** 刪除自己的帳號（無法復原） */
  delete() {
    return apiDelete(BASE);
  },
};
