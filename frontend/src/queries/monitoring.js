import { useQuery, useQueryClient } from "@tanstack/react-query";

import { MonitoringService } from "../services/monitoring";
import { LIST_REFRESH_INTERVAL } from "./queryClient";

export const monitoringKeys = Object.freeze({
  all: ["monitoring"],
  overview: ["monitoring", "overview"],
  alerts: (params) => ["monitoring", "alerts", params ?? {}],
  systemHealth: ["monitoring", "systemHealth"],
  nodeRrd: (node, timeframe) => ["monitoring", "nodeRrd", String(node), timeframe],
});

/** PVE 全域監控匯總（管理首頁、監控頁共用；原 hooks/usePveOverview） */
export function usePveOverviewQuery({ enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({
    queryKey: monitoringKeys.overview,
    queryFn: ({ signal }) => MonitoringService.getOverview({ signal }),
    enabled,
    refetchInterval,
  });
}

/** 資源告警；params 與 MonitoringService.listAlerts 相同（active／limit） */
export function useAlertsQuery(params, { enabled = true, refetchInterval = LIST_REFRESH_INTERVAL, select } = {}) {
  return useQuery({
    queryKey: monitoringKeys.alerts(params),
    queryFn: () => MonitoringService.listAlerts(params),
    enabled,
    refetchInterval,
    select,
  });
}

/** 平台健康（SystemHealthCard） */
export function useSystemHealthQuery({ enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({
    queryKey: monitoringKeys.systemHealth,
    queryFn: ({ signal }) => MonitoringService.getSystemHealth({ signal }),
    enabled,
    refetchInterval,
  });
}

/** 節點 RRD 圖表資料 */
export function useNodeRrdQuery(node, timeframe = "hour", { enabled = Boolean(node), refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({
    queryKey: monitoringKeys.nodeRrd(node, timeframe),
    queryFn: () => MonitoringService.getNodeRrd(node, timeframe),
    enabled,
    refetchInterval,
  });
}

export function useMonitoringCacheUpdater() {
  const queryClient = useQueryClient();
  return {
    invalidateAlerts() {
      return queryClient.invalidateQueries({ queryKey: ["monitoring", "alerts"] });
    },
    invalidate() {
      return queryClient.invalidateQueries({ queryKey: monitoringKeys.all });
    },
  };
}
