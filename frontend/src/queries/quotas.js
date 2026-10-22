import { useQuery, useQueryClient } from "@tanstack/react-query";

import { QuotasService } from "../services/quotas";
import { LIST_REFRESH_INTERVAL } from "./queryClient";

export const quotaKeys = Object.freeze({
  all: ["quotas"],
  myUsage: ["quotas", "myUsage"],
  list: ["quotas", "list"],
  global: ["quotas", "global"],
});

/** 我的配額用量（配額卡、申請表單上限、規格調整上限共用） */
export function useMyQuotaUsageQuery({ enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({
    queryKey: quotaKeys.myUsage,
    queryFn: ({ signal }) => QuotasService.getMyUsage({ signal }),
    enabled,
    refetchInterval,
  });
}

/** 管理員：個人配額清單 */
export function useQuotasQuery({ enabled = true } = {}) {
  return useQuery({ queryKey: quotaKeys.list, queryFn: () => QuotasService.list(), enabled });
}

/** 管理員：全站預設配額（表單型：切回分頁不重抓，免得別處改過的數值把正在編輯的欄位洗掉） */
export function useGlobalQuotaQuery({ enabled = true } = {}) {
  return useQuery({
    queryKey: quotaKeys.global,
    queryFn: () => QuotasService.getGlobal(),
    enabled,
    refetchOnWindowFocus: false,
  });
}

export function useQuotaCacheUpdater() {
  const queryClient = useQueryClient();
  return {
    /** 全域預設存檔後直接換成回應，不必重抓 */
    setGlobal(config) {
      queryClient.setQueryData(quotaKeys.global, config);
    },
    /** 配額改了，用量與清單都重抓 */
    invalidate() {
      return queryClient.invalidateQueries({ queryKey: quotaKeys.all });
    },
    invalidateMyUsage() {
      return queryClient.invalidateQueries({ queryKey: quotaKeys.myUsage });
    },
  };
}
