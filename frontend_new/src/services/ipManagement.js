import { apiDelete, apiGet, apiPut } from "./api";

export const IpManagementService = {
  /** 取得目前子網設定 */
  getSubnet() {
    return apiGet("/api/v1/ip-management/subnet");
  },

  /** 建立或更新子網設定 */
  upsertSubnet(body) {
    return apiPut("/api/v1/ip-management/subnet", body);
  },

  /** 刪除子網設定 */
  deleteSubnet() {
    return apiDelete("/api/v1/ip-management/subnet");
  },

  /** 取得 IP 分配清單 */
  listAllocations(params) {
    const q = new URLSearchParams();
    if (params?.skip != null)  q.set("skip",  String(params.skip));
    if (params?.limit != null) q.set("limit", String(params.limit));
    const qs = q.toString();
    return apiGet(`/api/v1/ip-management/allocations${qs ? `?${qs}` : ""}`);
  },

  /** 取得子網狀態摘要 */
  getStatus() {
    return apiGet("/api/v1/ip-management/status");
  },
};
