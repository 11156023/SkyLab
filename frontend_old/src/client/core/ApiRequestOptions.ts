/**
 * Compatibility shim for @hey-api/openapi-ts v0.94+
 * Restores the legacy ApiRequestOptions type used by hand-written service files.
 */

export type ApiRequestOptions<T = unknown> = {
  readonly method: 'DELETE' | 'GET' | 'HEAD' | 'OPTIONS' | 'PATCH' | 'POST' | 'PUT'
  readonly url: string
  readonly path?: Record<string, unknown>
  readonly query?: Record<string, unknown>
  readonly headers?: Record<string, string>
  readonly body?: T
  readonly formData?: T
  readonly mediaType?: string
  readonly responseHeader?: string
  readonly errors?: Record<number, string>
}
