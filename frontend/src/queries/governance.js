import { useQuery, useQueryClient } from "@tanstack/react-query";

import { GovernanceService } from "../services/governance";

export const governanceKeys = Object.freeze({
  all: ["governance"],
  config: ["governance", "config"],
});

/** 治理設定（表單型：不輪詢、不因切回分頁重抓，免得蓋掉正在編輯的欄位） */
export function useGovernanceConfigQuery({ enabled = true } = {}) {
  return useQuery({
    queryKey: governanceKeys.config,
    queryFn: () => GovernanceService.getConfig(),
    enabled,
    refetchOnWindowFocus: false,
  });
}

export function useGovernanceCacheUpdater() {
  const queryClient = useQueryClient();
  return {
    setConfig(config) {
      queryClient.setQueryData(governanceKeys.config, config);
    },
    invalidate() {
      return queryClient.invalidateQueries({ queryKey: governanceKeys.all });
    },
  };
}
