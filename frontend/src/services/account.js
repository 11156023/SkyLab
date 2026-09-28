import { apiGet, apiPatch, apiDelete, apiPost, apiPostMultipart } from "./api";

const BASE = "/api/v1/users/me";

export const AccountService = {
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

  /** 首次登入引導精靈走完或略過：之後登入不再顯示，回傳更新後的使用者 */
  completeOnboarding() {
    return apiPost(`${BASE}/onboarding/complete`, {});
  },

  /**
   * 登入後的服務檢查：{ ok, detailed, checks: [{ key, status, components? }] }。
   * 非管理員只有 key／status；refresh 只對管理員有效（略過後端快取重新探測）。
   */
  preflight({ refresh = false } = {}) {
    return apiGet(`${BASE}/preflight${refresh ? "?refresh=true" : ""}`);
  },

  /*
   * 以下是登入頁（未登入或剛登入）用到的帳號端點。放在這裡而不是 services/auth.js：
   * api.js 會 import ./auth 的 AuthStorage，auth.js 若再 import api.js 就成了循環相依。
   */

  /** 忘記密碼：寄出重設密碼信（email 放在路徑上，需編碼） */
  requestPasswordRecovery(email) {
    return apiPost(`/api/v1/password-recovery/${encodeURIComponent(email)}`, null);
  },

  /** 以重設信中的 token 設定新密碼 */
  resetPassword(token, newPassword) {
    return apiPost("/api/v1/reset-password/", { new_password: newPassword, token });
  },

  /** 自行註冊帳號 */
  signup({ email, full_name, password }) {
    return apiPost("/api/v1/users/signup", { email, full_name, password });
  },

  /** 核准桌面用戶端的裝置授權碼（需已登入） */
  approveDesktopDevice(deviceCode) {
    return apiPost("/api/v1/desktop-client/auth/approve", { device_code: deviceCode });
  },
};
