/**
 * AI Judge API types and service
 */

import type { CancelablePromise } from "@/client"
import { OpenAPI } from "@/client"
import { request as __request } from "@/client/core/request"

// ─── Types ────────────────────────────────────────────────────────────────────

export type RubricItem = {
  id: string
  title: string
  description: string
  checked: boolean
  detectable: "auto" | "partial" | "manual"
  detection_method: string | null
  fallback: string | null
  check_steps?: RubricCheckStep[]
}

export type RubricCheckStep = {
  template_key: TemplateKey
  command_key: string
  command_label?: string | null
}

export type TemplateKey = "linux" | "python" | "n8n"

export type RubricAnalysis = {
  items: RubricItem[]
  total_items: number
  checked_count: number
  auto_count: number
  partial_count: number
  manual_count: number
  summary: string
  raw_text: string
}

export type ChatMessage = {
  role: "user" | "assistant"
  content: string
}

export type RubricUploadResponse = {
  analysis: RubricAnalysis
  ai_metrics: {
    prompt_tokens: number
    completion_tokens: number
    total_tokens: number
    elapsed_seconds: number
    tokens_per_second: number
  }
  template_key: TemplateKey
}

export type RubricChatResponse = {
  reply: string
  updated_items: RubricItem[] | null
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  elapsed_seconds: number
  tokens_per_second: number
}

export type RubricHealthResponse = {
  status: string
  vllm_configured: boolean
}

export type TeacherJudgeScriptStatus =
  | "draft"
  | "review_failed"
  | "reviewed"
  | "approved"
  | "archived"

export type TeacherJudgeScriptArtifact = {
  id: string
  group_id: string
  name: string
  template_key: string
  rubric_snapshot_json: Record<string, unknown>
  script_language: "python" | "shell" | "bat"
  script_content: string
  source: "ai_generated" | "regenerated"
  version: number
  status: TeacherJudgeScriptStatus
  policy_check_result_json: Record<string, any>
  ai_review_result_json: Record<string, any>
  created_by: string | null
  approved_by: string | null
  created_at: string
  updated_at: string
  approved_at: string | null
}

export type TeacherJudgeScriptRunTargetScope =
  | "all_with_vm"
  | "running_only"
  | "manual"

export type TeacherJudgeScriptRunStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"

export type TeacherJudgeScriptRun = {
  id: string
  group_id: string
  artifact_id: string
  target_scope: TeacherJudgeScriptRunTargetScope
  target_snapshot_json: Record<string, any>
  status: TeacherJudgeScriptRunStatus
  progress_json: Record<string, any>
  result_summary_json: Record<string, any>
  target_results_json: Record<string, any>
  started_by: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
  updated_at: string
}

// ─── Service ──────────────────────────────────────────────────────────────────

