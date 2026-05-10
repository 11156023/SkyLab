import type { CancelablePromise, VmRequestPublic } from "@/client"
import { OpenAPI } from "@/client"
import { request as __request } from "@/client/core/request"
import type { VmRequestCreateRequestBody } from "@/lib/resourcePayloads"

export type VmRequestWindowAvailabilityRequest = {
  resource_type: "lxc" | "vm"
  cores: number
  memory: number
  disk_size?: number | null
  rootfs_size?: number | null
  gpu_required?: number
  start_at: string
  end_at: string
  mode?: "quick_template" | "research" | "scheduled"
}

export type VmRequestWindowAvailabilityResponse = {
  status: "available" | "limited" | "unavailable"
  feasible: boolean
  start_at: string
  end_at: string
  duration_hours: number
  duration_days: number
  summary: string
  reason: string
  selected_node?: string | null
  placement_strategy?: string | null
  checked_checkpoint_count: number
  warnings: string[]
}

export const VmRequestsApi = {
  create(data: {
    requestBody: VmRequestCreateRequestBody
  }): CancelablePromise<VmRequestPublic> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/vm-requests/",
      body: data.requestBody,
      mediaType: "application/json",
      errors: { 422: "Validation Error" },
    })
  },

  windowAvailability(data: {
    requestBody: VmRequestWindowAvailabilityRequest
  }): CancelablePromise<VmRequestWindowAvailabilityResponse> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/vm-requests/window-availability",
      body: data.requestBody,
      mediaType: "application/json",
      errors: { 422: "Validation Error" },
    })
  },

  cancel(data: { requestId: string }): CancelablePromise<VmRequestPublic> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/vm-requests/{request_id}/cancel",
      path: { request_id: data.requestId },
      errors: { 422: "Validation Error" },
    })
  },

  retry(data: { requestId: string }): CancelablePromise<VmRequestPublic> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/vm-requests/{request_id}/retry",
      path: { request_id: data.requestId },
      errors: { 422: "Validation Error" },
    })
  },
}
