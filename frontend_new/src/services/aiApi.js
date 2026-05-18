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
  listAllCredentials() {
    return apiGet(`${BASE}/credentials`);
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
};
