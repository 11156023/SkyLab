import { apiDelete, apiGet, apiPost, apiPut } from "./api";

/** 資源配額：admin 管理群組/個人配額；所有登入者可查自己的用量。 */
export const QuotasService = {
  /** 自己的配額與目前用量 */
  getMyUsage() {
    return apiGet("/api/v1/quotas/my-usage");
  },

  /** 全部配額（admin） */
  list() {
    return apiGet("/api/v1/quotas");
  },

  /** 建立配額（body: { scope, group_id|user_id, max_cpu_cores, max_memory_mb, max_disk_gb, max_instances }） */
  create(body) {
    return apiPost("/api/v1/quotas", body);
  },

  /** 更新配額（partial） */
  update(quotaId, body) {
    return apiPut(`/api/v1/quotas/${quotaId}`, body);
  },

  /** 刪除配額 */
  remove(quotaId) {
    return apiDelete(`/api/v1/quotas/${quotaId}`);
  },
};
