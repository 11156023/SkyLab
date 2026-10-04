import { apiDelete, apiDeleteJson, apiGet, apiGetBlob, apiPost, apiPut } from "./api";

/** 每批台數：10 台 LXC 開機（含補金鑰／密碼／防火牆）遠低於代理逾時 */
export const BATCH_CHUNK_SIZE = 10;

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

  /**
   * 分批送出的批次操作，回傳合併後的 { total, succeeded, failed, results }。
   *
   * 後端一次最多收 100 台，而且在同一個請求裡逐台開機（LXC 還要補金鑰、
   * 密碼與防火牆），整班一起送會撞到代理的 130 秒逾時。拆小批依序送，
   * 某一批請求失敗只記成那批失敗，其餘照送。
   */
  async batchActionInChunks(vmids, action, { chunkSize = BATCH_CHUNK_SIZE, onProgress } = {}) {
    const results = [];
    for (let index = 0; index < vmids.length; index += chunkSize) {
      const chunk = vmids.slice(index, index + chunkSize);
      try {
        const response = await ResourcesService.batchAction(chunk, action);
        results.push(...(response?.results ?? []));
      } catch (error) {
        const message = error?.message ?? "Request failed";
        results.push(...chunk.map((vmid) => ({ vmid, success: false, message })));
      }
      onProgress?.({ done: Math.min(index + chunkSize, vmids.length), total: vmids.length });
    }
    const succeeded = results.filter((item) => item.success).length;
    return { total: results.length, succeeded, failed: results.length - succeeded, results };
  },

  /** 練習階段狀態（自動關機／到期警告用）→ { should_warn, warn_reason, ... } */
  sessionStatus(vmid) {
    return apiGet(`/api/v1/resources/${vmid}/session-status`);
  },

  /** 本人所有執行中機器的練習階段狀態（一次取回，取代逐台 sessionStatus） */
  mySessionStatuses() {
    return apiGet("/api/v1/resources/my/session-status");
  },

  /** 延長練習階段 → { vmid, auto_stop_at, extended_minutes } */
  extendSession(vmid) {
    return apiPost(`/api/v1/resources/${vmid}/extend-session`, {});
  },

  /** 平台提供的 LXC 作業系統映像清單 */
  listLxcOsImages() {
    return apiGet("/api/v1/lxc/templates");
  },

  /** 平台提供的 VM 作業系統範本清單 */
  listVmOsTemplates() {
    return apiGet("/api/v1/vm/templates");
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

  /**
   * 這台機器當下能否使用快照 → { available, reason }
   * （reason: "unsupported"＝磁碟所在 storage 不支援、"unknown"＝查不到；可用時為 null）。
   * 不可用時整組快照功能（含一鍵重置、初始快照）都要藏起來，後端也會拒絕這些操作。
   */
  getSnapshotCapability(vmid) {
    return apiGet(`/api/v1/resources/${vmid}/snapshot-capability`);
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

  /* ── 備份（快照不能用的機器以備份當還原點；備份與還原都是背景任務） ── */

  /**
   * 這台機器能否使用備份 → { available, reason, requires_shutdown, max_count }
   * （reason: "not_configured"＝所屬叢集沒設備份 storage、"storage_unavailable"、"unknown"；
   * requires_shutdown＝備份期間機器會先關機；max_count＝可保留份數，null 表示不限）。
   */
  getBackupCapability(vmid) {
    return apiGet(`/api/v1/resources/${vmid}/backup-capability`);
  },

  /** 備份列表 → [{ volid, created_at, size, description }]（只含 SkyLab 為這台機器建立的備份） */
  listBackups(vmid) {
    return apiGet(`/api/v1/resources/${vmid}/backups`);
  },

  /** 建立備份（202，背景任務；body: { description? }）→ { message, task_id } */
  createBackup(vmid, body = {}) {
    return apiPost(`/api/v1/resources/${vmid}/backups`, body);
  },

  /** 以備份還原整台機器（202，背景任務）→ { message, task_id } */
  restoreBackup(vmid, volid) {
    return apiPost(`/api/v1/resources/${vmid}/backups/restore`, { volid });
  },

  /** 刪除備份；volid 含斜線與冒號，放 query 而不是路徑 */
  deleteBackup(vmid, volid) {
    return apiDelete(
      `/api/v1/resources/${vmid}/backups?volid=${encodeURIComponent(volid)}`,
    );
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

  /** 重設登入密碼（password 留空由系統產生）→ { password, applied_immediately, rebooting, message }；執行中的 VM 會自動重新開機套用 */
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
