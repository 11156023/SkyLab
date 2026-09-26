import { apiGet, apiPost } from "./api";

export const MonitoringService = {
  /** 全域監控匯總：叢集容量/用量、節點與 VM 統計（管理員） */
  getOverview(options) {
    return apiGet("/api/v1/monitoring/overview", options);
  },

  /** 節點 RRD 趨勢（timeframe: hour|day|week） */
  getNodeRrd(node, timeframe = "hour") {
    return apiGet(
      `/api/v1/monitoring/nodes/${encodeURIComponent(node)}/rrd?timeframe=${timeframe}`,
    );
  },

  /** VM/LXC RRD 趨勢（擁有者或管理員） */
  getVmRrd(vmid, timeframe = "hour") {
    return apiGet(`/api/v1/monitoring/vms/${vmid}/rrd?timeframe=${timeframe}`);
  },

  /** 警告事件列表（active=true 只列未解除的） */
  listAlerts({ active = false, limit = 200 } = {}) {
    const q = new URLSearchParams();
    q.set("active", String(active));
    q.set("limit", String(limit));
    return apiGet(`/api/v1/monitoring/alerts?${q.toString()}`);
  },

  /** 確認（ack）一筆警告 */
  ackAlert(alertId) {
    return apiPost(`/api/v1/monitoring/alerts/${alertId}/ack`, {});
  },

  /** 平台健康：DB、Redis、worker、PVE 連線與排程任務心跳（管理員） */
  getSystemHealth(options) {
    return apiGet("/api/v1/monitoring/system-health", options);
  },

  /** 監控 stack 的 Grafana 是否啟用：{ enabled, url }（未啟用時 url 為 null；管理員） */
  getGrafanaLink(options) {
    return apiGet("/api/v1/monitoring/grafana", options);
  },
};
