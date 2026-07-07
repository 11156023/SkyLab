import { apiGet, apiPost } from "./api";

export const MonitoringService = {
  /** 全域監控匯總：叢集容量/用量、節點與 VM 統計（管理員） */
  getOverview() {
    return apiGet("/api/v1/monitoring/overview");
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

  /** 告警事件列表（active=true 只列未解除的） */
  listAlerts({ active = false, limit = 200 } = {}) {
    const q = new URLSearchParams();
    q.set("active", String(active));
    q.set("limit", String(limit));
    return apiGet(`/api/v1/monitoring/alerts?${q.toString()}`);
  },

  /** 確認（ack）一筆告警 */
  ackAlert(alertId) {
    return apiPost(`/api/v1/monitoring/alerts/${alertId}/ack`, {});
  },
};
