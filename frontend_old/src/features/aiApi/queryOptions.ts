import { queryOptions } from "@tanstack/react-query"

import { queryKeys } from "@/lib/queryKeys"
import {
  type AiApiCredentialAdminStatus,
  type AiApiRequestStatus,
  AiApiService,
} from "@/services/aiApi"

export function aiApiMyRequestsQueryOptions() {
  return queryOptions({
    queryKey: queryKeys.aiApi.myRequests,
    queryFn: () => AiApiService.listMyRequests(),
  })
}

export function aiApiMyCredentialsQueryOptions() {
  return queryOptions({
    queryKey: queryKeys.aiApi.myCredentials,
    queryFn: () => AiApiService.listMyCredentials(),
  })
}

export function aiApiAdminRequestsQueryOptions(
  status: AiApiRequestStatus | "all",
) {
  return queryOptions({
    queryKey: queryKeys.aiApi.adminRequestsList(status),
    queryFn: () =>
      AiApiService.listAllRequests({
        status: status === "all" ? null : status,
      }),
  })
}

export function aiApiAdminCredentialsQueryOptions(query: {
  skip: number
  limit: number
  status: AiApiCredentialAdminStatus | "all"
  userEmail: string
}) {
  return queryOptions({
    queryKey: queryKeys.aiApi.adminCredentialsList(query),
    queryFn: () =>
      AiApiService.listAllCredentials({
        skip: query.skip,
        limit: query.limit,
        status: query.status === "all" ? undefined : query.status,
        userEmail: query.userEmail || undefined,
      }),
  })
}

export function aiApiAdminCredentialsCountQueryOptions(
  status: AiApiCredentialAdminStatus | "all",
) {
  return queryOptions({
    queryKey: queryKeys.aiApi.adminCredentialsCount(status),
    queryFn: () =>
      AiApiService.listAllCredentials({
        status: status === "all" ? undefined : status,
        skip: 0,
        limit: 1,
      }),
  })
}
