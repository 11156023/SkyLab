/**
 * Gateway VM 管理 API 服務
 */

import type { CancelablePromise } from "@/client"
import { OpenAPI } from "@/client"
import { request as __request } from "@/client/core/request"

export type GatewayConfigPublic = {
  host: string
  ssh_port: number
  ssh_user: string
  public_key: string
  is_configured: boolean
}

export type GatewayConfigUpdate = {
  host: string
  ssh_port: number
  ssh_user: string
}

export type GatewayConnectionTestResult = {
  success: boolean
  message: string
}

export type ServiceConfigRead = {
  service: string
  content: string
}

export type ServiceStatusResult = {
  service: string
  active: boolean
  status_text: string
}

export type ServiceActionResult = {
  service: string
  action: string
  success: boolean
  output: string
}

export type GatewayServiceVersionInfo = {
  service: GatewayService
  current_version: string | null
  target_version: string | null
  update_available: boolean | null
  source: string
  detection_error: string | null
}

export type GatewayServiceVersionsResult = {
  items: GatewayServiceVersionInfo[]
  checked_at: string
}

export type GatewayService = "haproxy" | "traefik" | "frps" | "frpc"
export type ServiceAction = "start" | "stop" | "restart" | "reload"

export const GatewayApiService = {
  /** 取得 Gateway VM 連線設定 */
  getConfig(): CancelablePromise<GatewayConfigPublic> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/gateway/config",
    })
  },

  /** 更新連線設定 */
  updateConfig(
    data: GatewayConfigUpdate,
  ): CancelablePromise<GatewayConfigPublic> {
    return __request(OpenAPI, {
      method: "PUT",
      url: "/api/v1/gateway/config",
      body: data,
      mediaType: "application/json",
    })
  },

  /** 生成新的 SSH Keypair */
  generateKeypair(): CancelablePromise<GatewayConfigPublic> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/gateway/generate-keypair",
    })
  },

  /** 測試 SSH 連線 */
  testConnection(): CancelablePromise<GatewayConnectionTestResult> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/gateway/test-connection",
    })
  },

  /** 套用 Cloudflare DNS Challenge 到 Traefik */
  syncTraefikDnsChallenge(): CancelablePromise<{ message: string }> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/gateway/traefik/dns-challenge/sync",
    })
  },

  /** 讀取服務設定檔 */
  readServiceConfig(
    service: GatewayService,
  ): CancelablePromise<ServiceConfigRead> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/gateway/services/{service}/config",
      path: { service },
    })
  },

  /** 寫入服務設定檔 */
  writeServiceConfig(
    service: GatewayService,
    content: string,
  ): CancelablePromise<{ message: string }> {
    return __request(OpenAPI, {
      method: "PUT",
      url: "/api/v1/gateway/services/{service}/config",
      path: { service },
      body: { content },
      mediaType: "application/json",
    })
  },

  /** 取得服務狀態 */
  getServiceStatus(
    service: GatewayService,
  ): CancelablePromise<ServiceStatusResult> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/gateway/services/{service}/status",
      path: { service },
    })
  },

  /** 取得 Gateway 服務版本資訊 */
  getServiceVersions(): CancelablePromise<GatewayServiceVersionsResult> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/gateway/services/versions",
    })
  },

  /** 控制服務 */
  controlService(
    service: GatewayService,
    action: ServiceAction,
  ): CancelablePromise<ServiceActionResult> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/gateway/services/{service}/{action}",
      path: { service, action },
    })
  },

  /** 取得服務日誌 */
  async getServiceLogs(
    service: GatewayService,
    lines: number = 50,
  ): Promise<string> {
    const token =
      typeof OpenAPI.TOKEN === "function"
        ? await (
            OpenAPI.TOKEN as (options: {
              method: string
              url: string
            }) => Promise<string>
          )({
            method: "GET",
            url: `/api/v1/gateway/services/${service}/logs`,
          })
        : (OpenAPI.TOKEN as string)
    const resp = await fetch(
      `${OpenAPI.BASE}/api/v1/gateway/services/${service}/logs?lines=${lines}`,
      {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      },
    )
    return resp.text()
  },

  /** 取得安裝腳本下載 URL */
  getInstallScriptUrl(): string {
    return `${OpenAPI.BASE}/api/v1/gateway/install-script`
  },
}
