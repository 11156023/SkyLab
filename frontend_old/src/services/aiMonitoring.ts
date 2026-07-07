import type { CancelablePromise } from "@/client"
import { OpenAPI } from "@/client"
import { request as __request } from "@/client/core/request"

/**
 * 將 "YYYY-MM-DD" 轉換為 "YYYY-MM-DDT00:00:00"，避免後端將日期解析為當天午夜而漏掉
 * 當天 00:00:00 之後的資料。若已包含時間部分則原樣回傳。
 */
function toStartOfDay(date?: string): string | undefined {
  if (!date) return undefined
  return date.includes("T") ? date : `${date}T00:00:00`
}

/**
 * 將 "YYYY-MM-DD" 轉換為 "YYYY-MM-DDT23:59:59"，使結束日期涵蓋整天。
 * 若已包含時間部分則原樣回傳。
 */
function toEndOfDay(date?: string): string | undefined {
  if (!date) return undefined
  return date.includes("T") ? date : `${date}T23:59:59`
}

// ===== Admin: AIMonitoringStats =====
export type AIMonitoringStats = {
  proxy_total_calls: number
  proxy_total_input_tokens: number
  proxy_total_output_tokens: number
  template_total_calls: number
  template_total_input_tokens: number
  template_total_output_tokens: number
  active_users: number
  models_used: string[]
}

// ===== Admin: Proxy Calls =====
export type AIProxyCallRecord = {
  id: string
  user_id: string
  user_email?: string | null
  user_full_name?: string | null
  credential_id: string
  model_name: string
  request_type: string
  input_tokens: number
  output_tokens: number
  request_duration_ms?: number | null
  status: string
  error_message?: string | null
  created_at: string
}

export type AIProxyCallsResponse = {
  data: AIProxyCallRecord[]
  count: number
}

// ===== Admin: Template Calls =====
export type AITemplateCallRecord = {
  id: string
  user_id: string
  user_email?: string | null
  user_full_name?: string | null
  call_type: string
  model_name: string
  preset?: string | null
  input_tokens: number
  output_tokens: number
  request_duration_ms?: number | null
  status: string
  error_message?: string | null
  created_at: string
}

export type AITemplateCallsResponse = {
  data: AITemplateCallRecord[]
  count: number
}

// ===== Admin: Users Usage =====
export type AIUserUsageSummary = {
  user_id: string
  user_email?: string | null
  user_full_name?: string | null
  proxy_calls: number
  proxy_input_tokens: number
  proxy_output_tokens: number
  template_calls: number
  template_input_tokens: number
  template_output_tokens: number
}

export type AIUsersUsageResponse = {
  data: AIUserUsageSummary[]
  count: number
}

// ===== User-facing: Proxy usage (needs AI API Key bearer) =====
export type UsageByModel = {
  requests: number
  input_tokens: number
  output_tokens: number
}

export type UsageStatsResponse = {
  total_requests: number
  total_input_tokens: number
  total_output_tokens: number
  by_model: Record<string, UsageByModel>
  start_date: string
  end_date: string
}

// ===== User-facing: Template usage (uses normal JWT) =====
export type TemplateUsageByCallType = {
  calls: number
  input_tokens: number
  output_tokens: number
}

export type TemplateUsageStatsResponse = {
  total_calls: number
  total_input_tokens: number
  total_output_tokens: number
  by_call_type: Record<string, TemplateUsageByCallType>
  start_date: string
  end_date: string
}

// ===== Admin monitoring service (JWT auth) =====
export const AiAdminMonitoringService = {
  getStats(params: {
    start_date?: string
    end_date?: string
  }): CancelablePromise<AIMonitoringStats> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/ai-api/monitoring/stats",
      query: {
        start_date: toStartOfDay(params.start_date),
        end_date: toEndOfDay(params.end_date),
      },
    })
  },

  listApiCalls(params: {
    user_id?: string
    model_name?: string
    status?: string
    start_date?: string
    end_date?: string
    skip?: number
    limit?: number
  }): CancelablePromise<AIProxyCallsResponse> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/ai-api/monitoring/api-calls",
      query: {
        user_id: params.user_id || undefined,
        model_name: params.model_name || undefined,
        status: params.status || undefined,
        start_date: toStartOfDay(params.start_date),
        end_date: toEndOfDay(params.end_date),
        skip: params.skip,
        limit: params.limit,
      },
    })
  },

  listTemplateCalls(params: {
    user_id?: string
    call_type?: string
    preset?: string
    status?: string
    start_date?: string
    end_date?: string
    skip?: number
    limit?: number
  }): CancelablePromise<AITemplateCallsResponse> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/ai-api/monitoring/template-calls",
      query: {
        user_id: params.user_id || undefined,
        call_type: params.call_type || undefined,
        preset: params.preset || undefined,
        status: params.status || undefined,
        start_date: toStartOfDay(params.start_date),
        end_date: toEndOfDay(params.end_date),
        skip: params.skip,
        limit: params.limit,
      },
    })
  },

  listUsersUsage(params: {
    start_date?: string
    end_date?: string
    skip?: number
    limit?: number
  }): CancelablePromise<AIUsersUsageResponse> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/ai-api/monitoring/users",
      query: {
        start_date: toStartOfDay(params.start_date),
        end_date: toEndOfDay(params.end_date),
        skip: params.skip,
        limit: params.limit,
      },
    })
  },
}

// ===== User-facing usage service =====
export const AiUserUsageService = {
  /** Template usage — uses normal JWT auth */
  getMyTemplateUsage(params: {
    start_date: string
    end_date: string
  }): CancelablePromise<TemplateUsageStatsResponse> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/ai/template-recommendation/usage/my",
      query: {
        start_date: toStartOfDay(params.start_date),
        end_date: toEndOfDay(params.end_date),
      },
    })
  },

  /** Proxy usage — uses normal JWT auth inside the dashboard */
  getMyProxyUsage(params: {
    start_date: string
    end_date: string
  }): CancelablePromise<UsageStatsResponse> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/ai-api/usage/proxy/my",
      query: {
        start_date: toStartOfDay(params.start_date),
        end_date: toEndOfDay(params.end_date),
      },
    })
  },
}
