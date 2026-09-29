import { apiGet, apiPost } from "./api";
import { fetchAllPages } from "./pagedList";

/** GET /vm-requests/ 的 limit 上限（後端 le=100） */
const VM_REQUEST_PAGE_SIZE = 100;

export const VmRequestsService = {
  list() {
    return apiGet("/api/v1/vm-requests/my");
  },

  /**
   * 管理員看全部申請（新到舊）。後端單次上限 100 筆，limit 超過時自動用 skip 分頁補齊，
   * 審核佇列要完整時請帶較大的 limit（例如待審分頁），否則比最新 100 筆舊的會看不到。
   */
  listAll(status, { limit = VM_REQUEST_PAGE_SIZE, skip = 0 } = {}) {
    return fetchAllPages(
      (page) => {
        const query = new URLSearchParams();
        if (status && status !== "all") query.set("status", status);
        query.set("limit", String(page.limit));
        if (page.skip) query.set("skip", String(page.skip));
        return apiGet(`/api/v1/vm-requests/?${query.toString()}`);
      },
      { limit, skip, pageSize: VM_REQUEST_PAGE_SIZE },
    );
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
