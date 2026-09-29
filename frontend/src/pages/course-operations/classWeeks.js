/* 班級建立精靈（ClassSetupPage）與班級工作頁（ClassWorkspacePage）共用的學生名單與週次規則，
   兩頁說法要一致，改規則只改這裡 */

/** 把老師貼上的名單拆成 email：逗號、分號、空白與換行都算分隔，轉小寫並去重 */
export function parseStudentEmails(value) {
  return [...new Set(String(value).split(/[\s,;]+/).map((email) => email.trim().toLowerCase()).filter(Boolean))];
}

// 後端只把這兩種狀態的週次送到學生端（weekly_task_service.VISIBLE_WEEK_STATUSES）。
export const VISIBLE_WEEK_STATUSES = ["published", "completed"];

export function isWeekVisible(week) {
  return VISIBLE_WEEK_STATUSES.includes(week?.status);
}

export function visibleWeekCount(weeks) {
  return weeks.filter(isWeekVisible).length;
}

/** 週次教材的送出格式：只送已上傳檔案的 id，storage_key 由後端依 id 查回（不接受前端指定） */
export function weekFilesPayload(files) {
  return (files ?? [])
    .filter((file) => file.id)
    .map((file) => ({
      id: String(file.id),
      target_path: file.target_path ?? null,
    }));
}
