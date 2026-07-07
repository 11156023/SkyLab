import { apiGet } from "./api";

export const DeletionRequestsService = {
  listAll(params = {}) {
    const query = new URLSearchParams();
    if (params.status) query.set("status", params.status);
    query.set("limit", String(params.limit ?? 100));
    if (params.skip) query.set("skip", String(params.skip));
    const qs = query.toString();
    return apiGet(`/api/v1/deletion-requests/${qs ? `?${qs}` : ""}`);
  },
};
