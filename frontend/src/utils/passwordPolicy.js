/**
 * 平台帳號密碼的複雜度規則（註冊、重設、變更密碼、管理員新增／編輯使用者、初始化精靈共用）。
 *
 * 與後端 `backend/app/services/user/password_policy.py` 是同一套規則，改這裡要一起改；
 * 後端才是把關的一方，這裡只是讓使用者在送出前就看到哪一項沒滿足。
 * 機器登入密碼（申請表單、範本克隆、重設機器密碼）是另一套，不套這裡。
 */

export const PASSWORD_MIN_LENGTH = 8;
export const PASSWORD_MAX_LENGTH = 128;

/* 順序即檢查清單的顯示順序；key 對應 common 語系的 PasswordRules.<key> */
export const PASSWORD_RULES = [
  {
    key: "length",
    test: (password) =>
      password.length >= PASSWORD_MIN_LENGTH && password.length <= PASSWORD_MAX_LENGTH,
  },
  { key: "uppercase", test: (password) => /[A-Z]/.test(password) },
  { key: "lowercase", test: (password) => /[a-z]/.test(password) },
  { key: "digit", test: (password) => /[0-9]/.test(password) },
  /* 英數與空白以外的字元都算特殊符號（含全形標點與非英文字母） */
  { key: "symbol", test: (password) => /[^A-Za-z0-9\s]/.test(password) },
];

/** 未滿足的規則 key（順序固定）；空陣列代表通過。 */
export function passwordIssues(password) {
  const value = password ?? "";
  return PASSWORD_RULES.filter((rule) => !rule.test(value)).map((rule) => rule.key);
}

export function isPasswordStrong(password) {
  return passwordIssues(password).length === 0;
}
