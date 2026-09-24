import { apiDelete, apiGet, apiPut } from "./api";

export const IpManagementService = {
  /** 取得目前子網設定 */
  getSubnet() {
    return apiGet("/api/v1/ip-management/subnet");
  },

  /**
   * 建立或更新子網設定。
   * 回傳除了子網欄位，還帶 `block_sync`：這次存檔順帶把額外封鎖網段套到各機器的結果
   * （`{ targets, created, updated, skipped, deleted, errors: [{ vmid, error }] }`）。
   * `errors` 不是空的代表設定存好了、但有機器沒套用成功。
   */
  upsertSubnet(body) {
    return apiPut("/api/v1/ip-management/subnet", body);
  },

  /** 刪除子網設定 */
  deleteSubnet() {
    return apiDelete("/api/v1/ip-management/subnet");
  },

  /** 取得 IP 分配清單（後端一次回整份，沒有分頁參數） */
  listAllocations() {
    return apiGet("/api/v1/ip-management/allocations");
  },

  /** 取得子網狀態摘要 */
  getStatus() {
    return apiGet("/api/v1/ip-management/status");
  },
};
