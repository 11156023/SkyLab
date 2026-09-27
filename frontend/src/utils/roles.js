/**
 * roles.js
 * 前端共用的身分判斷。只決定畫面上顯示什麼、路由導向哪裡；實際權限一律由後端把關。
 * 規則要改時只改這裡，不要在頁面各自寫一份 `is_superuser || role === "admin"`。
 */

/** 管理員（含超級使用者） */
export function isAdminUser(user) {
  return Boolean(user?.is_superuser || user?.role === "admin");
}

/** 可以教課的身分：管理員或老師（看得到 VMID、課程／班級管理等） */
export function canTeachUser(user) {
  return isAdminUser(user) || user?.role === "teacher";
}
