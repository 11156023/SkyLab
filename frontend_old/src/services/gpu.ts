import type { CancelablePromise } from "@/client"
import { OpenAPI } from "@/client"
import { request as __request } from "@/client/core/request"

export type GPUDeviceMap = {
  node: string
  path: string
  id: string
  subsystem_id?: string | null
  iommu_group?: number | null
  description?: string | null
}

export type GPUUsageInfo = {
  vmid: number
  vm_name: string
  node: string
  status: string
  mdev_type: string
  allocated_vram_mb: number
}

export type GPUMappingDetail = {
  id: string
  description: string
  maps: GPUDeviceMap[]
  physical_gpu_count: number
  device_count: number
  used_count: number
  available_count: number
  is_sriov: boolean
  has_mdev: boolean
  total_vram_mb: number
  used_vram_mb: number
  used_by: GPUUsageInfo[]
}

export type GPUMappingsPublic = {
  data: GPUMappingDetail[]
  count: number
}

export type GPUSummary = {
  mapping_id: string
  description: string
  model: string
  vram: string
  node: string
  physical_gpu_count: number
  device_count: number
  used_count: number
  available_count: number
  is_sriov: boolean
  has_mdev: boolean
  total_vram_mb: number
  used_vram_mb: number
}

export type GPUMappingCreate = {
  id: string
  description?: string
  map: string[]
}

export const GpuService = {
  listMappings(): CancelablePromise<GPUMappingsPublic> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/gpu/mappings",
      errors: { 422: "Validation Error" },
    })
  },

  getMapping(mappingId: string): CancelablePromise<GPUMappingDetail> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/gpu/mappings/{mapping_id}",
      path: { mapping_id: mappingId },
      errors: { 422: "Validation Error" },
    })
  },

  createMapping(
    body: GPUMappingCreate,
  ): CancelablePromise<{ message: string }> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/gpu/mappings",
      body,
      mediaType: "application/json",
      errors: { 422: "Validation Error" },
    })
  },

  deleteMapping(mappingId: string): CancelablePromise<{ message: string }> {
    return __request(OpenAPI, {
      method: "DELETE",
      url: "/api/v1/gpu/mappings/{mapping_id}",
      path: { mapping_id: mappingId },
      errors: { 422: "Validation Error" },
    })
  },

  listOptions(params?: {
    startAt?: string
    endAt?: string
  }): CancelablePromise<GPUSummary[]> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/gpu/options",
      query: {
        start_at: params?.startAt,
        end_at: params?.endAt,
      },
      errors: { 422: "Validation Error" },
    })
  },
}
