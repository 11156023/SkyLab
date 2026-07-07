/**
 * Compatibility shim for @hey-api/openapi-ts v0.94+
 * Provides the legacy `request(OpenAPI, options)` function used by hand-written service files.
 * Uses the native fetch API to avoid coupling to the generated axios client.
 */

import { ApiError } from './ApiError'
import type { ApiRequestOptions } from './ApiRequestOptions'
import type { OpenAPIConfig } from './OpenAPI'

function resolvePath(url: string, path?: Record<string, unknown>): string {
  if (!path) return url
  return url.replace(/\{(\w+)\}/g, (_, key) => {
    return path[key] !== undefined ? encodeURIComponent(String(path[key])) : `{${key}}`
  })
}

export function request<T>(
  config: OpenAPIConfig,
  options: ApiRequestOptions,
): Promise<T> {
  return (async () => {
    // Run PREPARE_REQUEST interceptor (handles token refresh / expiry checks)
    if (config.PREPARE_REQUEST) {
      await config.PREPARE_REQUEST(options)
    }

    // Resolve auth token
    let authToken: string | undefined
    if (config.TOKEN) {
      const resolved =
        typeof config.TOKEN === 'function'
          ? await config.TOKEN(options)
          : config.TOKEN
      if (resolved) authToken = resolved
    }

    // Build URL with path params
    const base = (config.BASE ?? '').replace(/\/+$/, '')
    const resolvedPath = resolvePath(options.url, options.path)

    // Build query string
    let urlStr = `${base}${resolvedPath}`
    if (options.query) {
      const params = new URLSearchParams()
      for (const [key, value] of Object.entries(options.query)) {
        if (value === undefined || value === null) continue
        if (Array.isArray(value)) {
          for (const item of value) params.append(key, String(item))
        } else {
          params.append(key, String(value))
        }
      }
      const qs = params.toString()
      if (qs) urlStr += `?${qs}`
    }

    // Build headers
    const headers: Record<string, string> = {}
    if (options.headers) Object.assign(headers, options.headers)
    if (authToken) headers['Authorization'] = `Bearer ${authToken}`

    // Build body
    let body: BodyInit | undefined
    const mediaType = options.mediaType ?? 'application/json'

    if (options.formData !== undefined) {
      const fd = new FormData()
      const data = options.formData as Record<string, unknown>
      for (const [key, value] of Object.entries(data)) {
        if (value instanceof File || value instanceof Blob) fd.append(key, value)
        else if (Array.isArray(value)) {
          // FastAPI 的 list Form 欄位需要重複欄位而非逗號串接
          for (const item of value) fd.append(key, String(item))
        } else if (value !== undefined && value !== null) fd.append(key, String(value))
      }
      body = fd
    } else if (options.body !== undefined) {
      if (mediaType === 'multipart/form-data') {
        const fd = new FormData()
        const data = options.body as Record<string, unknown>
        for (const [key, value] of Object.entries(data)) {
          if (value instanceof File || value instanceof Blob) fd.append(key, value)
          else if (value !== undefined && value !== null) fd.append(key, String(value))
        }
        body = fd
      } else {
        headers['Content-Type'] = mediaType
        body = JSON.stringify(options.body)
      }
    }

    const response = await fetch(urlStr, {
      method: options.method,
      headers,
      body,
      credentials: config.WITH_CREDENTIALS ? config.CREDENTIALS : 'same-origin',
    })

    if (!response.ok) {
      let body: unknown
      try {
        body = await response.json()
      } catch {
        body = await response.text().catch(() => '')
      }
      const message =
        (body as { detail?: string; message?: string } | null)?.detail ??
        (body as { detail?: string; message?: string } | null)?.message ??
        `HTTP ${response.status}`
      throw new ApiError({
        url: urlStr,
        status: response.status,
        statusText: response.statusText,
        body,
        message,
      })
    }

    if (response.status === 204) {
      return undefined as unknown as T
    }

    const contentType = response.headers.get('content-type') ?? ''
    if (contentType.includes('application/json')) {
      return response.json() as Promise<T>
    }
    return response.text() as unknown as Promise<T>
  })()
}
