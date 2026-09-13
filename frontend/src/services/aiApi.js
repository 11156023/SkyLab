import { apiDelete, apiGet, apiPatch, apiPost } from "./api";

const BASE = "/api/v1/ai-api";

export const AiApiService = {
  /* ── User 端: 申請 ── */
  createRequest(body) {
    return apiPost(`${BASE}/requests`, body);
  },
  listMyRequests() {
    return apiGet(`${BASE}/requests/my`);
  },
  listMyCredentials() {
    return apiGet(`${BASE}/credentials/my`);
  },

  /* ── Admin: 審核申請 ── */
  listAllRequests() {
    return apiGet(`${BASE}/requests`);
  },
  getRequest(requestId) {
    return apiGet(`${BASE}/requests/${requestId}`);
  },
  reviewRequest(requestId, body) {
    return apiPost(`${BASE}/requests/${requestId}/review`, body);
  },

  /* ── Admin: 憑證管理 ── */
  listAllCredentials({ status, user_email, query, user_role = [], created_after, skip = 0, limit = 100 } = {}) {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (user_email) params.set("user_email", user_email);
    if (query) params.set("query", query);
    for (const role of user_role) params.append("user_role", role);
    if (created_after) params.set("created_after", created_after);
    params.set("skip", String(skip));
    params.set("limit", String(limit));
    return apiGet(`${BASE}/credentials?${params.toString()}`);
  },
  rotateCredential(credentialId) {
    return apiPost(`${BASE}/credentials/${credentialId}/rotate`, {});
  },
  revokeCredential(credentialId) {
    return apiDelete(`${BASE}/credentials/${credentialId}`);
  },
  updateCredential(credentialId, body) {
    return apiPatch(`${BASE}/credentials/${credentialId}`, body);
  },

  /* ── User 端: 我的用量（統一兩種計算路由） ── */
  getMyUsage({ start_date, end_date }) {
    const q = new URLSearchParams();
    if (start_date) q.set("start_date", start_date);
    if (end_date) q.set("end_date", end_date);
    const qs = q.toString();
    return apiGet(`${BASE}/usage/my${qs ? `?${qs}` : ""}`);
  },
  getMyUsageRecords({ start_date, end_date, skip = 0, limit = 50 }) {
    const q = new URLSearchParams();
    if (start_date) q.set("start_date", start_date);
    if (end_date) q.set("end_date", end_date);
    q.set("skip", String(skip));
    q.set("limit", String(limit));
    return apiGet(`${BASE}/usage/records/my?${q.toString()}`);
  },
};
