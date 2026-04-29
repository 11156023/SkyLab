import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { ApiError } from "@/client"
import i18n from "@/lib/i18n"
import { AuthSessionService } from "@/services/authSession"

let unauthorizedRecoveryPromise: Promise<void> | null = null

async function recoverUnauthorizedSession() {
  if (unauthorizedRecoveryPromise) {
    return unauthorizedRecoveryPromise
  }

  unauthorizedRecoveryPromise = (async () => {
    const refreshed = await AuthSessionService.refreshAccessToken()
    if (refreshed) {
      await queryClient.invalidateQueries({ refetchType: "active" })
      return
    }

    AuthSessionService.clearTokens()
    toast.error(i18n.t("messages:error.sessionExpired"))
    window.location.href = "/login"
  })().finally(() => {
    unauthorizedRecoveryPromise = null
  })

  return unauthorizedRecoveryPromise
}

export const handleApiError = async (error: Error) => {
  if (error instanceof ApiError && error.status === 401) {
    // Ignore trailing 401 responses from requests that were sent before the
    // previous refresh completed.
    if (!AuthSessionService.wasRefreshedRecently()) {
      await recoverUnauthorizedSession()
    }
    return
  }

  if (error instanceof ApiError && error.status === 403) {
    const detail =
      (error.body as { detail?: string } | undefined)?.detail ??
      i18n.t("messages:error.noPermission")
    toast.error(detail)
  }
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) => {
        if (
          error instanceof ApiError &&
          (error.status === 401 || error.status === 403)
        ) {
          return false
        }
        return failureCount < 3
      },
    },
    mutations: {
      retry: false,
    },
  },
  queryCache: new QueryCache({
    onError: handleApiError,
  }),
  mutationCache: new MutationCache({
    onError: handleApiError,
  }),
})
