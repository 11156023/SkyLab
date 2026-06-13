import { apiDelete, apiGet, apiPost } from "./api";

export const ResourcesService = {
  /** 取得我的資源列表 */
  list() {
    return apiGet("/api/v1/resources/my");
  },

  /** 取得所有資源列表（管理員） */
  listAll() {
    return apiGet("/api/v1/resources/");
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

  /** 取得 VNC 控制台資訊（QEMU VM） */
  getConsole(vmid) {
    return apiGet(`/api/v1/vm/${vmid}/console`);
  },

  /** 取得 SSH 金鑰 */
  getSshKey(vmid) {
    return apiGet(`/api/v1/resources/${vmid}/ssh-key`);
  },
};
