import type { CancelablePromise } from "@/client"
import { OpenAPI } from "@/client"
import { request as __request } from "@/client/core/request"

export type NavigationAction = "navigate" | "suggest" | "clarify"

export type NavigationTarget = {
  title: string
  path: string
  reason: string
}

export type NavigationResolveResponse = {
  intent: string
  confidence: number
  action: NavigationAction
  primary?: NavigationTarget | null
  suggestions: NavigationTarget[]
  clarification_question?: string | null
}

export const AiNavigationService = {
  resolve(query: string): CancelablePromise<NavigationResolveResponse> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/ai/navigation/resolve",
      body: { query },
      mediaType: "application/json",
    })
  },
}
