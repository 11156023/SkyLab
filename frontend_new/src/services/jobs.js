import { apiGet } from "./api";

export const JobsService = {
  /** 列出統一背景任務,支援篩選 */
  list(params) {
    const q = new URLSearchParams();
    if (params?.kinds?.length)    q.set("kinds",        params.kinds.join(","));
    if (params?.statuses?.length) q.set("statuses",     params.statuses.join(","));
    if (params?.activeOnly)       q.set("active_only",  "true");
    if (params?.limit != null)    q.set("limit",        String(params.limit));
    if (params?.offset != null)   q.set("offset",       String(params.offset));
    if (params?.historyDays)      q.set("history_days", String(params.historyDays));
    const qs = q.toString();
    return apiGet(`/api/v1/jobs/${qs ? `?${qs}` : ""}`);
  },

  /** 最近 N 筆 (Banner popover 用) */
  recent(limit = 5) {
    return apiGet(`/api/v1/jobs/recent?limit=${limit}`);
  },

  /** 取得單一 Job 詳情 (id 格式: <kind>:<source_id>) */
  detail(jobId) {
    return apiGet(`/api/v1/jobs/${encodeURIComponent(jobId)}`);
  },
};
