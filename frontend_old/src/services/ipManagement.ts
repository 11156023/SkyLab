/**
 * IP 管理 API 服務
 */

import type { CancelablePromise } from "@/client"
import { OpenAPI } from "@/client"
import { request as __request } from "@/client/core/request"

// ── Types ────────────────────────────────────────────────────────────────────

export type SubnetConfigCreate = {
  cidr: string
  gateway: string
  bridge_name: string
  gateway_vm_ip: string
  dns_servers?: string | null
  extra_blocked_subnets?: string[]
}

export type SubnetConfigPublic = {
  cidr: string
  gateway: string
  bridge_name: string
  gateway_vm_ip: string
  dns_servers: string | null
  extra_blocked_subnets: string[]
  updated_at: string
  total_ips: number
  used_ips: number
  available_ips: number
}

export type SubnetStatusResponse = {
  configured: boolean
  cidr?: string | null
  bridge_name?: string | null
  total_ips: number
  used_ips: number
  available_ips: number
}

export type IpAllocationPublic = {
  ip_address: string
  purpose: string
  vmid: number | null
  description: string | null
  allocated_at: string
}

export type IpAllocationListResponse = {
  allocations: IpAllocationPublic[]
  total: number
}

// ── Service ──────────────────────────────────────────────────────────────────

export const IpManagementApiService = {
  /** 取得子網配置（管理員） */
  getSubnetConfig(): CancelablePromise<SubnetConfigPublic | null> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/ip-management/subnet",
    })
  },

  /** 設定/更新子網配置（管理員） */
  upsertSubnetConfig(
    data: SubnetConfigCreate,
  ): CancelablePromise<SubnetConfigPublic> {
    return __request(OpenAPI, {
      method: "PUT",
      url: "/api/v1/ip-management/subnet",
      body: data,
      mediaType: "application/json",
    })
  },

  /** 刪除子網配置（管理員） */
  deleteSubnetConfig(): CancelablePromise<{ message: string }> {
    return __request(OpenAPI, {
      method: "DELETE",
      url: "/api/v1/ip-management/subnet",
    })
  },

  /** 列出所有 IP 分配記錄（管理員） */
  getAllocations(): CancelablePromise<IpAllocationListResponse> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/ip-management/allocations",
    })
  },

  /** 取得子網狀態（所有登入使用者） */
  getSubnetStatus(): CancelablePromise<SubnetStatusResponse> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/ip-management/status",
    })
  },
}
