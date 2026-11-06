/**
 * 機器登入密碼的規則（資源詳情「重設登入密碼」自己指定密碼時）。
 *
 * 與後端 `backend/app/schemas/resource_settings.py` 的 `PasswordResetRequest` 是同一套
 * （8–64 字、不含空白），Windows 機器再加上 utils/windowsPassword 的複雜度規則；
 * 後端才是把關的一方，這裡只是讓使用者在送出前就看到哪一項沒滿足。
 * 規則的形狀與 components/PasswordRules 相同，可以直接交給它顯示成清單。
 */

import { WINDOWS_PASSWORD_RULES } from "./windowsPassword";

export const MACHINE_PASSWORD_MIN_LENGTH = 8;
export const MACHINE_PASSWORD_MAX_LENGTH = 64;

const LENGTH_RULE = {
  key: "length",
  params: { min: MACHINE_PASSWORD_MIN_LENGTH },
  test: (password) =>
    password.length >= MACHINE_PASSWORD_MIN_LENGTH && password.length <= MACHINE_PASSWORD_MAX_LENGTH,
};

/* 還沒輸入時不算符合：清單一打開就有一條綠勾，像是已經填了什麼 */
const NO_WHITESPACE_RULE = {
  key: "noWhitespace",
  test: (password) => password.length > 0 && !/\s/.test(password),
};

const LINUX_RULES = [LENGTH_RULE, NO_WHITESPACE_RULE];
/* Windows 的長度規則與上面同一條，不重複列 */
const WINDOWS_RULES = [
  LENGTH_RULE,
  NO_WHITESPACE_RULE,
  ...WINDOWS_PASSWORD_RULES.filter((rule) => rule.key !== "length"),
];

/** 這台機器適用的規則（順序即清單的顯示順序） */
export function machinePasswordRules({ windows = false } = {}) {
  return windows ? WINDOWS_RULES : LINUX_RULES;
}

/** 未滿足的規則 key（順序固定）；空陣列代表通過。 */
export function machinePasswordIssues(password, { windows = false } = {}) {
  const value = password ?? "";
  return machinePasswordRules({ windows }).filter((rule) => !rule.test(value)).map((rule) => rule.key);
}
