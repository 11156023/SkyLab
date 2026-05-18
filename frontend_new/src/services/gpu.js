import { apiDelete, apiGet, apiPost } from "./api";

export const GpuService = {
  /** 取得可用 GPU 選項 */
  listOptions(params) {
    const query = new URLSearchParams();
    if (params?.startAt) query.set("start_at", params.startAt);
    if (params?.endAt)   query.set("end_at",   params.endAt);
    const qs = query.toString();
    return apiGet(`/api/v1/gpu/options${qs ? `?${qs}` : ""}`);
  },

  /** 取得所有 GPU mapping (含使用狀態) */
  listMappings() {
    return apiGet("/api/v1/gpu/mappings");
  },

  /** 取得單一 GPU mapping 詳情 */
  getMapping(mappingId) {
    return apiGet(`/api/v1/gpu/mappings/${encodeURIComponent(mappingId)}`);
  },

  /** 新增 mapping */
  createMapping(body) {
    return apiPost("/api/v1/gpu/mappings", body);
  },

  /** 刪除 mapping */
  deleteMapping(mappingId) {
    return apiDelete(`/api/v1/gpu/mappings/${encodeURIComponent(mappingId)}`);
  },
};
