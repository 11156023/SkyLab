/* 範本頁（手冊附件清單、建立／編輯表單）共用的顯示格式 */

/** 附件大小：≥1 MB 顯示一位小數 MB，≥1 KB 取整數 KB，其餘 B */
export function formatBytes(bytes) {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}
