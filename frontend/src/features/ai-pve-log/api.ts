import type { CancelablePromise } from "@/client"
import { OpenAPI } from "@/client"
import { request as __request } from "@/client/core/request"

export type ToolCallRecord = {
  name: string
  args: Record<string, unknown>
  result?: Record<string, unknown>
}

export type ChatResponse = {
  reply: string
  tools_called: ToolCallRecord[]
  needs_confirmation?: boolean
  messages?: Record<string, unknown>[]
  error: string | null
}

export type SSHConfirmRequest = {
  token: string
  approved: boolean
  command?: string
}

export type SSHExecResult = {
  stdout: string
  stderr: string
  exit_code: number
  blocked: boolean
  pending: boolean
  confirm_token?: string | null
}

export const AiPveLogService = {
  chat(data: {
    message?: string
    messages?: Record<string, unknown>[]
    group_id: string
  }): CancelablePromise<ChatResponse> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/ai/pve-log/chat",
      body: data,
      mediaType: "application/json",
    })
  },

  confirmSsh(data: SSHConfirmRequest): CancelablePromise<SSHExecResult> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/ai/pve-log/ssh/confirm",
      body: data,
      mediaType: "application/json",
    })
  },
}
