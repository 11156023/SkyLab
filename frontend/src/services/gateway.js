import { apiGet, apiGetBlob, apiPost, apiPut } from "./api";

/** 平台入口的狀態／測試要 SSH 到 Gateway 再連主系統，比一般請求慢 */
export const PLATFORM_ENTRY_PROBE_TIMEOUT_MS = 45_000;
/** 儲存平台入口會同步 nginx，第一次還要等 certbot 簽憑證（主系統 nginx 的 /api 上限是 130 秒） */
export const PLATFORM_ENTRY_SAVE_TIMEOUT_MS = 120_000;

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

  /** 平台入口設定：主系統經 Gateway nginx 對外的網域與上游 */
  getPlatformEntry() {
    return apiGet("/api/v1/gateway/platform-entry");
  },

  /** 儲存平台入口並同步到 Gateway 的 nginx（後端會先從 Gateway 測試上游，連不到不存） */
  updatePlatformEntry(body) {
    return apiPut("/api/v1/gateway/platform-entry", body, {
      timeoutMs: PLATFORM_ENTRY_SAVE_TIMEOUT_MS,
    });
  },

  /** 從 Gateway 連一次主系統入口，回 { reachable, detail }（不儲存） */
  testPlatformEntryUpstream(body) {
    return apiPost("/api/v1/gateway/platform-entry/test-upstream", body, {
      timeoutMs: PLATFORM_ENTRY_PROBE_TIMEOUT_MS,
    });
  },

  /** Gateway 上實際套用的平台入口、憑證與上游狀態（經 SSH 讀回） */
  getPlatformEntryStatus() {
    return apiGet("/api/v1/gateway/platform-entry/status", {
      timeoutMs: PLATFORM_ENTRY_PROBE_TIMEOUT_MS,
    });
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
