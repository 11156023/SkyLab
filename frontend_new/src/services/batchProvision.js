import { apiGet, apiPost } from "./api";

export const BatchProvisionService = {
  /** Admin: 列出待審核批次 */
  listPending() {
    return apiGet("/api/v1/batch-provision/pending");
  },

  /** 取得單一批次任務狀態 */
  getStatus(jobId) {
    return apiGet(`/api/v1/batch-provision/${jobId}/status`);
  },

  /** 列出某 Group 的所有批次 */
  listByGroup(groupId) {
    return apiGet(`/api/v1/batch-provision/group/${groupId}`);
  },

  /** Admin: 核准 / 駁回 */
  review(jobId, body) {
    return apiPost(`/api/v1/batch-provision/${jobId}/review`, body);
  },

  /** 週期排程批次：預覽未來 count 個開機時段（回傳 { windows: [start, end][] }） */
  getRecurrencePreview(jobId, count = 5) {
    return apiGet(`/api/v1/batch-provision/${jobId}/recurrence-preview?count=${count}`);
  },

  /** 教師: 送出批次申請 */
  submit(groupId, body) {
    return apiPost(`/api/v1/batch-provision/${groupId}`, body);
  },
};
