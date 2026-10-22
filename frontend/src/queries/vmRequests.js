import { useQuery, useQueryClient } from "@tanstack/react-query";

import { VmRequestsService } from "../services/vmRequests";
import {
  PENDING_IDLE_POLL_INTERVAL,
  PENDING_POLL_INTERVAL,
  isConsumedRequest,
  isCreatingRequest,
} from "../services/pendingResources";
import { LIST_REFRESH_INTERVAL } from "./queryClient";

/**
 * 我的 VM 申請單：「我的資源」的建立中佔位列與「我的申請」列表共用同一份快取，
 * 兩頁各自用 select 取需要的子集合，來回切換不會重打 API。
 */
export const vmRequestKeys = Object.freeze({
  all: ["vmRequests"],
  mine: ["vmRequests", "mine"],
  everyone: (status, params) => ["vmRequests", "everyone", status ?? null, params ?? {}],
  reviewContext: (requestId) => ["vmRequests", "reviewContext", String(requestId)],
});

/** 管理員：全部申請單（審核頁、管理首頁）；參數與 VmRequestsService.listAll 相同 */
export function useAllVmRequestsQuery(status, params, { enabled = true, refetchInterval = LIST_REFRESH_INTERVAL, select } = {}) {
  return useQuery({
    queryKey: vmRequestKeys.everyone(status, params),
    queryFn: () => VmRequestsService.listAll(status, params),
    enabled,
    refetchInterval,
    select,
  });
}

/** 審核單一申請時的脈絡（配額、衝突…） */
export function useVmRequestReviewContextQuery(requestId, { enabled } = {}) {
  return useQuery({
    queryKey: vmRequestKeys.reviewContext(requestId),
    queryFn: () => VmRequestsService.getReviewContext(requestId),
    enabled: enabled ?? (requestId != null),
  });
}

async function fetchMyVmRequests({ signal }) {
  const res = await VmRequestsService.list({ signal });
  return res?.data ?? [];
}

/**
 * 我的申請單的 query 設定（key＋queryFn）。hook 與事件處理裡的
 * `queryClient.fetchQuery(myVmRequestsQueryOptions())` 共用，快取格式只寫在這裡。
 */
export function myVmRequestsQueryOptions() {
  return { queryKey: vmRequestKeys.mine, queryFn: fetchMyVmRequests };
}

/** 建立中（尚未開通完成）的申請：資源列表 placeholder 用 */
export function selectCreatingRequests(items) {
  return (items ?? []).filter(isCreatingRequest);
}

/** 機器已被刪除／轉範本的申請單只留做稽核，列表不顯示 */
export function selectActiveRequests(items) {
  return (items ?? []).filter((r) => !isConsumedRequest(r));
}

/** 有建立中的申請時 5 秒追進度，否則放慢 */
export function creatingPollInterval(items) {
  return (items ?? []).some(isCreatingRequest) ? PENDING_POLL_INTERVAL : PENDING_IDLE_POLL_INTERVAL;
}

/**
 * @param {object} [options]
 * @param {(items: any[]) => any} [options.select]
 * @param {boolean} [options.enabled]
 * @param {number|false} [options.refetchInterval] 省略時依「是否有建立中的申請」自動決定
 */
export function useMyVmRequestsQuery({ select, enabled = true, refetchInterval } = {}) {
  return useQuery({
    ...myVmRequestsQueryOptions(),
    select,
    enabled,
    refetchInterval: refetchInterval ?? ((query) => creatingPollInterval(query.state.data)),
  });
}

/** 建立中的申請（我的資源頁的佔位列） */
export function usePendingVmRequestsQuery(options = {}) {
  return useMyVmRequestsQuery({ ...options, select: selectCreatingRequests });
}

/** 我的申請列表；`fastPoll` 為 true 時（規格調整套用中）改用 5 秒輪詢 */
export function useMyActiveVmRequestsQuery({ enabled = true, fastPoll = false } = {}) {
  return useMyVmRequestsQuery({
    enabled,
    select: selectActiveRequests,
    refetchInterval: fastPoll ? PENDING_POLL_INTERVAL : LIST_REFRESH_INTERVAL,
  });
}

/** 單筆申請操作（取消、重試）回來的新資料就地寫進快取，不等下一輪刷新 */
export function useVmRequestCacheUpdater() {
  const queryClient = useQueryClient();
  return {
    /** 寫入回應就地換掉那一張。先取消在途的背景刷新：它在操作前送出，晚回來會把剛寫的結果蓋回舊狀態 */
    async update(updated) {
      await queryClient.cancelQueries({ queryKey: vmRequestKeys.mine });
      queryClient.setQueryData(vmRequestKeys.mine, (prev) => (
        prev ? prev.map((r) => (r.id === updated.id ? updated : r)) : prev
      ));
    },
    invalidate() {
      return queryClient.invalidateQueries({ queryKey: vmRequestKeys.mine });
    },
    /** 審核／重試後：全部申請單的各種篩選都重抓 */
    invalidateEveryone() {
      return queryClient.invalidateQueries({ queryKey: ["vmRequests", "everyone"] });
    },
  };
}
