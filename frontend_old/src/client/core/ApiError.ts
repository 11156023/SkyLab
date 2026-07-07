/**
 * Compatibility shim for @hey-api/openapi-ts v0.94+
 *
 * Restores the legacy ApiError class used by hand-written service files,
 * queryClient, and the existing test suite.
 *
 * Supports both calling conventions:
 *   - new-style: `new ApiError({ url, status, statusText, body, message })`
 *   - legacy 3-arg: `new ApiError(request, response, message)`
 */

interface NewStyleArgs {
  url: string
  status: number
  statusText: string
  body: unknown
  message: string
}

interface LegacyRequest {
  method?: string
  url?: string
  [k: string]: unknown
}

interface LegacyResponse {
  url?: string
  status: number
  statusText: string
  body?: unknown
  ok?: boolean
  [k: string]: unknown
}

function isNewStyle(arg: unknown): arg is NewStyleArgs {
  return (
    typeof arg === "object" &&
    arg !== null &&
    "status" in arg &&
    "statusText" in arg &&
    "body" in arg &&
    "url" in arg &&
    "message" in arg
  )
}

export class ApiError extends Error {
  public readonly status: number
  public readonly statusText: string
  public readonly body: unknown
  public readonly url: string
  public readonly request?: LegacyRequest

  constructor(args: NewStyleArgs)
  constructor(request: LegacyRequest, response: LegacyResponse, message: string)
  constructor(
    arg1: NewStyleArgs | LegacyRequest,
    arg2?: LegacyResponse,
    arg3?: string,
  ) {
    if (isNewStyle(arg1)) {
      super(arg1.message)
      this.name = "ApiError"
      this.url = arg1.url
      this.status = arg1.status
      this.statusText = arg1.statusText
      this.body = arg1.body
      return
    }
    // Legacy 3-arg form
    const request = arg1 as LegacyRequest
    const response = arg2 as LegacyResponse
    super(arg3 ?? response.statusText)
    this.name = "ApiError"
    this.request = request
    this.url = response.url ?? request.url ?? ""
    this.status = response.status
    this.statusText = response.statusText
    this.body = response.body
  }
}
