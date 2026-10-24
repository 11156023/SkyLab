import { useQuery, useQueryClient } from "@tanstack/react-query";

import { GpuService } from "../services/gpu";
import { LIST_REFRESH_INTERVAL } from "./queryClient";

export const gpuKeys = Object.freeze({
  all: ["gpu"],
  options: (params) => ["gpu", "options", params ?? {}],
  mappings: ["gpu", "mappings"],
});

/** 申請表單／範本克隆可選的 GPU；params 與 GpuService.listOptions 相同 */
export function useGpuOptionsQuery(params, { enabled = true } = {}) {
  return useQuery({ queryKey: gpuKeys.options(params), queryFn: () => GpuService.listOptions(params), enabled });
}

/** 管理員：GPU 對應清單 */
export function useGpuMappingsQuery({ enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({ queryKey: gpuKeys.mappings, queryFn: () => GpuService.listMappings(), enabled, refetchInterval });
}

export function useGpuCacheUpdater() {
  const queryClient = useQueryClient();
  return {
    invalidate() {
      return queryClient.invalidateQueries({ queryKey: gpuKeys.all });
    },
  };
}
