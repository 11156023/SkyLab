import { apiGet, apiPost } from "./api";

export const MigrationJobsService = {
  /** 列出遷移任務，可依狀態篩選 */
  list(params = {}) {
    const q = new URLSearchParams();
    if (params.status) q.set("status", params.status);
    q.set("skip", String(params.skip ?? 0));
    q.set("limit", String(params.limit ?? 50));
    return apiGet(`/api/v1/migration-jobs/?${q.toString()}`);
  },

  /** 統計摘要（total_jobs / by_status / avg_duration_seconds / success_rate） */
  stats() {
    return apiGet("/api/v1/migration-jobs/stats");
  },

  /** 單一任務詳情 */
  get(jobId) {
    return apiGet(`/api/v1/migration-jobs/${jobId}`);
  },

  /** 重試失敗任務 */
  retry(jobId) {
    return apiPost(`/api/v1/migration-jobs/${jobId}/retry`);
  },

  /** 取消排隊中的任務 */
  cancel(jobId) {
    return apiPost(`/api/v1/migration-jobs/${jobId}/cancel`);
  },
};
