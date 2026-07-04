import { apiGet, apiPost } from "./api";

export const SpecChangeRequestsService = {
  /** 送出規格變更申請（body: { vmid, change_type, reason, requested_cpu?, requested_memory? }） */
  create(body) {
    return apiPost("/api/v1/spec-change-requests/", body);
  },

  listAll(params = {}) {
    const query = new URLSearchParams();
    if (params.status) query.set("status", params.status);
    if (params.vmid) query.set("vmid", String(params.vmid));
    query.set("limit", String(params.limit ?? 100));
    if (params.skip) query.set("skip", String(params.skip));
    const qs = query.toString();
    return apiGet(`/api/v1/spec-change-requests/${qs ? `?${qs}` : ""}`);
  },

  review(requestId, body) {
    return apiPost(`/api/v1/spec-change-requests/${requestId}/review`, body);
  },
};
