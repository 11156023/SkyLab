import { apiDelete, apiGet, apiPatch, apiPost } from "./api";

const BASE = "/api/v1/ai-api";

function browserTimeZone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  } catch {
    return "";
  }
}

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
  getCredential(credentialId, options = {}) {
    return apiGet(`${BASE}/credentials/${credentialId}`, options);
  },

  /* ── Admin: 審核申請 ── */
  /** 狀態／分頁篩選交給後端（limit 上限 100，count 為篩選後總數）；不帶參數時維持原本的 URL。 */
  listAllRequests({ status, skip, limit } = {}) {
    const params = new URLSearchParams();
    if (status && status !== "all") params.set("status", status);
    if (skip != null) params.set("skip", String(skip));
    if (limit != null) params.set("limit", String(limit));
    const qs = params.toString();
    return apiGet(`${BASE}/requests${qs ? `?${qs}` : ""}`);
  },
  reviewRequest(requestId, body) {
    return apiPost(`${BASE}/requests/${requestId}/review`, body);
  },
  bulkRejectRequests(requestIds, reviewComment) {
    return apiPost(`${BASE}/requests/bulk-reject`, {
      request_ids: requestIds,
      review_comment: reviewComment,
    });
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
  deleteCredential(credentialId) {
    return apiDelete(`${BASE}/credentials/${credentialId}`);
  },
  updateCredential(credentialId, body) {
    return apiPatch(`${BASE}/credentials/${credentialId}`, body);
  },

  /* ── User 端: 我的用量（只計算申請金鑰的 API 呼叫） ── */
  getMyUsage({ start_date, end_date }) {
    const q = new URLSearchParams();
    if (start_date) q.set("start_date", start_date);
    if (end_date) q.set("end_date", end_date);
    /* 讓後端依瀏覽器時區切分每日用量，否則跨午夜的呼叫會被算到 UTC 的日期 */
    const tz = browserTimeZone();
    if (tz) q.set("tz", tz);
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
