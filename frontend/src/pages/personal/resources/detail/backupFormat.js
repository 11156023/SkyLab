/* 備份分頁的顯示格式與任務判斷 */

const GB = 1024 ** 3;
const MB = 1024 ** 2;

/** 備份大小：≥1 GB 顯示一位小數 GB，其餘取整數 MB（不足 1 MB 顯示 "< 1 MB"）；沒有值顯示 "—" */
export function formatBackupSize(bytes) {
  if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes >= GB) return `${(bytes / GB).toFixed(1)} GB`;
  if (bytes >= MB) return `${Math.round(bytes / MB)} MB`;
  return "< 1 MB";
}

const BACKUP_JOB_KINDS = new Set(["resource_backup", "resource_restore"]);

/** 執行中任務清單裡，這台機器的備份／還原任務（沒有回 null） */
export function findActiveBackupJob(jobs, vmid) {
  return (jobs ?? []).find(
    (job) => BACKUP_JOB_KINDS.has(job?.kind) && Number(job?.meta?.vmid) === Number(vmid),
  ) ?? null;
}
