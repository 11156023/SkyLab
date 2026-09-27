/* 申請頁共用的身分判斷；只影響畫面上顯示什麼，實際權限由後端把關。 */

/** 管理員（含超級使用者）：可看原始開通錯誤 log。 */
export function isAdminUser(user) {
  return Boolean(user?.is_superuser || user?.role === "admin");
}

/** 管理員或老師：看得到 VMID、可選系統範本與排程模式。 */
export function isStaffUser(user) {
  return isAdminUser(user) || user?.role === "teacher";
}
