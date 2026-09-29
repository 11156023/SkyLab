import { apiGet, apiPost } from "./api";

export const BatchProvisionService = {
  /** Admin: 列出待審核批次 */
  listPending() {
    return apiGet("/api/v1/batch-provision/pending");
  },

  /** Admin: 列出批次（不限狀態），審核頁的分頁需要看得到已審核的批次 */
  listAll(params = {}) {
    const query = new URLSearchParams();
    if (params.status && params.status !== "all") query.set("status", params.status);
    query.set("limit", String(params.limit ?? 100));
    return apiGet(`/api/v1/batch-provision/?${query.toString()}`);
  },

  /** Admin: 核准 / 駁回 */
  review(jobId, body) {
    return apiPost(`/api/v1/batch-provision/${jobId}/review`, body);
  },

  /** Admin: 對同一班級目前的所有節點做一次一致審核 */
  reviewClass(classId, body) {
    return apiPost(`/api/v1/batch-provision/class/${classId}/review`, body);
  },

  /** 週期排程批次：預覽未來 count 個開機時段（回傳 { windows: [start, end][] }） */
  getRecurrencePreview(jobId, count = 5) {
    return apiGet(`/api/v1/batch-provision/${jobId}/recurrence-preview?count=${count}`);
  },
};
