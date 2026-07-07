import type { CancelablePromise, VmRequestPublic } from "@/client"
import { OpenAPI } from "@/client"
import { request as __request } from "@/client/core/request"

export type VmRequestReviewRuntimeResource = {
  vmid: number
  name: string
  node: string
  resource_type: string
  status: string
  linked_request_id?: string | null
  linked_hostname?: string | null
  linked_actual_node?: string | null
  linked_desired_node?: string | null
}

export type VmRequestReviewOverlapItem = {
  request_id: string
  hostname: string
  resource_type: string
  start_at?: string | null
  end_at?: string | null
  vmid?: number | null
  status:
    | "pending"
    | "approved"
    | "provisioning"
    | "running"
    | "rejected"
    | "cancelled"
  assigned_node?: string | null
  desired_node?: string | null
  actual_node?: string | null
  projected_node?: string | null
  projected_strategy?: string | null
  migration_status:
    | "idle"
    | "pending"
    | "running"
    | "completed"
    | "failed"
    | "blocked"
  is_current_request: boolean
  is_running_now: boolean
  is_provisioned: boolean
}

export type VmRequestReviewProjectedNode = {
  node: string
  request_count: number
  includes_current_request: boolean
  hostnames: string[]
}

export type VmRequestReviewNodeScore = {
  node: string
  balance_score: number
  cpu_share: number
  memory_share: number
  disk_share: number
  peak_penalty: number
  loadavg_penalty: number
  storage_penalty: number
  migration_cost: number
  priority: number
  is_selected: boolean
  reason?: string | null
}

export type VmRequestReviewContext = {
  request: VmRequestPublic & {
    migration_pinned?: boolean
    resource_warning?: string | null
    desired_node?: string | null
    actual_node?: string | null
    migration_status?: string | null
    migration_error?: string | null
  }
  window_start: string
  window_end: string
  window_active_now: boolean
  feasible: boolean
  placement_strategy?: string | null
  projected_node?: string | null
  summary: string
  reasons: string[]
  warnings: string[]
  resource_warnings: string[]
  cluster_nodes: string[]
  current_running_resources: VmRequestReviewRuntimeResource[]
  overlapping_approved_requests: VmRequestReviewOverlapItem[]
  projected_nodes: VmRequestReviewProjectedNode[]
  node_scores: VmRequestReviewNodeScore[]
}

export const VmRequestReviewService = {
  getContext(data: {
    requestId: string
  }): CancelablePromise<VmRequestReviewContext> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/vm-requests/{request_id}/review-context",
      path: { request_id: data.requestId },
      errors: { 422: "Validation Error" },
    })
  },
}
