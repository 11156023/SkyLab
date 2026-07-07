import type { CancelablePromise } from "@/client"
import { OpenAPI } from "@/client"
import { request as __request } from "@/client/core/request"

// Local types — kept independent from the generated client so the new
// AuditAction enum values added on the backend don't require client regen.

export type AuditLogEntry = {
  id: string
  user_id: string | null
  user_email?: string | null
  user_full_name?: string | null
  vmid: number | null
  action: string
  details: string
  ip_address: string | null
  user_agent: string | null
  created_at: string
}

export type AuditLogsResponse = {
  data: AuditLogEntry[]
  count: number
}

export type AuditLogStats = {
  total: number
  danger: number
  login_failed: number
  active_users: number
}

export type AuditActionMeta = {
  value: string
  category: string
}

export type AuditUserOption = {
  id: string
  email: string
  full_name?: string | null
}

export type AuditLogQuery = {
  skip?: number
  limit?: number
  vmid?: number | null
  user_id?: string | null
  action?: string | null
  start_time?: string | null
  end_time?: string | null
  ip_address?: string | null
  search?: string | null
}

function buildQuery(q: AuditLogQuery) {
  return {
    skip: q.skip ?? 0,
    limit: q.limit ?? 50,
    vmid: q.vmid ?? undefined,
    user_id: q.user_id ?? undefined,
    action: q.action ?? undefined,
    start_time: q.start_time ?? undefined,
    end_time: q.end_time ?? undefined,
    ip_address: q.ip_address ?? undefined,
    search: q.search ?? undefined,
  }
}

export const AuditLogsAPI = {
  listAll(query: AuditLogQuery): CancelablePromise<AuditLogsResponse> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/audit-logs/",
      query: buildQuery(query),
      errors: { 422: "Validation Error" },
    })
  },

  listMy(query: AuditLogQuery): CancelablePromise<AuditLogsResponse> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/audit-logs/my",
      query: buildQuery(query),
      errors: { 422: "Validation Error" },
    })
  },

  getStats(params: {
    start_time?: string | null
    end_time?: string | null
  }): CancelablePromise<AuditLogStats> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/audit-logs/stats",
      query: {
        start_time: params.start_time ?? undefined,
        end_time: params.end_time ?? undefined,
      },
      errors: { 422: "Validation Error" },
    })
  },

  listActions(): CancelablePromise<AuditActionMeta[]> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/audit-logs/actions",
    })
  },

  listUsers(): CancelablePromise<AuditUserOption[]> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/audit-logs/users",
    })
  },

  /**
   * CSV download — returns the absolute URL the browser should hit. The browser
   * needs to send the JWT in the Authorization header which `fetch`/`__request`
   * already handles, so we route through __request and download the blob client-side.
   */
  exportCsv(query: AuditLogQuery): Promise<Blob> {
    const params = new URLSearchParams()
    const q = buildQuery(query)
    for (const [k, v] of Object.entries(q)) {
      if (v !== undefined && v !== null && v !== "") {
        params.append(k, String(v))
      }
    }
    const baseUrl = OpenAPI.BASE
    const token =
      typeof OpenAPI.TOKEN === "function"
        ? (
            OpenAPI.TOKEN as (options: {
              method: string
              url: string
            }) => Promise<string> | string
          )({
            method: "GET",
            url: "/api/v1/audit-logs/export",
          })
        : (OpenAPI.TOKEN as string | undefined)

    const headers: Record<string, string> = {}
    const buildHeaders = async () => {
      const t = await Promise.resolve(token)
      if (t) headers.Authorization = `Bearer ${t}`
      return headers
    }

    return buildHeaders().then((h) =>
      fetch(`${baseUrl}/api/v1/audit-logs/export?${params.toString()}`, {
        method: "GET",
        headers: h,
      }).then(async (res) => {
        if (!res.ok) {
          throw new Error(`Failed to export audit logs: ${res.status}`)
        }
        return res.blob()
      }),
    )
  },
}

// Action -> category mapping fallback when the /actions endpoint hasn't loaded yet
export const ACTION_CATEGORY_FALLBACK: Record<string, string> = {}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement("a")
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}