export const AiJudgeService = {
  /**
   * Upload rubric document for AI analysis
   */
  uploadRubric(
    file: File,
    templateKey: TemplateKey = "linux",
  ): CancelablePromise<RubricUploadResponse> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/rubric/upload",
      formData: { file, template_key: templateKey },
    })
  },

  /**
   * Chat with AI to refine rubric
   */
  chat(data: {
    messages: ChatMessage[]
    rubric_context: string
    is_refine?: boolean
    template_key?: TemplateKey
  }): CancelablePromise<RubricChatResponse> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/rubric/chat",
      body: {
        messages: data.messages,
        rubric_context: data.rubric_context,
        is_refine: data.is_refine ?? false,
        template_key: data.template_key ?? "linux",
      },
      mediaType: "application/json",
    })
  },

  /**
   * Download rubric as Excel file
   */
  async downloadExcel(data: {
    items: RubricItem[]
    summary: string
  }): Promise<Blob> {
    // TOKEN can be a string or a Resolver function — resolve it first
    const rawToken = OpenAPI.TOKEN
    const token =
      typeof rawToken === "function"
        ? await (
            rawToken as (o: { method: string; url: string }) => Promise<string>
          )({
            method: "POST",
            url: "/api/v1/rubric/download-excel",
          })
        : rawToken

    const base = OpenAPI.BASE || ""
    const response = await fetch(`${base}/api/v1/rubric/download-excel`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({
        items: data.items,
        summary: data.summary,
      }),
    })

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}))
      throw new Error(errorData.detail || "下載失敗")
    }

    return response.blob()
  },

  /**
   * Health check
   */
  healthCheck(): CancelablePromise<RubricHealthResponse> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/rubric/health",
    })
  },

  listScripts(data: {
    groupId: string
  }): CancelablePromise<TeacherJudgeScriptArtifact[]> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/groups/{groupId}/judge/scripts/",
      path: { groupId: data.groupId },
    })
  },

  createScript(data: {
    groupId: string
    name: string
    template_key: TemplateKey
    rubric_snapshot: RubricAnalysis
  }): CancelablePromise<TeacherJudgeScriptArtifact> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/groups/{groupId}/judge/scripts/",
      path: { groupId: data.groupId },
      body: {
        name: data.name,
        template_key: data.template_key,
        rubric_snapshot: data.rubric_snapshot,
      },
      mediaType: "application/json",
    })
  },

  regenerateScript(data: {
    groupId: string
    scriptId: string
    rubric_snapshot?: RubricAnalysis | null
  }): CancelablePromise<TeacherJudgeScriptArtifact> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/groups/{groupId}/judge/scripts/{scriptId}/regenerate",
      path: { groupId: data.groupId, scriptId: data.scriptId },
      body: { rubric_snapshot: data.rubric_snapshot ?? null },
      mediaType: "application/json",
    })
  },

  approveScript(data: {
    groupId: string
    scriptId: string
  }): CancelablePromise<TeacherJudgeScriptArtifact> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/groups/{groupId}/judge/scripts/{scriptId}/approve",
      path: { groupId: data.groupId, scriptId: data.scriptId },
    })
  },

  createScriptRun(data: {
    groupId: string
    scriptId: string
    target_vmids: number[]
  }): CancelablePromise<TeacherJudgeScriptRun> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/groups/{groupId}/judge/scripts/{scriptId}/runs",
      path: { groupId: data.groupId, scriptId: data.scriptId },
      body: {
        target_scope: "manual",
        target_vmids: data.target_vmids,
      },
      mediaType: "application/json",
    })
  },

  deleteScript(data: {
    groupId: string
    scriptId: string
  }): CancelablePromise<void> {
    return __request(OpenAPI, {
      method: "DELETE",
      url: "/api/v1/groups/{groupId}/judge/scripts/{scriptId}",
      path: { groupId: data.groupId, scriptId: data.scriptId },
    })
  },
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Trigger file download from blob
 */
export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

/**
 * Format rubric analysis to context string for chat
 */
export function rubricToContext(analysis: RubricAnalysis): string {
  return JSON.stringify({
    items: analysis.items,
    total_items: analysis.total_items,
    checked_count: analysis.checked_count,
    summary: analysis.summary,
  })
}

/**
 * Get detectable status badge info
 */
export function getDetectableInfo(detectable: string) {
  switch (detectable) {
    case "auto":
      return {
        label: "可自動偵測",
        className:
          "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
      }
    case "partial":
      return {
        label: "部分可偵測",
        className:
          "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400",
      }
    default:
      return {
        label: "需人工評閱",
        className:
          "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
      }
  }
}

/**
 * Get achieved status badge info
 */
export function getCheckedInfo(checked: boolean) {
  if (checked) {
    return {
      label: "已達成",
      className:
        "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
    }
  }

  return {
    label: "未達成",
    className:
      "bg-slate-100 text-slate-700 dark:bg-slate-900/30 dark:text-slate-300",
  }
}

export const TEMPLATE_OPTIONS: { key: TemplateKey; label: string }[] = [
  { key: "linux", label: "一般 Linux/LXC" },
  { key: "python", label: "Python" },
  { key: "n8n", label: "n8n" },
]

export function getTemplateLabel(templateKey: string | null | undefined) {
  return (
    TEMPLATE_OPTIONS.find((option) => option.key === templateKey)?.label ??
    "一般 Linux/LXC"
  )
}
