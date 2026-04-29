import type { CancelablePromise } from "@/client"
import { OpenAPI } from "@/client"
import { request as __request } from "@/client/core/request"

export type CsvImportResult = {
  created: string[]
  already_existed: string[]
  added_to_group: number
  errors: string[]
}

export type TaskStatus = "pending" | "running" | "completed" | "failed"

export type BatchTask = {
  id: string
  user_id: string
  user_email: string | null
  user_name: string | null
  member_index: number
  vmid: number | null
  status: TaskStatus
  error: string | null
  started_at: string | null
  finished_at: string | null
}

export type BatchJobStatus =
  | "pending_review"
  | "approved"
  | "rejected"
  | "cancelled"
  | "pending"
  | "running"
  | "completed"
  | "failed"

export type BatchJobSpec = {
  cores?: number | null
  memory?: number | null
  disk_size?: number | null
  rootfs_size?: number | null
  ostemplate?: string | null
  template_id?: number | null
  username?: string | null
  environment_type?: string | null
  os_info?: string | null
  expiry_date?: string | null
}

export type BatchJob = {
  id: string
  group_id: string
  group_name?: string | null
  resource_type: string
  hostname_prefix: string
  status: BatchJobStatus
  total: number
  done: number
  failed_count: number
  created_at: string
  finished_at: string | null
  initiated_by?: string | null
  initiated_by_email?: string | null
  initiated_by_name?: string | null
  reviewer_id?: string | null
  reviewer_email?: string | null
  reviewed_at?: string | null
  review_comment?: string | null
  recurrence_rule?: string | null
  recurrence_duration_minutes?: number | null
  schedule_timezone?: string | null
  spec?: BatchJobSpec
  tasks: BatchTask[]
}

export type RecurrencePreview = {
  windows: [string, string][]
}

export type BatchProvisionFormState = {
  resourceType: "lxc" | "qemu"
  hostnamePrefix: string
  password: string
  cores: number
  memory: number
  rootfsSize: number
  diskSize: number
  ostemplate: string
  templateId: number | null
  username: string
  expiryDate: string
}

export type BatchProvisionRequestBody = {
  resource_type: "lxc" | "qemu"
  hostname_prefix: string
  password: string
  cores: number
  memory: number
  environment_type: string
  expiry_date?: string
  ostemplate?: string
  rootfs_size?: number
  template_id?: number
  username?: string
  disk_size?: number
  recurrence_rule?: string
  recurrence_duration_minutes?: number
  schedule_timezone?: string
}

export type BatchProvisionReviewBody = {
  decision: "approved" | "rejected"
  review_comment?: string | null
}

export function buildBatchProvisionRequestBody(
  values: BatchProvisionFormState,
): BatchProvisionRequestBody {
  const body: BatchProvisionRequestBody = {
    resource_type: values.resourceType,
    hostname_prefix: values.hostnamePrefix.trim(),
    password: values.password,
    cores: values.cores,
    memory: values.memory,
    environment_type: "教學環境",
  }

  if (values.expiryDate) {
    body.expiry_date = values.expiryDate
  }

  if (values.resourceType === "lxc") {
    body.ostemplate = values.ostemplate
    body.rootfs_size = values.rootfsSize
    return body
  }

  body.template_id = values.templateId ?? undefined
  body.username = values.username.trim()
  body.disk_size = values.diskSize
  return body
}

export const GroupFeatureService = {
  importCsv(data: {
    groupId: string
    file: File
  }): CancelablePromise<CsvImportResult> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/groups/{groupId}/import-csv",
      path: { groupId: data.groupId },
      formData: { file: data.file },
    })
  },

  createBatchProvisionJob(data: {
    groupId: string
    requestBody: BatchProvisionRequestBody
  }): CancelablePromise<BatchJob> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/batch-provision/{groupId}",
      path: { groupId: data.groupId },
      body: data.requestBody,
      mediaType: "application/json",
    })
  },

  getBatchProvisionStatus(data: {
    jobId: string
  }): CancelablePromise<BatchJob> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/batch-provision/{jobId}/status",
      path: { jobId: data.jobId },
    })
  },

  listPendingBatchJobs(): CancelablePromise<BatchJob[]> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/batch-provision/pending",
    })
  },

  reviewBatchJob(data: {
    jobId: string
    requestBody: BatchProvisionReviewBody
  }): CancelablePromise<BatchJob> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/batch-provision/{jobId}/review",
      path: { jobId: data.jobId },
      body: data.requestBody,
      mediaType: "application/json",
    })
  },

  getRecurrencePreview(data: {
    jobId: string
    count?: number
  }): CancelablePromise<RecurrencePreview> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/batch-provision/{jobId}/recurrence-preview",
      path: { jobId: data.jobId },
      query: { count: data.count ?? 5 },
    })
  },
}
