/**
 * 操作紀錄的動作代碼（後端 AuditAction）怎麼顯示：正式分頁與導覽示範頁共用。
 * 獨立成檔是因為元件檔只輸出元件，熱更新才不會壞。
 */

/** 依動作類型決定 badge 色系（僅使用四種語意色） */
export function actionBadgeClass(action) {
  if (action.includes("create")) return "badge_success";
  if (action.includes("delete")) return "badge_danger";
  return "badge_info";
}

/** 動作代碼的顯示文字；只翻了機器本身會出現的動作，其餘（或日後新增的）退回原始代碼 */
export function actionLabel(action, t) {
  return t(`AuditLogsTab.action.${action}`, { defaultValue: action });
}
