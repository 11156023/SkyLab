import type {
  CancelablePromise,
  QuotaUsagePublic,
  ResourceQuotaCreate,
  ResourceQuotaPublic,
} from "@/client"
import { OpenAPI } from "@/client"
import { request as __request } from "@/client/core/request"

const BASE = "/api/v1/quotas"

export const QuotaAPI = {
  list(): CancelablePromise<ResourceQuotaPublic[]> {
    return __request(OpenAPI, { method: "GET", url: BASE })
  },

  create(body: ResourceQuotaCreate): CancelablePromise<ResourceQuotaPublic> {
    return __request(OpenAPI, {
      method: "POST",
      url: BASE,
      body,
      mediaType: "application/json",
    })
  },

  remove(quotaId: string): CancelablePromise<{ message: string }> {
    return __request(OpenAPI, {
      method: "DELETE",
      url: `${BASE}/{quota_id}`,
      path: { quota_id: quotaId },
    })
  },

  myUsage(): CancelablePromise<QuotaUsagePublic> {
    return __request(OpenAPI, { method: "GET", url: `${BASE}/my-usage` })
  },
}
