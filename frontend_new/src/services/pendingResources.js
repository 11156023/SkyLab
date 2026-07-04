import { VmRequestsService } from "./vmRequests";

/** 建立中狀態變化頻率高，5 秒輪詢一次 */
export const PENDING_POLL_INTERVAL = 5000;

/**
 * 是否為「建立中」、應在資源列表預先顯示為 placeholder 的申請。
 * 開通成功後 VMRequest.status 仍停留在 approved（後端只把 vmid 寫回），
 * 所以 approved 必須同時看 vmid：vmid 已存在代表機器已開出來，不再是 placeholder。
 */
export function isCreatingRequest(req) {
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
    .map((r) => `${r.id}:${r.status}:${r.vmid ?? ""}:${r.migration_status ?? ""}`)
    .sort()
    .join(",");
}
