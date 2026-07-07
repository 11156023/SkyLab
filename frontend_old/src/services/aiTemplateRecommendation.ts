import type { RecommendationFormContext } from "@/client"
import {
  AiTemplateRecommendationService,
  type ChatRequest,
  type ChatResponse,
} from "@/client"
import type { GPUSummary } from "@/services/gpu"

export type { RecommendationFormContext }

export type AiChatMessage = {
  role: "user" | "assistant" | "system"
  content: string
}

export type AiMetrics = {
  total_tokens?: number
  elapsed_seconds?: number
  tokens_per_second?: number
}

export type FormPrefill = {
  resource_type?: string
  hostname?: string
  service_template_slug?: string
  lxc_template_slug?: string
  lxc_os_image?: string
  vm_os_choice?: string
  vm_template_id?: number
  gpu_mapping_id?: string
  start_at?: string
  end_at?: string
  cores?: number
  memory_mb?: number
  disk_gb?: number
  username?: string
  reason?: string
}

export type RecommendationFormContextWithGpu = RecommendationFormContext & {
  gpu_options?: GPUSummary[]
}

export type AiPlanResult = {
  summary?: string
  final_plan?: {
    form_prefill?: FormPrefill
    gpu_recommendation?: {
      should_use_gpu?: boolean
      selected_gpu_mapping_id?: string
      selected_gpu_label?: string
      reason?: string
      candidates?: Array<{
        mapping_id: string
        label: string
        reason: string
      }>
    }
    recommended_templates?: Array<{
      slug: string
      name: string
      why: string
    }>
    machines?: Array<{
      name: string
      deployment_type: string
      cpu: number
      memory_mb: number
      disk_gb: number
      template_slug?: string
    }>
    application_target?: {
      service_name?: string
      execution_environment?: string
      environment_reason?: string
    }
  }
  ai_metrics?: AiMetrics
}

export type AiTemplateRecommendationRequest = Pick<
  ChatRequest,
  "messages" | "top_k" | "device_nodes"
> & {
  form_context?: RecommendationFormContext
}

export const AiTemplateRecommendationApi = {
  chat(data: {
    requestBody: AiTemplateRecommendationRequest
  }): Promise<ChatResponse> {
    return AiTemplateRecommendationService.chat({
      requestBody: data.requestBody,
    })
  },

  recommend(data: {
    requestBody: AiTemplateRecommendationRequest
  }): Promise<AiPlanResult> {
    return AiTemplateRecommendationService.recommend({
      requestBody: data.requestBody,
    }) as Promise<AiPlanResult>
  },
}
