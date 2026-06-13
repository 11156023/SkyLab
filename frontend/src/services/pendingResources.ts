import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect } from "react"

import {
  type ResourcePublic,
  type VMRequestPublic,
  type VMRequestStatus,
  VmRequestsService,
} from "@/client"
import { queryKeys } from "@/lib/queryKeys"
import type { DeletingMeta } from "@/services/deletingResources"
import { connectJobsWebSocket } from "@/services/jobs"

/** 視為「創建中」、應在資源列表預先顯示為 placeholder 的 VM Request 狀態。 */
export const CREATING_STATUSES: ReadonlySet<VMRequestStatus> = new Set([
  "pending",
  "approved",
])

/** 用於資源列表 row 的 placeholder 標記資料。 */
export interface CreatingMeta {
  request_id: string
  status: VMRequestStatus
  hostname: string
  resource_type: string
  cores: number
  memory: number
  user_id: string
  user_email: string | null
  user_full_name: string | null
  created_at: string | null
  /** 排程開機時間；若已過且仍在 pending/approved，視為超時。 */
  start_at: string | null
}

/** Resources DataTable row：原本的 ResourcePublic，再多 `_creating` / `_deleting` 標記。 */
export type ResourceRow = ResourcePublic & {
  _creating?: CreatingMeta
  _deleting?: DeletingMeta
}

function toCreatingMeta(req: VMRequestPublic): CreatingMeta {
  return {
    request_id: req.id,
    status: req.status,
    hostname: req.hostname,
    resource_type: req.resource_type,
    cores: req.cores,
    memory: req.memory,
    user_id: req.user_id,
    user_email: req.user_email ?? null,
    user_full_name: req.user_full_name ?? null,
    created_at: req.created_at ?? null,
    start_at: req.start_at ?? null,
  }
}

/** 把 VMRequest 轉成 fake ResourceRow（用負 vmid 確保不會與真實 VM 衝突）。 */
export function pendingToFakeRow(
  req: VMRequestPublic,
  idx: number,
): ResourceRow {
  return {
    vmid: -(idx + 1), // 負數 placeholder vmid
    name: req.hostname,
    status: "provisioning",
    node: req.assigned_node ?? req.desired_node ?? "—",
    type: req.resource_type === "lxc" ? "lxc" : "qemu",
    environment_type: req.environment_type ?? null,
    os_info: req.os_info ?? null,
    expiry_date: req.expiry_date ?? null,
    ip_address: null,
    ssh_public_key: null,
    service_template_slug: null,
    cpu: null,
    maxcpu: req.cores,
    mem: null,
    maxmem: req.memory ? req.memory * 1024 * 1024 : null,
    uptime: null,
    _creating: toCreatingMeta(req),
  }
}

interface UsePendingResourcesOpts {
  isAdmin: boolean
  enabled?: boolean
}

/**
 * Fetch 目前 user（一般使用者）或全部（admin）尚未 provisioned 完成的 VM Request，
 * 過濾出 pending / approved 兩種尚未完成供應的申請狀態。
 */
export function usePendingResources({
  isAdmin,
  enabled = true,
}: UsePendingResourcesOpts) {
  return useQuery({
    queryKey: ["pending-resources", isAdmin ? "all" : "my"],
    queryFn: async () => {
      const resp = isAdmin
        ? await VmRequestsService.listAllVmRequests({})
        : await VmRequestsService.listMyVmRequests({})
      const items: VMRequestPublic[] = resp.data ?? []
      return items.filter((r) => CREATING_STATUSES.has(r.status))
    },
    refetchInterval: 5000, // 創建中狀態變化頻率高，5 秒輪詢
    staleTime: 2000,
    enabled,
  })
}

/** 取消尚未進入 provisioning 階段的 VM Request。 */
export function useCancelVmRequest() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (requestId: string) =>
      VmRequestsService.cancelVmRequest({ requestId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["pending-resources"] })
      queryClient.invalidateQueries({ queryKey: queryKeys.vmRequests.all })
      queryClient.invalidateQueries({ queryKey: ["jobs"] })
    },
  })
}

/**
 * 訂閱 /ws/jobs，當 vm_request 類別的任務狀態變化時，立即 invalidate
 * pending-resources 與 resources 兩個 query，避免等 5 秒 polling。
 */
export function usePendingResourcesLiveSync() {
  const queryClient = useQueryClient()
  useEffect(() => {
    const token = localStorage.getItem("access_token")
    if (!token) return
    let lastSig = ""
    const dispose = connectJobsWebSocket(token, (snap) => {
      const sig = snap.items
        .filter((i) => i.kind === "vm_request")
        .map((i) => `${i.id}:${i.status}`)
        .sort()
        .join(",")
      if (sig === lastSig) return
      lastSig = sig
      queryClient.invalidateQueries({ queryKey: ["pending-resources"] })
      queryClient.invalidateQueries({ queryKey: queryKeys.resources.all })
    })
    return dispose
  }, [queryClient])
}
