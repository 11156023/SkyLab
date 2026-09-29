import { VmRequestsService } from "./vmRequests";

/** 建立中狀態變化頻率高，5 秒輪詢一次 */
export const PENDING_POLL_INTERVAL = 5000;
/** 沒有建立中的申請時放慢輪詢（新申請送出後頁面會主動刷新） */
export const PENDING_IDLE_POLL_INTERVAL = 30_000;

/**
 * 後端在使用者刪機／孤兒清理／轉範本時寫進 review_comment、resource_warning、
 * provisioning_error 的系統標記（對應 backend app/domain/resource_markers.py RESOURCE_DELETED_MARKERS）。
 * 帶標記的 approved 申請單只保留做稽核，任何列表都不該再把它當成活單。
 */
export const CONSUMED_REQUEST_MARKERS = Object.freeze([
  "Resource deleted by user",
  "Resource deleted (orphan DB cleanup)",
  "Resource converted to template",
]);

/** 申請單對應的機器已被使用者刪除／清理／轉為範本 */
export function isConsumedRequest(req) {
  if (!req) return false;
  return (
    CONSUMED_REQUEST_MARKERS.includes(req.review_comment) ||
    CONSUMED_REQUEST_MARKERS.includes(req.resource_warning) ||
    CONSUMED_REQUEST_MARKERS.includes(req.provisioning_error)
  );
}

/**
 * 是否為「建立中」、應在資源列表預先顯示為 placeholder 的申請。
 * 開通成功後 VMRequest.status 仍停留在 approved（後端只把 vmid 寫回），
 * 所以 approved 必須同時看 vmid：vmid 已存在代表機器已開出來，不再是 placeholder。
 * 已消耗的申請單（機器被刪掉）即使 vmid 為空也不是建立中，不能再顯示成 placeholder。
 */
export function isCreatingRequest(req) {
  if (isConsumedRequest(req)) return false;
  if (req.status === "pending") return true;
  return req.status === "approved" && req.vmid == null;
}

/** 取得目前使用者尚未開通完成的 VM Request（資源列表 placeholder 用） */
export async function fetchPendingResources() {
  const res = await VmRequestsService.list();
  return (res?.data ?? []).filter(isCreatingRequest);
}

/** 取消尚未進入開通階段的 VM Request */
export function cancelVmRequest(requestId) {
  return VmRequestsService.cancel(requestId);
}

/**
 * 輪詢用簽章：任一申請的階段變化（審核通過、開通完成、開通失敗、取消）
 * 都會改變字串，用來判斷是否需要同步刷新資源列表。
 */
export function pendingSignature(items) {
  return items
    .map((r) => `${r.id}:${r.status}:${r.vmid ?? ""}:${r.provisioning_status ?? ""}`)
    .sort()
    .join(",");
}
