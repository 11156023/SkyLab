import type { CancelablePromise } from "@/client"
import { OpenAPI } from "@/client"
import { request as __request } from "@/client/core/request"

export type AiApiRequestStatus = "pending" | "approved" | "rejected"

export type AiApiRequestPublic = {
  id: string
  user_id: string
  user_email?: string | null
  user_full_name?: string | null
  purpose: string
  api_key_name: string
  duration?: string
  rate_limit?: number | null
  status: AiApiRequestStatus
  reviewer_id?: string | null
  reviewer_email?: string | null
  review_comment?: string | null
  reviewed_at?: string | null
  created_at: string
}

export type AiApiRequestsPublic = {
  data: AiApiRequestPublic[]
  count: number
}

export type AiApiCredentialPublic = {
  id: string
  request_id: string
  base_url: string
  api_key: string
  api_key_prefix: string
  api_key_name: string
  rate_limit?: number | null
  expires_at?: string | null
  revoked_at?: string | null
  created_at: string
}

export type AiApiCredentialsPublic = {
  data: AiApiCredentialPublic[]
  count: number
}

export type AiApiCredentialAdminStatus = "active" | "inactive"
export type AiApiCredentialInactiveReason = "revoked" | "expired"

export type AiApiCredentialAdminPublic = {
  id: string
  user_id: string
  user_email?: string | null
  user_full_name?: string | null
  request_id: string
  base_url: string
  api_key_prefix: string
  api_key_name: string
  rate_limit?: number | null
  status: AiApiCredentialAdminStatus
  inactive_reason?: AiApiCredentialInactiveReason | null
  expires_at?: string | null
  revoked_at?: string | null
  created_at: string
}

export type AiApiCredentialsAdminPublic = {
  data: AiApiCredentialAdminPublic[]
  count: number
}

export type AiApiCreateRequestBody = {
  purpose: string
  api_key_name: string
  duration?: string
}

export type AiApiReviewRequestBody = {
  status: AiApiRequestStatus
  review_comment?: string | null
}

export type AiApiUpdateCredentialNameBody = {
  api_key_name: string
}

export type AiApiListRequestsQuery = {
  status?: AiApiRequestStatus | null
}

export type AiApiListCredentialsQuery = {
  status?: AiApiCredentialAdminStatus | null
  userEmail?: string | null
  skip?: number
  limit?: number
}

export const AiApiService = {
  createRequest(data: {
    requestBody: AiApiCreateRequestBody
  }): CancelablePromise<AiApiRequestPublic> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/ai-api/requests",
      body: data.requestBody,
      mediaType: "application/json",
      errors: { 422: "Validation Error" },
    })
  },

  listMyRequests(): CancelablePromise<AiApiRequestsPublic> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/ai-api/requests/my",
      errors: { 422: "Validation Error" },
    })
  },

  listAllRequests(
    data?: AiApiListRequestsQuery,
  ): CancelablePromise<AiApiRequestsPublic> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/ai-api/requests",
      query: data?.status ? { status: data.status } : undefined,
      errors: { 422: "Validation Error" },
    })
  },

  reviewRequest(data: {
    requestId: string
    requestBody: AiApiReviewRequestBody
  }): CancelablePromise<AiApiRequestPublic> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/ai-api/requests/{request_id}/review",
      path: { request_id: data.requestId },
      body: data.requestBody,
      mediaType: "application/json",
      errors: { 422: "Validation Error" },
    })
  },

  listMyCredentials(): CancelablePromise<AiApiCredentialsPublic> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/ai-api/credentials/my",
      errors: { 422: "Validation Error" },
    })
  },

  listAllCredentials(
    data?: AiApiListCredentialsQuery,
  ): CancelablePromise<AiApiCredentialsAdminPublic> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/ai-api/credentials",
      query: {
        status: data?.status ?? undefined,
        user_email: data?.userEmail ?? undefined,
        skip: data?.skip,
        limit: data?.limit,
      },
      errors: { 422: "Validation Error" },
    })
  },

  rotateCredential(data: {
    credentialId: string
  }): CancelablePromise<AiApiCredentialPublic> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/ai-api/credentials/{credential_id}/rotate",
      path: { credential_id: data.credentialId },
      errors: { 422: "Validation Error" },
    })
  },

  deleteCredential(data: {
    credentialId: string
  }): CancelablePromise<{ message: string }> {
    return __request(OpenAPI, {
      method: "DELETE",
      url: "/api/v1/ai-api/credentials/{credential_id}",
      path: { credential_id: data.credentialId },
      errors: { 422: "Validation Error" },
    })
  },

  updateCredentialName(data: {
    credentialId: string
    requestBody: AiApiUpdateCredentialNameBody
  }): CancelablePromise<AiApiCredentialPublic> {
    return __request(OpenAPI, {
      method: "PATCH",
      url: "/api/v1/ai-api/credentials/{credential_id}",
      path: { credential_id: data.credentialId },
      body: data.requestBody,
      mediaType: "application/json",
      errors: { 422: "Validation Error" },
    })
  },
}
