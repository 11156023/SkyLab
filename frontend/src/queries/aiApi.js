import { isCancelledError, keepPreviousData, useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";

import { AiApiService } from "../services/aiApi";
import { LIST_REFRESH_INTERVAL } from "./queryClient";

export const aiApiKeys = Object.freeze({
  all: ["aiApi"],
  myRequests: ["aiApi", "myRequests"],
  myCredentials: ["aiApi", "myCredentials"],
  credential: (credentialId) => ["aiApi", "credential", String(credentialId)],
  allRequests: (params) => ["aiApi", "allRequests", params ?? {}],
  allCredentials: (params) => ["aiApi", "allCredentials", params ?? {}],
  myUsage: (range) => ["aiApi", "myUsage", range ?? {}],
  myUsageRecords: (params) => ["aiApi", "myUsageRecords", params ?? {}],
});

/**
 * 我的 AI API 申請。refetchInterval 可傳函式（例如「有待審的才輪詢」）：
 * `(query) => (hasPending(query.state.data) ? LIST_REFRESH_INTERVAL : false)`
 */
export function useMyAiRequestsQuery({ enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({ queryKey: aiApiKeys.myRequests, queryFn: () => AiApiService.listMyRequests(), enabled, refetchInterval });
}

/** 我的 AI API 憑證（回應含 data 與 public_base_url） */
export function useMyAiCredentialsQuery({ enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({ queryKey: aiApiKeys.myCredentials, queryFn: () => AiApiService.listMyCredentials(), enabled, refetchInterval });
}

/**
 * 單一憑證詳細（擁有者專用端點，回應帶完整金鑰）。
 * - 傳 signal：呼叫端取消（關閉金鑰視窗）或卸載時，請求跟著中止，晚回來的金鑰不會進快取。
 * - gcTime 0：完整金鑰只留在開著的視窗／聊天分頁；沒有人在看就從快取丟掉，不在記憶體裡多放 5 分鐘。
 * - staleTime Infinity：同一筆憑證的金鑰不會變（重新產生是新的一筆），開著時切回分頁不必再打一次擁有者端點；
 *   重新開啟視窗時快取已丟掉，一定重新取得。
 */
export function useAiCredentialQuery(credentialId, { enabled } = {}) {
  return useQuery({
    queryKey: aiApiKeys.credential(credentialId),
    queryFn: ({ signal }) => AiApiService.getCredential(credentialId, { signal }),
    /* 預設值不寫在參數解構裡：React Compiler 不處理參數預設值裡的運算式 */
    enabled: enabled ?? credentialId != null,
    gcTime: 0,
    staleTime: Infinity,
  });
}

/**
 * 管理員：全部 AI API 申請；params 與 AiApiService.listAllRequests 相同（status／skip／limit）。
 * 換篩選時先沿用上一份資料（keepPreviousData），呼叫端用 isPlaceholderData 判斷是不是還沒換好。
 */
export function useAllAiRequestsQuery(params, { enabled = true, refetchInterval = LIST_REFRESH_INTERVAL, select } = {}) {
  return useQuery({
    queryKey: aiApiKeys.allRequests(params),
    queryFn: () => AiApiService.listAllRequests(params),
    enabled,
    refetchInterval,
    select,
    placeholderData: keepPreviousData,
  });
}

/** 管理員：全部憑證；params 與 AiApiService.listAllCredentials 相同。換篩選／分頁時沿用上一頁，不閃空白 */
export function useAllAiCredentialsQuery(params, { enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({
    queryKey: aiApiKeys.allCredentials(params),
    queryFn: () => AiApiService.listAllCredentials(params),
    enabled,
    refetchInterval,
    placeholderData: keepPreviousData,
  });
}

/** 我的用量統計；range = { start_date, end_date }（不輪詢，與原頁面相同）。換區間時沿用上一個區間的數字 */
export function useMyAiUsageQuery(range, { enabled = true } = {}) {
  return useQuery({
    queryKey: aiApiKeys.myUsage(range),
    queryFn: () => AiApiService.getMyUsage(range),
    enabled,
    placeholderData: keepPreviousData,
  });
}

/** 已載入筆數還不到總數就再抓下一頁；回傳下一頁的 skip，沒有下一頁時回 undefined */
export function nextUsageRecordsSkip(lastPage, allPages) {
  const loaded = allPages.reduce((sum, page) => sum + (page?.data?.length ?? 0), 0);
  const lastCount = lastPage?.data?.length ?? 0;
  return lastCount > 0 && loaded < (lastPage?.count ?? 0) ? loaded : undefined;
}

/**
 * 我的用量明細（「載入更多」）；params = { start_date, end_date, limit }，skip 由頁數決定。
 * 用 infinite query 而不是單頁 query＋自己接陣列：背景重抓時所有已載入的頁會依序重抓，
 * 第一頁多了新紀錄也不會跟後面自己接上去的頁重複或漏掉；換區間時舊區間的「載入更多」也不會接到新清單後面。
 */
export function useMyAiUsageRecordsInfiniteQuery(params, { enabled = true } = {}) {
  return useInfiniteQuery({
    queryKey: aiApiKeys.myUsageRecords(params),
    queryFn: ({ pageParam }) => AiApiService.getMyUsageRecords({ ...params, skip: pageParam }),
    initialPageParam: 0,
    getNextPageParam: nextUsageRecordsSkip,
    enabled,
    placeholderData: keepPreviousData,
  });
}

/* 操作後的重抓：throwOnError 時重抓失敗會丟出，讓呼叫端照原本的行為跳 toast；
   被較新的重抓或 cancelCredential 取消不算失敗 */
async function refetchActive(queryClient, queryKey, { throwOnError = false } = {}) {
  try {
    await queryClient.invalidateQueries({ queryKey }, { throwOnError });
  } catch (error) {
    if (!isCancelledError(error)) throw error;
  }
}

export function useAiApiCacheUpdater() {
  const queryClient = useQueryClient();
  /* 回傳的物件要穩定：金鑰視窗的 effect 依賴 cancelCredential */
  return useMemo(() => ({
    invalidate() {
      return queryClient.invalidateQueries({ queryKey: aiApiKeys.all });
    },
    /** 申請／重新產生／刪除／改名後：我的申請與憑證一起重抓 */
    invalidateMine(options) {
      return Promise.all([
        refetchActive(queryClient, aiApiKeys.myRequests, options),
        refetchActive(queryClient, aiApiKeys.myCredentials, options),
      ]);
    },
    /** 申請被核准時：會多一把新金鑰，背景補抓我的憑證（失敗保留畫面上的資料，同背景輪詢） */
    refreshMyCredentials() {
      return refetchActive(queryClient, aiApiKeys.myCredentials);
    },
    /** 審核後：全部申請的各種篩選都重抓 */
    invalidateAllRequests(options) {
      return refetchActive(queryClient, ["aiApi", "allRequests"], options);
    },
    /** 撤銷後：全部憑證的各種篩選都重抓 */
    invalidateAllCredentials(options) {
      return refetchActive(queryClient, ["aiApi", "allCredentials"], options);
    },
    /**
     * 刪除成功後先把那把金鑰從我的憑證快取拿掉，背景重抓失敗也不會恢復。
     * 先取消在途的背景刷新（等它完成再寫，否則取消時的還原會蓋掉剛寫進去的結果）；還沒有資料時不動。
     */
    async removeMyCredential(credentialId) {
      const queryKey = aiApiKeys.myCredentials;
      if (queryClient.getQueryData(queryKey) === undefined) return;
      await queryClient.cancelQueries({ queryKey });
      queryClient.setQueryData(queryKey, (prev) => (
        prev ? { ...prev, data: (prev.data ?? []).filter((item) => item.id !== credentialId) } : prev
      ));
    },
    /** 金鑰視窗一按關閉就中止取回完整金鑰的請求（不等離場動畫、不等卸載） */
    cancelCredential(credentialId) {
      return queryClient.cancelQueries({ queryKey: aiApiKeys.credential(credentialId) });
    },
  }), [queryClient]);
}
