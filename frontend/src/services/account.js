import { apiGet, apiPatch, apiDelete, apiPost, apiPostMultipart } from "./api";

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

  /** 上傳頭像圖片（Blob / File），後端存檔並回傳更新後的使用者 */
  uploadAvatar(imageBlob) {
    const form = new FormData();
    form.append("file", imageBlob, "avatar.jpg");
    return apiPostMultipart(`${BASE}/avatar`, form);
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

  /** 兩步驟驗證：產生金鑰與 otpauth URI（待確認，尚未啟用） */
  setupTotp() {
    return apiPost(`${BASE}/totp/setup`, {});
  },

  /** 兩步驟驗證：用 Authenticator 的驗證碼確認綁定，正式啟用 */
  confirmTotp(code) {
    return apiPost(`${BASE}/totp/confirm`, { code });
  },

  /** 兩步驟驗證：停用（需目前有效的驗證碼） */
  disableTotp(code) {
    return apiPost(`${BASE}/totp/disable`, { code });
  },
};
