import type { CancelablePromise } from "@/client"
import { OpenAPI } from "@/client"
import { request as __request } from "@/client/core/request"

export type MigrationJobStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "blocked"
  | "cancelled"

export type MigrationJob = {
  id: string
  request_id: string
  vmid?: number | null
  source_node?: string | null
  target_node: string
  status: MigrationJobStatus
  rebalance_epoch: number
  attempt_count: number
  last_error?: string | null
  requested_at: string
  available_at?: string | null
  claimed_by?: string | null
  claimed_at?: string | null
  claim_expires_at?: string | null
  started_at?: string | null
  finished_at?: string | null
  updated_at: string
}

export type MigrationJobsResponse = {
  data: MigrationJob[]
  count: number
}

export type MigrationStats = {
  total_jobs: number
  by_status: Record<string, number>
  avg_duration_seconds: number
  success_rate: number
}

export const MigrationJobsService = {
  list(data: {
    status?: MigrationJobStatus | null
    skip?: number
    limit?: number
  }): CancelablePromise<MigrationJobsResponse> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/migration-jobs/",
      query: {
        status: data.status ?? undefined,
        skip: data.skip ?? 0,
        limit: data.limit ?? 50,
      },
      errors: { 422: "Validation Error" },
    })
  },

  getStats(): CancelablePromise<MigrationStats> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/migration-jobs/stats",
      errors: { 422: "Validation Error" },
    })
  },

  get(data: { jobId: string }): CancelablePromise<MigrationJob> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/migration-jobs/{job_id}",
      path: { job_id: data.jobId },
      errors: { 422: "Validation Error" },
    })
  },

  retry(data: { jobId: string }): CancelablePromise<MigrationJob> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/migration-jobs/{job_id}/retry",
      path: { job_id: data.jobId },
      errors: { 422: "Validation Error" },
    })
  },

  cancel(data: { jobId: string }): CancelablePromise<MigrationJob> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/migration-jobs/{job_id}/cancel",
      path: { job_id: data.jobId },
      errors: { 422: "Validation Error" },
    })
  },
}
