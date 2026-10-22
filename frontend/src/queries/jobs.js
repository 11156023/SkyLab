import { useQuery, useQueryClient } from "@tanstack/react-query";

import { JobsService } from "../services/jobs";
import { LIST_REFRESH_INTERVAL } from "./queryClient";

export const jobKeys = Object.freeze({
  all: ["jobs"],
  list: (params) => ["jobs", "list", params ?? {}],
  detail: (jobId) => ["jobs", "detail", String(jobId)],
});

/** 仍視為「進行中」的狀態；需與後端 schemas/jobs.py 的 ACTIVE_JOB_STATUSES 一致 */
export const ACTIVE_JOB_STATUSES = Object.freeze(new Set(["pending", "running", "blocked"]));
/** 任務詳情：還在跑就 3 秒追一次 */
export const JOB_DETAIL_POLL_INTERVAL = 3_000;
/** 任務詳情：背景刷新偶發失敗（後端重啟、502）時放慢到 6 秒再試，恢復後錯誤自動消失 */
export const JOB_DETAIL_RETRY_INTERVAL = 6_000;

/**
 * 任務詳情的輪詢間隔（原 JobDetailDialog 的 setTimeout 鏈）：
 * 任務已到終態或首次載入就失敗（沒有任何資料）時不再輪詢；
 * 進行中照 3 秒，最近一次背景刷新失敗則放慢到 6 秒。
 */
export function jobDetailPollInterval(state) {
  if (!ACTIVE_JOB_STATUSES.has(state?.data?.item?.status)) return false;
  return state.status === "error" ? JOB_DETAIL_RETRY_INTERVAL : JOB_DETAIL_POLL_INTERVAL;
}

/**
 * 背景任務清單；params 與 JobsService.list 相同（kinds／statuses／activeOnly／limit／offset／historyDays），
 * 不同篩選各自一份快取。JobsProvider 另有 WebSocket 推進度，這裡只負責初始與定期補抓。
 */
export function useJobsQuery(params, {
  enabled = true,
  refetchInterval = LIST_REFRESH_INTERVAL,
  refetchOnWindowFocus,
  select,
  placeholderData,
} = {}) {
  return useQuery({
    queryKey: jobKeys.list(params),
    queryFn: () => JobsService.list(params),
    enabled,
    refetchInterval,
    /* 省略時沿用 QueryClient 預設（聚焦時若資料過期就重抓）；JobsProvider 有 WS 推送，傳 false 關掉。
       明確傳 undefined 會蓋掉預設值，所以只有呼叫端有給才放進選項 */
    ...(refetchOnWindowFocus === undefined ? {} : { refetchOnWindowFocus }),
    select,
    placeholderData,
  });
}

/**
 * 單一任務詳情（id 格式 <kind>:<source_id>）。
 * refetchInterval 省略時依任務狀態自動決定（見 jobDetailPollInterval）。
 */
/* 預設輪詢間隔是模組層級的函式：參數預設值寫運算式或箭頭函式會讓 React Compiler 略過整支 hook */
const defaultJobDetailInterval = (query) => jobDetailPollInterval(query.state);

export function useJobDetailQuery(jobId, { enabled, refetchInterval = defaultJobDetailInterval } = {}) {
  return useQuery({
    queryKey: jobKeys.detail(jobId),
    queryFn: () => JobsService.detail(jobId),
    enabled: enabled ?? (jobId != null),
    refetchInterval,
  });
}

export function useJobCacheUpdater() {
  const queryClient = useQueryClient();
  return {
    invalidate() {
      return queryClient.invalidateQueries({ queryKey: jobKeys.all });
    },
  };
}
