import type { CancelablePromise } from "@/client"
import { OpenAPI } from "@/client"
import { request as __request } from "@/client/core/request"

export type ScriptDeployLogListItem = {
  id: string
  task_id: string
  user_id: string | null
  vmid: number | null
  template_slug: string
  template_name: string | null
  hostname: string | null
  status: string
  progress: string | null
  message: string | null
  created_at: string
  updated_at: string
  completed_at: string | null
}

export type ScriptDeployLogDetail = ScriptDeployLogListItem & {
  script_path: string | null
  error: string | null
  output: string | null
}

export type ScriptDeployLogList = {
  items: ScriptDeployLogListItem[]
  total: number
  limit: number
  offset: number
}

export type ScriptDeployLogQuery = {
  limit?: number
  offset?: number
  status?: string | null
  template_slug?: string | null
  vmid?: number | null
}

export const ScriptDeployLogsAPI = {
  list(query: ScriptDeployLogQuery): CancelablePromise<ScriptDeployLogList> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/script-deploy/logs",
      query: {
        limit: query.limit ?? 50,
        offset: query.offset ?? 0,
        status: query.status ?? undefined,
        template_slug: query.template_slug ?? undefined,
        vmid: query.vmid ?? undefined,
      },
      errors: { 422: "Validation Error" },
    })
  },

  detail(taskId: string): CancelablePromise<ScriptDeployLogDetail> {
    return __request(OpenAPI, {
      method: "GET",
      url: `/api/v1/script-deploy/logs/${encodeURIComponent(taskId)}`,
      errors: { 404: "Not Found" },
    })
  },
}
