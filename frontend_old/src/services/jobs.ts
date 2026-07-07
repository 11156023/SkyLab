import type { CancelablePromise } from "@/client"
import { OpenAPI } from "@/client"
import { request as __request } from "@/client/core/request"

export type JobKind =
  | "migration"
  | "script_deploy"
  | "vm_request"
  | "spec_change"
  | "deletion"

export type JobStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "blocked"
  | "cancelled"

export type JobItem = {
  id: string
  kind: JobKind
  title: string
  status: JobStatus
  progress: number | null
  message: string | null
  user_id: string | null
  user_email: string | null
  created_at: string
  updated_at: string
  completed_at: string | null
  detail_url: string | null
  meta: Record<string, unknown>
}

export type JobsListResponse = {
  items: JobItem[]
  total: number
  active_count: number
}

export type JobsListQuery = {
  kinds?: JobKind[]
  statuses?: JobStatus[]
  active_only?: boolean
  limit?: number
  offset?: number
  history_days?: number
}

export type JobDetail = {
  item: JobItem
  output: string | null
  error: string | null
  extra: Record<string, unknown>
}

const csv = (xs: readonly string[] | undefined) =>
  xs && xs.length > 0 ? xs.join(",") : undefined

export const JobsAPI = {
  list(params: JobsListQuery = {}): CancelablePromise<JobsListResponse> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/jobs/",
      query: {
        kinds: csv(params.kinds),
        statuses: csv(params.statuses),
        active_only: params.active_only ? true : undefined,
        limit: params.limit ?? 50,
        offset: params.offset ?? 0,
        history_days: params.history_days ?? 30,
      },
      errors: { 422: "Validation Error" },
    })
  },

  recent(limit = 5): CancelablePromise<JobsListResponse> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/jobs/recent",
      query: { limit },
      errors: { 422: "Validation Error" },
    })
  },

  detail(jobId: string): CancelablePromise<JobDetail> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/jobs/{job_id}",
      path: { job_id: jobId },
      errors: { 403: "Forbidden", 404: "Not found", 422: "Validation Error" },
    })
  },
}

// ─── WebSocket helper ────────────────────────────────────────────────────────

export type JobsSubscriber = (snapshot: JobsListResponse) => void

export function connectJobsWebSocket(
  token: string,
  onSnapshot: JobsSubscriber,
  onError?: (err: Event) => void,
): () => void {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:"
  const host = window.location.host
  const url = `${proto}//${host}/ws/jobs?token=${encodeURIComponent(token)}`

  let ws: WebSocket | null = null
  let stopped = false
  let reconnectTimer: number | null = null

  const open = () => {
    if (stopped) return
    try {
      ws = new WebSocket(url)
    } catch (err) {
      onError?.(err as Event)
      schedule()
      return
    }
    ws.onmessage = (evt) => {
      try {
        const data = JSON.parse(evt.data) as JobsListResponse
        onSnapshot(data)
      } catch {
        // ignore parse error
      }
    }
    ws.onerror = (err) => onError?.(err)
    ws.onclose = () => {
      ws = null
      schedule()
    }
  }

  const schedule = () => {
    if (stopped) return
    if (reconnectTimer !== null) return
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null
      open()
    }, 5000)
  }

  open()

  return () => {
    stopped = true
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (ws) {
      try {
        ws.close()
      } catch {
        // noop
      }
      ws = null
    }
  }
}
