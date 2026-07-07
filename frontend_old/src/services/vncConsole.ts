import type { CancelablePromise } from "@/client"
import { OpenAPI } from "@/client"
import { request as __request } from "@/client/core/request"

export type VncConsoleInfo = {
  vmid: number
  ws_url?: string
  ticket?: string | null
  port?: string | null
  message: string
}

export const VncConsoleService = {
  getVmConsole(data: { vmid: number }): CancelablePromise<VncConsoleInfo> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/vm/{vmid}/console",
      path: { vmid: data.vmid },
      errors: { 422: "Validation Error" },
    })
  },
}
