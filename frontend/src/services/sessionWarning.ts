/**
 * Session-status / extend API for the auto-stop warning dialog.
 *
 * These endpoints aren't part of the auto-generated client because the
 * backend feature ships ahead of the OpenAPI sync. Once the client is
 * regenerated they can be replaced by the typed SDK.
 */
import type { CancelablePromise } from "@/client"
import { OpenAPI } from "@/client"
import { request as __request } from "@/client/core/request"

export type SessionStatus = {
  vmid: number
  running: boolean
  auto_stop_at: string | null
  auto_stop_reason: "window_grace" | "practice_quota" | null
  minutes_until_stop: number | null
  should_warn: boolean
  can_extend: boolean
}

export type ExtendSessionResult = {
  vmid: number
  auto_stop_at: string
  extended_minutes: number
}

export const SessionWarningService = {
  getStatus(vmid: number): CancelablePromise<SessionStatus> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/resources/{vmid}/session-status",
      path: { vmid },
    })
  },

  extend(vmid: number): CancelablePromise<ExtendSessionResult> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/resources/{vmid}/extend-session",
      path: { vmid },
    })
  },
}
