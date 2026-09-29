import { apiPost } from "./api";

export const VmRequestAvailabilityService = {
  /**
   * 預覽草稿規格的可用時段
   * @param {object} draft  { resource_type, cores, memory, disk_size?, rootfs_size?, ... }
   */
  preview(draft, options = {}) {
    return apiPost("/api/v1/vm-requests/availability", {
      ...draft,
      days:     90,
      timezone: "Asia/Taipei",
      detail:   false,
    }, options);
  },

  windowAvailability(draft) {
    return apiPost("/api/v1/vm-requests/window-availability", draft);
  },
};
