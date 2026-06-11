import { apiGet, apiPost } from "./api";

export const VmRequestAvailabilityService = {
  /**
   * 預覽草稿規格的可用時段
   * @param {object} draft  { resource_type, cores, memory, disk_size?, rootfs_size?, ... }
   */
  preview(draft) {
    return apiPost("/api/v1/vm-requests/availability", {
      ...draft,
      days:     7,
      timezone: "Asia/Taipei",
    });
  },

  windowAvailability(draft) {
    return apiPost("/api/v1/vm-requests/window-availability", draft);
  },

  /**
   * 取得某筆申請的可用時段
   * @param {string} requestId
   */
  getByRequestId(requestId) {
    return apiGet(
      `/api/v1/vm-requests/${requestId}/availability?days=7&timezone=Asia%2FTaipei`,
    );
  },
};
