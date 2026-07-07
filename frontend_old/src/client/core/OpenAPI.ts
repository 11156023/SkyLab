/**
 * Compatibility shim for @hey-api/openapi-ts v0.94+
 * Restores the legacy OpenAPI configuration singleton used by hand-written service files.
 */

import type { ApiRequestOptions } from './ApiRequestOptions'

export type OpenAPIConfig = {
  BASE: string
  WITH_CREDENTIALS: boolean
  CREDENTIALS: 'include' | 'omit' | 'same-origin'
  TOKEN?: string | ((options: ApiRequestOptions) => Promise<string>)
  USERNAME?: string
  PASSWORD?: string
  HEADERS?:
    | Record<string, string>
    | ((options: ApiRequestOptions) => Promise<Record<string, string>>)
  ENCODE_PATH?: (path: string) => string
  PREPARE_REQUEST?: (options: ApiRequestOptions) => Promise<void>
}

export const OpenAPI: OpenAPIConfig = {
  BASE: '',
  WITH_CREDENTIALS: false,
  CREDENTIALS: 'include',
}
