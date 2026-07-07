import { apiGet, apiPost } from "./api";

export const VmRequestsService = {
  list() {
    return apiGet("/api/v1/vm-requests/my");
  },

  listAll(status) {
    const query = new URLSearchParams();
    if (status && status !== "all") query.set("status", status);
    query.set("limit", "100");
    const qs = query.toString();
    return apiGet(`/api/v1/vm-requests/${qs ? `?${qs}` : ""}`);
  },

  getReviewContext(requestId) {
    return apiGet(`/api/v1/vm-requests/${requestId}/review-context`);
  },

  create(body) {
    return apiPost("/api/v1/vm-requests/", body);
  },

  /** VM vs LXC 自動判斷（規則引擎；advisor 停用時後端回 400） */
  advise(body) {
    return apiPost("/api/v1/vm-requests/advise", body);
  },

  review(requestId, body) {
    return apiPost(`/api/v1/vm-requests/${requestId}/review`, body);
  },

  cancel(requestId) {
    return apiPost(`/api/v1/vm-requests/${requestId}/cancel`, {});
  },

  retry(requestId) {
    return apiPost(`/api/v1/vm-requests/${requestId}/retry`, {});
  },
};
