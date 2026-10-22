import { useQuery, useQueryClient } from "@tanstack/react-query";

import { IpManagementService } from "../services/ipManagement";
import { LIST_REFRESH_INTERVAL } from "./queryClient";

export const ipManagementKeys = Object.freeze({
  all: ["ipManagement"],
  status: ["ipManagement", "status"],
  subnet: ["ipManagement", "subnet"],
  allocations: ["ipManagement", "allocations"],
});

/**
 * 網段狀態（SubnetBanner 的提示、IP 管理頁共用）。
 * `staleTime` 省略時用全站預設；全站橫幅給較長的值，切回分頁時不必每次都重查。
 */
export function useIpStatusQuery({ enabled = true, refetchInterval = LIST_REFRESH_INTERVAL, staleTime } = {}) {
  return useQuery({
    queryKey: ipManagementKeys.status,
    queryFn: () => IpManagementService.getStatus(),
    enabled,
    refetchInterval,
    /* 傳 undefined 會蓋掉全站預設的 staleTime，沒給就不放這個欄位 */
    ...(staleTime !== undefined && { staleTime }),
  });
}

/** 子網設定（管理員；尚未設定時回 null） */
export function useSubnetQuery({ enabled = true, refetchInterval = false } = {}) {
  return useQuery({
    queryKey: ipManagementKeys.subnet,
    queryFn: () => IpManagementService.getSubnet(),
    enabled,
    refetchInterval,
  });
}

export function useIpAllocationsQuery({ enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({
    queryKey: ipManagementKeys.allocations,
    queryFn: () => IpManagementService.listAllocations(),
    enabled,
    refetchInterval,
  });
}

export function useIpManagementCacheUpdater() {
  const queryClient = useQueryClient();
  return {
    invalidate() {
      return queryClient.invalidateQueries({ queryKey: ipManagementKeys.all });
    },
    /** 子網刪除成功後：快取改成「未設定」（後端 GET /subnet 此時回 null）；先取消在途的重抓，免得它把舊子網寫回來 */
    async clearSubnet() {
      await queryClient.cancelQueries({ queryKey: ipManagementKeys.subnet });
      queryClient.setQueryData(ipManagementKeys.subnet, null);
    },
  };
}
