import { useQuery, useQueryClient } from "@tanstack/react-query";

import { BatchProvisionService } from "../services/batchProvision";
import { LIST_REFRESH_INTERVAL } from "./queryClient";

export const batchProvisionKeys = Object.freeze({
  all: ["batchProvision"],
  pending: ["batchProvision", "pending"],
  list: (params) => ["batchProvision", "list", params ?? {}],
  recurrencePreview: (jobId, count) => ["batchProvision", "recurrencePreview", String(jobId), count],
});

/** 待審核的批次建置（管理首頁摘要） */
export function usePendingBatchProvisionQuery({ enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({
    queryKey: batchProvisionKeys.pending,
    queryFn: () => BatchProvisionService.listPending(),
    enabled,
    refetchInterval,
  });
}

/** 批次建置清單；params 與 BatchProvisionService.listAll 相同 */
export function useAllBatchProvisionQuery(params, { enabled = true, refetchInterval = LIST_REFRESH_INTERVAL, select } = {}) {
  return useQuery({
    queryKey: batchProvisionKeys.list(params),
    queryFn: () => BatchProvisionService.listAll(params),
    enabled,
    refetchInterval,
    select,
  });
}

/** 週期排程預覽的 query 設定（hook 與點擊時主動載入共用同一份 key／queryFn） */
export function recurrencePreviewQueryOptions(jobId, count = 5) {
  return {
    queryKey: batchProvisionKeys.recurrencePreview(jobId, count),
    queryFn: () => BatchProvisionService.getRecurrencePreview(jobId, count),
  };
}

/**
 * 週期排程的下幾次預覽。
 * 審核頁是「點了才載入」：傳 `enabled: false` 只讀快取（含載入中狀態），
 * 由 useBatchProvisionCacheUpdater().loadRecurrencePreview 主動抓。
 */
export function useRecurrencePreviewQuery(jobId, count = 5, { enabled } = {}) {
  /* 沒指定時有 jobId 才抓（預設值不寫在參數裡，React Compiler 才編得動） */
  return useQuery({ ...recurrencePreviewQueryOptions(jobId, count), enabled: enabled ?? jobId != null });
}

export function useBatchProvisionCacheUpdater() {
  const queryClient = useQueryClient();
  return {
    /** 審核後：清單（含管理首頁的待審摘要）全部重抓 */
    invalidate() {
      return queryClient.invalidateQueries({ queryKey: batchProvisionKeys.all });
    },
    /** 點「查看時段」：抓預覽寫進快取；已有新鮮快取就直接回傳，失敗丟例外給呼叫端提示 */
    loadRecurrencePreview(jobId, count = 5) {
      return queryClient.fetchQuery(recurrencePreviewQueryOptions(jobId, count));
    },
  };
}
