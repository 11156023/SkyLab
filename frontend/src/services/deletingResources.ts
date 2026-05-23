import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect } from "react"

import {
  type DeletionRequestPublic,
  type DeletionRequestStatus,
  DeletionRequestsService,
} from "@/client"
import { queryKeys } from "@/lib/queryKeys"
import { connectJobsWebSocket } from "@/services/jobs"

/** 視為「刪除中」、應在資源列表覆蓋顯示為 placeholder 的刪除請求狀態。 */
export const DELETING_STATUSES: ReadonlySet<DeletionRequestStatus> = new Set([
  "pending",
  "running",
])

export interface DeletingMeta {
  request_id: string
  status: DeletionRequestStatus
  user_id: string
  user_email: string | null | undefined
  user_full_name: string | null | undefined
  created_at: string
  started_at: string | null | undefined
  purge: boolean
  force: boolean
}

export function deletionToMeta(req: DeletionRequestPublic): DeletingMeta {
  return {
    request_id: req.id,
    status: req.status,
    user_id: req.user_id,
    user_email: req.user_email,
    user_full_name: req.user_full_name,
    created_at: req.created_at,
    started_at: req.started_at,
    purge: req.purge,
    force: req.force,
  }
}

interface UseDeletingResourcesOpts {
  isAdmin: boolean
  enabled?: boolean
}

/**
 * Fetch 目前正在處理中（pending / running）的刪除請求，
 * 回傳 `Map<vmid, DeletionRequestPublic>` 以便 row 即時對應。
 */
export function useDeletingResources({
  isAdmin,
  enabled = true,
}: UseDeletingResourcesOpts) {
  return useQuery({
    queryKey: ["deleting-resources", isAdmin ? "all" : "my"],
    queryFn: async () => {
      const resp = isAdmin
        ? await DeletionRequestsService.listAllDeletionRequests({})
        : await DeletionRequestsService.listMyDeletionRequests({})
      const items: DeletionRequestPublic[] = resp.data ?? []
      const active = items.filter((r) => DELETING_STATUSES.has(r.status))
      const byVmid = new Map<number, DeletionRequestPublic>()
      for (const r of active) {
        byVmid.set(r.vmid, r)
      }
      return byVmid
    },
    refetchInterval: 5000,
    staleTime: 2000,
    enabled,
  })
}

/** 取消尚未開始執行的刪除請求（僅 pending）。 */
export function useCancelDeletionRequest() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (requestId: string) =>
      DeletionRequestsService.cancelDeletionRequest({ requestId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["deleting-resources"] })
      queryClient.invalidateQueries({ queryKey: queryKeys.resources.all })
      queryClient.invalidateQueries({ queryKey: ["jobs"] })
    },
  })
}

/** 重試已失敗的刪除請求（僅 failed）。 */
export function useRetryDeletionRequest() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (requestId: string) =>
      DeletionRequestsService.retryDeletionRequest({ requestId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["deleting-resources"] })
      queryClient.invalidateQueries({ queryKey: queryKeys.resources.all })
      queryClient.invalidateQueries({ queryKey: ["jobs"] })
    },
  })
}

/**
 * 訂閱 /ws/jobs，當 deletion 類別的任務狀態變化時，立即 invalidate
 * deleting-resources 與 resources 兩個 query，避免等 5 秒 polling。
 */
export function useDeletingResourcesLiveSync() {
  const queryClient = useQueryClient()
  useEffect(() => {
    const token = localStorage.getItem("access_token")
    if (!token) return
    let lastSig = ""
    const dispose = connectJobsWebSocket(token, (snap) => {
      const sig = snap.items
        .filter((i) => i.kind === "deletion")
        .map((i) => `${i.id}:${i.status}`)
        .sort()
        .join(",")
      if (sig === lastSig) return
      lastSig = sig
      queryClient.invalidateQueries({ queryKey: ["deleting-resources"] })
      queryClient.invalidateQueries({ queryKey: queryKeys.resources.all })
      queryClient.invalidateQueries({ queryKey: queryKeys.resources.my })
    })
    return dispose
  }, [queryClient])
}
