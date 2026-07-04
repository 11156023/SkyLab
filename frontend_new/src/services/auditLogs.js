import { apiGet, apiGetBlob } from "./api";

function buildQuery(params = {}) {
  const q = new URLSearchParams();
  if (params.skip != null) q.set("skip", String(params.skip));
  if (params.limit != null) q.set("limit", String(params.limit));
  if (params.vmid != null && params.vmid !== "") q.set("vmid", String(params.vmid));
  if (params.userId) q.set("user_id", params.userId);
  if (params.action) q.set("action", params.action);
  if (params.startTime) q.set("start_time", params.startTime);
  if (params.endTime) q.set("end_time", params.endTime);
  if (params.ipAddress) q.set("ip_address", params.ipAddress);
  if (params.search) q.set("search", params.search);
  return q.toString();
}

export const AuditLogsService = {
  /** Admin: 全系統操作紀錄 */
  list(params) {
    const qs = buildQuery({ skip: 0, limit: 50, ...params });
    return apiGet(`/api/v1/audit-logs/${qs ? `?${qs}` : ""}`);
  },

  /** 一般使用者: 自己的操作紀錄 */
  listMy(params) {
    const qs = buildQuery({ skip: 0, limit: 50, ...params });
    return apiGet(`/api/v1/audit-logs/my${qs ? `?${qs}` : ""}`);
  },

  /** 統計摘要（total / danger / login_failed / active_users） */
  stats(params = {}) {
    const qs = buildQuery(params);
    return apiGet(`/api/v1/audit-logs/stats${qs ? `?${qs}` : ""}`);
  },

  /** 所有 action 種類（含分類），供篩選下拉用 */
  actions() {
    return apiGet("/api/v1/audit-logs/actions");
  },

  /** 出現過紀錄的使用者列表，供篩選下拉用 */
  users() {
    return apiGet("/api/v1/audit-logs/users");
  },

  /** 匯出 CSV（回傳 Blob） */
  exportCsv(params) {
    const qs = buildQuery(params);
    return apiGetBlob(`/api/v1/audit-logs/export${qs ? `?${qs}` : ""}`);
  },
};
