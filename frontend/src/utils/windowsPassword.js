/**
 * Windows 機器登入密碼的複雜度規則（申請表單、重設機器密碼）。
 *
 * Windows 預設啟用密碼複雜度，cloudbase-init 寫入不合規的密碼會被系統拒絕，
 * 機器開得起來卻登不進去。與後端 `backend/app/utils/login_password.py` 的
 * `windows_password_issues` 是同一套規則，改這裡要一起改；後端才是把關的一方。
 * Linux 機器不套這裡。
 */

/** cloudbase-init 設定檔固定的 Windows 登入帳號 */
export const WINDOWS_LOGIN_USERNAME = "Admin";
export const WINDOWS_PASSWORD_MIN_LENGTH = 8;
const WINDOWS_MIN_CATEGORIES = 3;

/* 缺字元種類時建議補的四類；key 對應 common 語系的 PasswordRules.windowsType.<key> */
const SUGGESTED_CATEGORIES = ["uppercase", "lowercase", "digit", "symbol"];

function charCategory(ch) {
  if (ch !== ch.toLowerCase()) return "uppercase";
  if (ch !== ch.toUpperCase()) return "lowercase";
  if (/\p{Nd}/u.test(ch)) return "digit";
  /* 沒有大小寫之分的字母（中日文等），Windows 另算一類 */
  if (/\p{L}/u.test(ch)) return "letter";
  return "symbol";
}

/** 還差幾類字元（`need`），以及還沒用到、可以補的類別（`missing`）。 */
export function windowsCategoryShortfall(password) {
  const present = new Set([...(password ?? "")].map(charCategory));
  return {
    need: Math.max(0, WINDOWS_MIN_CATEGORIES - present.size),
    missing: SUGGESTED_CATEGORIES.filter((category) => !present.has(category)),
  };
}

/* 順序即檢查清單的顯示順序；key 對應 common 語系的 PasswordRules.<key> */
export const WINDOWS_PASSWORD_RULES = [
  {
    key: "length",
    params: { min: WINDOWS_PASSWORD_MIN_LENGTH },
    test: (password) => password.length >= WINDOWS_PASSWORD_MIN_LENGTH,
  },
  {
    key: "windowsCategories",
    test: (password) => windowsCategoryShortfall(password).need === 0,
    /* 不先列出「四類任選三類」：還沒輸入就不顯示，缺幾類就只提示可以補哪幾類 */
    describe: (password) => {
      if (!password) return null;
      const { need, missing } = windowsCategoryShortfall(password);
      if (need === 0) return { key: "windowsCategoriesMet" };
      return {
        key: need === 1 ? "windowsAddOneType" : "windowsAddTwoTypes",
        options: missing.map((category) => `windowsType.${category}`),
      };
    },
  },
  {
    key: "windowsUsername",
    params: { username: WINDOWS_LOGIN_USERNAME },
    test: (password) => !password.toLowerCase().includes(WINDOWS_LOGIN_USERNAME.toLowerCase()),
  },
];

/** 未滿足的規則 key（順序固定）；空陣列代表通過。 */
export function windowsPasswordIssues(password) {
  const value = password ?? "";
  return WINDOWS_PASSWORD_RULES.filter((rule) => !rule.test(value)).map((rule) => rule.key);
}
