import { useQuery, useQueryClient } from "@tanstack/react-query";

import { ProxmoxConfigService } from "../services/proxmoxConfig";
import { LIST_REFRESH_INTERVAL } from "./queryClient";

export const proxmoxConfigKeys = Object.freeze({
  all: ["proxmoxConfig"],
  connections: ["proxmoxConfig", "connections"],
  config: ["proxmoxConfig", "config"],
  nodes: ["proxmoxConfig", "nodes"],
  storages: ["proxmoxConfig", "storages"],
});

/** PVE 連線清單 */
export function usePveConnectionsQuery({ enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({
    queryKey: proxmoxConfigKeys.connections,
    queryFn: () => ProxmoxConfigService.listConnections(),
    enabled,
    refetchInterval,
  });
}

/** 放置與排程策略（表單型：不輪詢、不因切回分頁重抓） */
export function usePveConfigQuery({ enabled = true } = {}) {
  return useQuery({
    queryKey: proxmoxConfigKeys.config,
    queryFn: () => ProxmoxConfigService.getConfig(),
    enabled,
    refetchOnWindowFocus: false,
  });
}

/** 節點清單 */
export function usePveNodesQuery({ enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({
    queryKey: proxmoxConfigKeys.nodes,
    queryFn: () => ProxmoxConfigService.getNodes(),
    enabled,
    refetchInterval,
  });
}

/** Storage 清單（每列可就地改速度等級／優先度，背景不輪詢免得蓋掉輸入中的草稿） */
export function usePveStoragesQuery({ enabled = true } = {}) {
  return useQuery({
    queryKey: proxmoxConfigKeys.storages,
    queryFn: () => ProxmoxConfigService.getStorages(),
    enabled,
    refetchOnWindowFocus: false,
  });
}

export function useProxmoxConfigCacheUpdater() {
  const queryClient = useQueryClient();
  return {
    setConfig(config) {
      queryClient.setQueryData(proxmoxConfigKeys.config, config);
    },
    updateStorage(updated) {
      queryClient.setQueryData(proxmoxConfigKeys.storages, (prev) => (
        Array.isArray(prev) ? prev.map((s) => (s.id === updated.id ? updated : s)) : prev
      ));
    },
    updateNode(updated) {
      queryClient.setQueryData(proxmoxConfigKeys.nodes, (prev) => (
        Array.isArray(prev) ? prev.map((n) => (n.id === updated.id ? updated : n)) : prev
      ));
    },
    /** 連線新增／修改／刪除／同步後：連線、節點、Storage 都重抓 */
    invalidateConnections() {
      return Promise.all([
        queryClient.invalidateQueries({ queryKey: proxmoxConfigKeys.connections }),
        queryClient.invalidateQueries({ queryKey: proxmoxConfigKeys.nodes }),
        queryClient.invalidateQueries({ queryKey: proxmoxConfigKeys.storages }),
      ]);
    },
    invalidate() {
      return queryClient.invalidateQueries({ queryKey: proxmoxConfigKeys.all });
    },
  };
}
