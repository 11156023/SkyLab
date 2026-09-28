import { apiDelete, apiGet, apiPost, apiPut } from "./api";

export const ProxmoxConfigService = {
  /** 連線列表（多 PVE 入口） */
  listConnections() {
    return apiGet("/api/v1/proxmox-config/connections");
  },

  /**
   * 新增連線。
   * 連線層：name / host / port / user / password / verify_ssl / ca_cert / api_timeout
   * 該叢集自身：pool_name / iso_storage / data_storage / task_check_interval /
   *             gateway_ip / local_subnet / default_node
   * 其他：enabled / is_default
   */
  createConnection(body) {
    return apiPost("/api/v1/proxmox-config/connections", body);
  },

  /** 更新連線（password 為 null 表示不更新） */
  updateConnection(connectionId, body) {
    return apiPut(`/api/v1/proxmox-config/connections/${connectionId}`, body);
  },

  /** 刪除連線（其節點與 Storage 記錄一併移除）；force=true 只在 PVE 永久連不上（後端回 503）時使用 */
  deleteConnection(connectionId, { force = false } = {}) {
    return apiDelete(
      `/api/v1/proxmox-config/connections/${connectionId}${force ? "?force=true" : ""}`,
    );
  },

  /** 測試指定連線 */
  testConnectionById(connectionId) {
    return apiPost(`/api/v1/proxmox-config/connections/${connectionId}/test`);
  },

  /** 同步指定連線的節點與 Storage */
  syncConnection(connectionId) {
    return apiPost(`/api/v1/proxmox-config/connections/${connectionId}/sync`);
  },

  /** 取得放置與排程策略（PVE 連線本身見 connections） */
  getConfig() {
    return apiGet("/api/v1/proxmox-config/");
  },

  /** 更新放置與排程策略（部分更新，只送要改的欄位） */
  updateConfig(body) {
    return apiPut("/api/v1/proxmox-config/", body);
  },

  /** 節點列表 */
  getNodes() {
    return apiGet("/api/v1/proxmox-config/nodes");
  },

  /** 更新節點（host / port / priority） */
  updateNode(nodeId, body) {
    return apiPut(`/api/v1/proxmox-config/nodes/${nodeId}`, body);
  },

  /**
   * Storage 列表。共享 Storage 是叢集共用同一份實體儲存，每個連線只回一筆，
   * 涵蓋的節點在 node_names；非共享（local / local-lvm）仍逐節點列出。
   */
  getStorages() {
    return apiGet("/api/v1/proxmox-config/storages");
  },

  /** 更新 Storage（enabled / speed_tier / user_priority；共享者整組套用） */
  updateStorage(storageId, body) {
    return apiPut(`/api/v1/proxmox-config/storages/${storageId}`, body);
  },
};
