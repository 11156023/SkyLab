import { apiGet, apiGetBlob, apiPost, apiPut } from "./api";

export const GatewayService = {
  /** 取得 Gateway VM 連線設定 */
  getConfig() {
    return apiGet("/api/v1/gateway/config");
  },

  /** 更新連線設定（host / ssh_port / ssh_user） */
  updateConfig(body) {
    return apiPut("/api/v1/gateway/config", body);
  },

  /** 生成新的 SSH Keypair */
  generateKeypair() {
    return apiPost("/api/v1/gateway/generate-keypair");
  },

  /** 測試 SSH 連線 */
  testConnection() {
    return apiPost("/api/v1/gateway/test-connection");
  },

  /** 套用 Cloudflare DNS Challenge 到 Traefik */
  syncTraefikDnsChallenge() {
    return apiPost("/api/v1/gateway/traefik/dns-challenge/sync");
  },

  /** 讀取服務設定檔（haproxy / traefik / frps / frpc） */
  readServiceConfig(service) {
    return apiGet(`/api/v1/gateway/services/${service}/config`);
  },

  /** 寫入服務設定檔 */
  writeServiceConfig(service, content) {
    return apiPut(`/api/v1/gateway/services/${service}/config`, { content });
  },

  /** 取得服務狀態 */
  getServiceStatus(service) {
    return apiGet(`/api/v1/gateway/services/${service}/status`);
  },

  /** 取得所有服務版本資訊 */
  getServiceVersions() {
    return apiGet("/api/v1/gateway/services/versions");
  },

  /** 控制服務（start / stop / restart / reload） */
  controlService(service, action) {
    return apiPost(`/api/v1/gateway/services/${service}/${action}`);
  },

  /** 取得服務日誌（純文字） */
  async getServiceLogs(service, lines = 100) {
    const blob = await apiGetBlob(`/api/v1/gateway/services/${service}/logs?lines=${lines}`);
    return blob.text();
  },
};
