/**
 * setup.js
 * 首次安裝初始化精靈（/api/v1/setup/*）。
 * 全部是免登入端點，只在後端 `system_setup.completed` 為 false 時可用；完成後一律 403。
 */

import { apiGet, apiPost, apiPut } from "./api";
import {
  PLATFORM_ENTRY_PROBE_TIMEOUT_MS,
  PLATFORM_ENTRY_SAVE_TIMEOUT_MS,
} from "./gateway";

/** PVE 測試連線／建立連線會實際打到 Proxmox，逾時要比一般請求長 */
const PROXMOX_TIMEOUT_MS = 90_000;
/** Gateway 的測試與安裝狀態都要先 SSH 連上去 */
const GATEWAY_TIMEOUT_MS = 45_000;

export const SetupService = {
  /** 初始化進度：{ completed, completed_at, steps: { admin, proxmox, subnet, gateway, platform_entry } } */
  getStatus(options = {}) {
    return apiGet("/api/v1/setup/status", options);
  },

  /** 步驟一：建立系統管理員（信箱已是超級使用者時改為接管） */
  createAdmin(body) {
    return apiPost("/api/v1/setup/admin", body);
  },

  /** 步驟二：用表單內容測試 PVE 連線，回 { success, is_cluster, nodes, storages, error } */
  testProxmox(body) {
    return apiPost("/api/v1/setup/proxmox/test", body, { timeoutMs: PROXMOX_TIMEOUT_MS });
  },

  /** 步驟二：建立第一組 PVE 連線並同步節點／Storage */
  createProxmox(body) {
    return apiPost("/api/v1/setup/proxmox", body, { timeoutMs: PROXMOX_TIMEOUT_MS });
  },

  /** 步驟三：設定實驗室 IP 網段（欄位同 IP 管理頁的子網設定） */
  configureSubnet(body) {
    return apiPost("/api/v1/setup/subnet", body);
  },

  /** 步驟四：目前的 Gateway 連線設定與公鑰 { host, ssh_port, ssh_user, public_key, is_configured } */
  getGateway() {
    return apiGet("/api/v1/setup/gateway");
  },

  /** 步驟四：儲存 Gateway 的 SSH 連線設定；還沒有金鑰時後端會產生一組並回傳公鑰 */
  saveGateway(body) {
    return apiPut("/api/v1/setup/gateway", body);
  },

  /** 步驟四：用已儲存的設定測試 SSH 連線，回 { success, message } */
  testGateway() {
    return apiPost("/api/v1/setup/gateway/test", {}, { timeoutMs: GATEWAY_TIMEOUT_MS });
  },

  /** 步驟四：Gateway 一鍵安裝的狀態、日誌、網卡與建議參數（經 SSH） */
  getGatewayInstallStatus() {
    return apiGet("/api/v1/setup/gateway/install", { timeoutMs: GATEWAY_TIMEOUT_MS });
  },

  /** 步驟四：在 Gateway 背景執行 install.sh，回傳啟動後的狀態 */
  startGatewayInstall(options) {
    return apiPost("/api/v1/setup/gateway/install", options, { timeoutMs: GATEWAY_TIMEOUT_MS });
  },

  /** 步驟五：目前的平台入口設定 */
  getPlatformEntry() {
    return apiGet("/api/v1/setup/platform-entry");
  },

  /** 步驟五：從 Gateway 連一次主系統入口，回 { reachable, detail } */
  testPlatformEntryUpstream(body) {
    return apiPost("/api/v1/setup/platform-entry/test-upstream", body, {
      timeoutMs: PLATFORM_ENTRY_PROBE_TIMEOUT_MS,
    });
  },

  /** 步驟五：儲存平台入口並同步到 Gateway 的 nginx；body 可帶 cloudflare_api_token */
  savePlatformEntry(body) {
    return apiPut("/api/v1/setup/platform-entry", body, {
      timeoutMs: PLATFORM_ENTRY_SAVE_TIMEOUT_MS,
    });
  },

  /** 完成初始化；之後精靈端點全部關閉 */
  complete() {
    return apiPost("/api/v1/setup/complete", {});
  },
};
