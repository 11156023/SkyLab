import type { CancelablePromise } from "@/client"
import { OpenAPI } from "@/client"
import { request as __request } from "@/client/core/request"

export type CloudflareConfigPublic = {
  account_id: string | null
  is_configured: boolean
  has_api_token: boolean
  has_default_dns_target: boolean
  default_dns_target_type: string | null
  default_dns_target_value: string | null
  updated_at: string | null
  last_verified_at: string | null
}

export type CloudflareConfigUpdate = {
  account_id?: string | null
  api_token?: string | null
  default_dns_target_type?: string | null
  default_dns_target_value?: string | null
}

export type CloudflareConnectionTestResult = {
  success: boolean
  message: string
  token_status: string | null
}

export type CloudflarePageInfoPublic = {
  page: number
  per_page: number
  count: number
  total_count: number
  total_pages: number
}

export type CloudflareZonePublic = {
  id: string
  name: string
  status: string
  paused: boolean
  type: string | null
  development_mode: number | null
  name_servers: string[]
  original_name_servers: string[]
  created_on: string | null
  modified_on: string | null
  activated_on: string | null
}

export type CloudflareZonesPublic = {
  items: CloudflareZonePublic[]
  page_info: CloudflarePageInfoPublic
}

export type CloudflareZoneCreate = {
  name: string
  account_id?: string | null
  jump_start: boolean
}

export type CloudflareDNSRecordPublic = {
  id: string
  zone_id: string
  type: string
  name: string
  content: string
  ttl: number
  proxied: boolean | null
  proxiable: boolean | null
  comment: string | null
  priority: number | null
  tags: string[]
  created_on: string | null
  modified_on: string | null
}

export type CloudflareDNSRecordsPublic = {
  items: CloudflareDNSRecordPublic[]
  page_info: CloudflarePageInfoPublic
}

export type CloudflareDNSRecordMutation = {
  type: string
  name: string
  content: string
  ttl: number
  proxied?: boolean
  comment?: string
  priority?: number
}

export type ZoneListParams = {
  page?: number
  per_page?: number
  search?: string
  status?: string
}

export type DNSRecordListParams = {
  page?: number
  per_page?: number
  search?: string
  type?: string
  proxied?: boolean
}

export const CloudflareApiService = {
  getConfig(): CancelablePromise<CloudflareConfigPublic> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/cloudflare/config",
    })
  },

  updateConfig(
    data: CloudflareConfigUpdate,
  ): CancelablePromise<CloudflareConfigPublic> {
    return __request(OpenAPI, {
      method: "PUT",
      url: "/api/v1/cloudflare/config",
      body: data,
      mediaType: "application/json",
    })
  },

  testConfig(): CancelablePromise<CloudflareConnectionTestResult> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/cloudflare/config/test",
    })
  },

  listZones(
    params: ZoneListParams = {},
  ): CancelablePromise<CloudflareZonesPublic> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/cloudflare/zones",
      query: params,
    })
  },

  getZone(zoneId: string): CancelablePromise<CloudflareZonePublic> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/cloudflare/zones/{zone_id}",
      path: { zone_id: zoneId },
    })
  },

  createZone(
    data: CloudflareZoneCreate,
  ): CancelablePromise<CloudflareZonePublic> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/cloudflare/zones",
      body: data,
      mediaType: "application/json",
    })
  },

  listDnsRecords(
    zoneId: string,
    params: DNSRecordListParams = {},
  ): CancelablePromise<CloudflareDNSRecordsPublic> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/cloudflare/zones/{zone_id}/dns-records",
      path: { zone_id: zoneId },
      query: params,
    })
  },

  createDnsRecord(
    zoneId: string,
    data: CloudflareDNSRecordMutation,
  ): CancelablePromise<CloudflareDNSRecordPublic> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/cloudflare/zones/{zone_id}/dns-records",
      path: { zone_id: zoneId },
      body: data,
      mediaType: "application/json",
    })
  },

  updateDnsRecord(
    zoneId: string,
    recordId: string,
    data: CloudflareDNSRecordMutation,
  ): CancelablePromise<CloudflareDNSRecordPublic> {
    return __request(OpenAPI, {
      method: "PATCH",
      url: "/api/v1/cloudflare/zones/{zone_id}/dns-records/{record_id}",
      path: { zone_id: zoneId, record_id: recordId },
      body: data,
      mediaType: "application/json",
    })
  },

  deleteDnsRecord(
    zoneId: string,
    recordId: string,
  ): CancelablePromise<{ message: string }> {
    return __request(OpenAPI, {
      method: "DELETE",
      url: "/api/v1/cloudflare/zones/{zone_id}/dns-records/{record_id}",
      path: { zone_id: zoneId, record_id: recordId },
    })
  },
}
