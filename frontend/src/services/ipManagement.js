import { apiDelete, apiGet, apiPut } from "./api";

/**
 * 子網設定存檔／刪除成功後在 window 上發出的事件，SubnetBanner 收到會立即重查。
 * 字串必須與 components/SubnetBanner 監聽的字面值一致。
 */
export const SUBNET_CHANGED_EVENT = "skylab:subnet-changed";

/** 只在請求成功後通知；失敗時錯誤原樣往外拋。 */
function notifySubnetChanged(res) {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(SUBNET_CHANGED_EVENT));
  return res;
}

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
    return apiPut("/api/v1/ip-management/subnet", body).then(notifySubnetChanged);
  },

  /** 刪除子網設定 */
  deleteSubnet() {
    return apiDelete("/api/v1/ip-management/subnet").then(notifySubnetChanged);
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
