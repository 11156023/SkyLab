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

  /** 重設 pinned SSH host key（Gateway VM 重灌後 host key 變更時使用） */
  resetHostKey() {
    return apiPost("/api/v1/gateway/reset-host-key");
  },

  /** 讀取一鍵安裝的狀態、日誌、Gateway 網卡與建議參數（經 SSH） */
  getInstallStatus() {
    return apiGet("/api/v1/gateway/install");
  },

  /** 以已綁定的 SSH 金鑰在 Gateway 背景執行 install.sh，回傳啟動後的狀態 */
  startInstall(options) {
    return apiPost("/api/v1/gateway/install", options);
  },

  /** 讀取可安全編輯的服務設定檔（nginx） */
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

  /** Return a WireGuard runtime summary without private or peer keys. */
  getWireGuardOverview() {
    return apiGet("/api/v1/gateway/wireguard/overview");
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
