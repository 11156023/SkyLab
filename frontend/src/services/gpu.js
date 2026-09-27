import { apiDelete, apiGet } from "./api";

export const GpuService = {
  /** 取得可用 GPU 選項（node = 範本所在節點，只回同一 PVE 叢集的 GPU） */
  listOptions(params) {
    const query = new URLSearchParams();
    if (params?.startAt) query.set("start_at", params.startAt);
    if (params?.endAt)   query.set("end_at",   params.endAt);
    if (params?.node)    query.set("node",     params.node);
    const qs = query.toString();
    return apiGet(`/api/v1/gpu/options${qs ? `?${qs}` : ""}`);
  },

  /** 取得所有 GPU mapping (含使用狀態) */
  listMappings() {
    return apiGet("/api/v1/gpu/mappings");
  },

  /** 刪除 mapping */
  deleteMapping(mappingId) {
    return apiDelete(`/api/v1/gpu/mappings/${encodeURIComponent(mappingId)}`);
  },
};
