// MUST come first — polyfills `crypto.randomUUID` for HTTP non-secure contexts
// (LAN IP deploy) before any dependency's top-level code can throw.
import "./lib/polyfills"

import { QueryClientProvider } from "@tanstack/react-query"
import { createRouter, RouterProvider } from "@tanstack/react-router"
import type { AxiosError } from "axios"
import { StrictMode } from "react"
import ReactDOM from "react-dom/client"

import { ApiError, client, OpenAPI } from "./client"
import { ThemeProvider } from "./components/theme-provider"
import { Toaster } from "./components/ui/sonner"
import "./index.css"
import "./lib/i18n"
import { queryClient } from "./lib/queryClient"
import { LanguageProvider } from "./providers/LanguageProvider"
import { routeTree } from "./routeTree.gen"
import { AuthSessionService } from "./services/authSession"
import {
  isTokenExpiredOrNearExpiry,
  prepareOpenApiRequestAuth,
  resolveOpenApiToken,
} from "./services/openApiAuth"

const apiBaseUrl = import.meta.env.VITE_API_URL

// Configure legacy hand-written services (use OpenAPI singleton + fetch shim)
OpenAPI.BASE = apiBaseUrl
OpenAPI.PREPARE_REQUEST = prepareOpenApiRequestAuth
OpenAPI.TOKEN = resolveOpenApiToken

// Configure the generated axios client (used by auto-generated SDK endpoints)
client.setConfig({
  baseURL: apiBaseUrl,
  throwOnError: true,
  // Proactive token refresh: same logic as prepareOpenApiRequestAuth so both
  // the legacy fetch shim and the generated axios SDK behave consistently.
  auth: async () => {
    const token = AuthSessionService.getAccessToken()
    if (!token) return undefined
    if (isTokenExpiredOrNearExpiry(token)) {
      await AuthSessionService.refreshAccessToken()
      return AuthSessionService.getAccessToken() ?? undefined
    }
    return token
  },
})

// Convert axios errors into ApiError so `instanceof ApiError` checks
// (in queryClient.ts and elsewhere) work uniformly across both code paths.
client.instance.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    const status = error.response?.status ?? 0
    const apiError = new ApiError({
      url: error.config?.url ?? "",
      status,
      statusText: error.response?.statusText ?? error.message,
      body: error.response?.data ?? null,
      message:
        ((error.response?.data as { detail?: string; message?: string } | null)
          ?.detail ??
          (error.response?.data as { detail?: string; message?: string } | null)
            ?.message) ||
        error.message ||
        `HTTP ${status}`,
    })
    return Promise.reject(apiError)
  },
)

// 跟 vite `base` 同步：production build 時 BASE_URL = "/old/"，
// router basepath 接收不含尾巴 slash 的字串。
const basepath = import.meta.env.BASE_URL.replace(/\/$/, "") || undefined
const router = createRouter({ routeTree, basepath })

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router
  }
}

const rootElement = document.getElementById("root")

if (!rootElement) {
  throw new Error("Root element not found")
}

ReactDOM.createRoot(rootElement).render(
  <StrictMode>
    <LanguageProvider defaultLanguage="zh-TW" storageKey="SkyLab-language">
      <ThemeProvider defaultTheme="light" storageKey="vite-ui-theme">
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} />
          <Toaster richColors closeButton />
        </QueryClientProvider>
      </ThemeProvider>
    </LanguageProvider>
  </StrictMode>,
)
