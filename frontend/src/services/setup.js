/**
 * setup.js
 * 首次安裝初始化精靈（/api/v1/setup/*）。
 * 全部是免登入端點，只在後端 `system_setup.completed` 為 false 時可用；完成後一律 403。
 */

import { apiGet, apiPost } from "./api";

/** PVE 測試連線／建立連線會實際打到 Proxmox，逾時要比一般請求長 */
const PROXMOX_TIMEOUT_MS = 90_000;

export const SetupService = {
  /** 初始化進度：{ completed, completed_at, steps: { admin, proxmox, subnet } } */
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

  /** 完成初始化；之後精靈端點全部關閉 */
  complete() {
    return apiPost("/api/v1/setup/complete", {});
  },
};
