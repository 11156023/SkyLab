import { useQuery, useQueryClient } from "@tanstack/react-query";

import { MiningIncidentsService } from "../services/miningIncidents";
import { LIST_REFRESH_INTERVAL } from "./queryClient";

export const miningIncidentKeys = Object.freeze({
  all: ["miningIncidents"],
  list: (params) => ["miningIncidents", "list", params ?? {}],
});

/** 挖礦事件清單；params 與 MiningIncidentsService.list 相同（status／limit） */
export function useMiningIncidentsQuery(params, { enabled = true, refetchInterval = LIST_REFRESH_INTERVAL, select } = {}) {
  return useQuery({
    queryKey: miningIncidentKeys.list(params),
    queryFn: () => MiningIncidentsService.list(params),
    enabled,
    refetchInterval,
    select,
  });
}

export function useMiningIncidentCacheUpdater() {
  const queryClient = useQueryClient();
  return {
    /** 處置（停權／忽略）後：所有篩選的清單都重抓，管理首頁的摘要也會跟著更新 */
    invalidate() {
      return queryClient.invalidateQueries({ queryKey: miningIncidentKeys.all });
    },
  };
}
