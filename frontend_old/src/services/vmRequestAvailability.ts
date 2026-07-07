import type { CancelablePromise } from "@/client"
import { OpenAPI } from "@/client"
import { request as __request } from "@/client/core/request"

export type VmRequestAvailabilityStatus =
  | "available"
  | "limited"
  | "unavailable"
  | "policy_blocked"

export type VmRequestAvailabilityRequest = {
  resource_type: "lxc" | "vm"
  cores: number
  memory: number
  disk_size?: number | null
  rootfs_size?: number | null
  instance_count?: number
  gpu_required?: number
  days?: number
  timezone?: string
  policy_role?: "student" | "teacher" | "admin" | null
}

export type VmRequestAvailabilitySlot = {
  start_at: string
  end_at: string
  date: string
  hour: number
  within_policy: boolean
  feasible: boolean
  status: VmRequestAvailabilityStatus
  label: string
  summary: string
  reasons: string[]
  recommended_nodes: string[]
  target_node?: string | null
  placement_strategy?: string | null
  node_snapshots: VmRequestAvailabilityNodeSnapshot[]
}

export type VmRequestAvailabilityStackItem = {
  name: string
  count: number
  pending: boolean
}

export type VmRequestAvailabilityNodeSnapshot = {
  node: string
  status: string
  candidate: boolean
  priority: number
  is_target: boolean
  placement_count: number
  running_resources: number
  projected_running_resources: number
  dominant_share: number
  average_share: number
  cpu_share: number
  memory_share: number
  disk_share: number
  remaining_cpu_cores: number
  remaining_memory_gb: number
  remaining_disk_gb: number
  vm_stack: VmRequestAvailabilityStackItem[]
}

export type VmRequestAvailabilityDay = {
  date: string
  available_hours: number[]
  limited_hours: number[]
  blocked_hours: number[]
  unavailable_hours: number[]
  best_hours: number[]
  slots: VmRequestAvailabilitySlot[]
}

export type VmRequestAvailabilitySummary = {
  timezone: string
  role: string
  role_label: string
  policy_window: string
  checked_days: number
  feasible_slot_count: number
  recommended_slot_count: number
  current_status: string
}

export type VmRequestAvailabilityResponse = {
  summary: VmRequestAvailabilitySummary
  recommended_slots: VmRequestAvailabilitySlot[]
  days: VmRequestAvailabilityDay[]
}

export const VmRequestAvailabilityService = {
  preview(data: {
    requestBody: VmRequestAvailabilityRequest
  }): CancelablePromise<VmRequestAvailabilityResponse> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/vm-requests/availability",
      body: data.requestBody,
      mediaType: "application/json",
      errors: { 422: "Validation Error" },
    })
  },

  getByRequestId(data: {
    requestId: string
    days?: number
    timezone?: string
  }): CancelablePromise<VmRequestAvailabilityResponse> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/vm-requests/{request_id}/availability",
      path: { request_id: data.requestId },
      query: {
        days: data.days ?? 7,
        timezone: data.timezone ?? "Asia/Taipei",
      },
      errors: { 422: "Validation Error" },
    })
  },
}
