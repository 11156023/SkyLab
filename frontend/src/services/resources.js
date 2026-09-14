import { apiDelete, apiDeleteJson, apiGet, apiGetBlob, apiPost, apiPut } from "./api";

export const ResourcesService = {
  /** 克隆機來源範本的使用手冊（資源擁有者即可，不受範本可見範圍影響） */
  getTemplateManual(vmid) {
    return apiGet(`/api/v1/resources/${vmid}/template-manual`);
  },

  /** 下載來源範本手冊（回傳 Blob，配 downloadBlob 使用） */
  downloadTemplateManual(vmid, attachmentId) {
    return apiGetBlob(
      `/api/v1/resources/${vmid}/template-manual/${attachmentId}/download`,
    );
  },

  /** 取得我的資源列表 */
  list(options) {
    return apiGet("/api/v1/resources/my", options);
  },

  /** 取得單一資源 */
  get(vmid) {
    return apiGet(`/api/v1/resources/${vmid}`);
  },

  /** 取得資源目前配置（cpu_cores / memory_mb） */
  getConfig(vmid) {
    return apiGet(`/api/v1/resources/${vmid}/config`);
  },

  /** 取得所有資源列表（管理員） */
  listAll(options) {
    return apiGet("/api/v1/resources/", options);
  },

  /** 啟動 */
  start(vmid) {
    return apiPost(`/api/v1/resources/${vmid}/start`, {});
  },

  /** 強制停止 */
  stop(vmid) {
    return apiPost(`/api/v1/resources/${vmid}/stop`, {});
  },

  /** 正常關機 */
  shutdown(vmid) {
    return apiPost(`/api/v1/resources/${vmid}/shutdown`, {});
  },

  /** 重新啟動 */
  reboot(vmid) {
    return apiPost(`/api/v1/resources/${vmid}/reboot`, {});
  },

  /** 強制重置 */
  reset(vmid) {
    return apiPost(`/api/v1/resources/${vmid}/reset`, {});
  },

  /** 刪除（非同步，返回 202） */
  delete(vmid) {
    return apiDelete(`/api/v1/resources/${vmid}`);
  },

  /** 批次操作（action: start|stop|shutdown|reboot|reset|delete）→ { succeeded, failed } */
  batchAction(vmids, action) {
    return apiPost("/api/v1/resources/batch", { vmids, action });
  },

  /** 練習階段狀態（自動關機／到期警告用）→ { should_warn, warn_reason, ... } */
  sessionStatus(vmid) {
    return apiGet(`/api/v1/resources/${vmid}/session-status`);
  },

  /** 延長練習階段 → { vmid, auto_stop_at, extended_minutes } */
  extendSession(vmid) {
    return apiPost(`/api/v1/resources/${vmid}/extend-session`, {});
  },

  /** 取得 VNC 控制台資訊（QEMU VM） */
  getConsole(vmid) {
    return apiGet(`/api/v1/vm/${vmid}/console`);
  },

  /** 取得 SSH 金鑰 */
  getSshKey(vmid) {
    return apiGet(`/api/v1/resources/${vmid}/ssh-key`);
  },

  /* ── 詳情頁端點（resource_details.py） ── */

  /** 即時狀態（CPU/記憶體/磁碟/網路目前值） */
  getCurrentStats(vmid) {
    return apiGet(`/api/v1/resources/${vmid}/current-stats`);
  },

  /** RRD 歷史趨勢（timeframe: hour|day|week） */
  getStats(vmid, timeframe = "hour") {
    return apiGet(`/api/v1/resources/${vmid}/stats?timeframe=${timeframe}`);
  },

  /** 快照列表 */
  listSnapshots(vmid) {
    return apiGet(`/api/v1/resources/${vmid}/snapshots`);
  },

  /** 建立快照（body: { snapname, description, vmstate }） */
  createSnapshot(vmid, body) {
    return apiPost(`/api/v1/resources/${vmid}/snapshots`, body);
  },

  /** 刪除快照 */
  deleteSnapshot(vmid, snapname) {
    return apiDelete(
      `/api/v1/resources/${vmid}/snapshots/${encodeURIComponent(snapname)}`,
    );
  },

  /** 還原到指定快照 */
  rollbackSnapshot(vmid, snapname) {
    return apiPost(
      `/api/v1/resources/${vmid}/snapshots/${encodeURIComponent(snapname)}/rollback`,
      {},
    );
  },

  /** 管理員直改規格（body: { cores, memory, disk_size }） */
  updateSpecDirect(vmid, body) {
    return apiPut(`/api/v1/resources/${vmid}/spec/direct`, body);
  },

  /** 一鍵重置到初始快照（202，背景任務） */
  resetToInit(vmid) {
    return apiPost(`/api/v1/resources/${vmid}/reset-to-init`, {});
  },

  /** 建立初始快照（教師/管理員） */
  createInitSnapshot(vmid) {
    return apiPost(`/api/v1/resources/${vmid}/init-snapshot`, {});
  },

  /* ── 進階設定端點（resource_settings.py） ── */

  /** 規格摘要 → { cpu_cores, memory_mb, disk_gb, resource_type } */
  getSpecs(vmid) {
    return apiGet(`/api/v1/resources/${vmid}/specs`);
  },

  /** 開機選項 → { boot_order, boot_devices, cdrom_iso, ... }（onboot 由後端隨開關機自動對齊，不在此） */
  getBootOptions(vmid) {
    return apiGet(`/api/v1/resources/${vmid}/boot-options`);
  },

  /** 更新開機選項（body: { boot_order?, cdrom_iso?, eject_cdrom? }） */
  updateBootOptions(vmid, body) {
    return apiPut(`/api/v1/resources/${vmid}/boot-options`, body);
  },

  /** 可掛載的 ISO 映像清單 */
  listIsoImages(vmid) {
    return apiGet(`/api/v1/resources/${vmid}/iso-images`);
  },

  /** 登入憑證狀態（使用者、授權公鑰、是否需執行中） */
  getCredentials(vmid) {
    return apiGet(`/api/v1/resources/${vmid}/credentials`);
  },

  /** 重設登入密碼（password 留空由系統產生）→ { password, applied_immediately, message } */
  resetPassword(vmid, password) {
    return apiPost(`/api/v1/resources/${vmid}/credentials/reset-password`, {
      password: password || null,
    });
  },

  /** 重新產生平台 SSH 金鑰 → { ssh_public_key, ssh_private_key, ... } */
  regenerateSshKey(vmid) {
    return apiPost(`/api/v1/resources/${vmid}/credentials/regenerate-ssh-key`, {});
  },

  /** 匯入自己的公鑰 */
  addAuthorizedKey(vmid, publicKey) {
    return apiPost(`/api/v1/resources/${vmid}/credentials/authorized-keys`, {
      public_key: publicKey,
    });
  },

  /** 移除一把授權公鑰（平台金鑰不可移除） */
  removeAuthorizedKey(vmid, publicKey) {
    return apiDeleteJson(`/api/v1/resources/${vmid}/credentials/authorized-keys`, {
      public_key: publicKey,
    });
  },

  /** 標籤與備註 → { tags, description } */
  getMetadata(vmid) {
    return apiGet(`/api/v1/resources/${vmid}/metadata`);
  },

  /** 更新標籤與備註（body: { tags?, description? }） */
  updateMetadata(vmid, body) {
    return apiPut(`/api/v1/resources/${vmid}/metadata`, body);
  },

  /** 共享名單 */
  listShares(vmid) {
    return apiGet(`/api/v1/resources/${vmid}/shares`);
  },

  /** 用信箱把機器共享給另一位使用者 */
  addShare(vmid, email) {
    return apiPost(`/api/v1/resources/${vmid}/shares`, { email });
  },

  /** 撤銷一筆共享 */
  removeShare(vmid, shareId) {
    return apiDelete(`/api/v1/resources/${vmid}/shares/${shareId}`);
  },

  /** 把機器轉移給另一位使用者（keepAccess 保留自己在共享名單） */
  transferOwnership(vmid, email, keepAccess = false) {
    return apiPost(`/api/v1/resources/${vmid}/transfer`, {
      email,
      keep_access: keepAccess,
    });
  },
};
