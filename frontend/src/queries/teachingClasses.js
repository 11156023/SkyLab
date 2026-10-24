import { useMemo, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { TeachingClassesService } from "../services/teachingClasses";
import { LIST_REFRESH_INTERVAL } from "./queryClient";

export const teachingClassKeys = Object.freeze({
  all: ["teachingClasses"],
  list: ["teachingClasses", "list"],
  detail: (classId) => ["teachingClasses", "detail", String(classId)],
  capacityPreview: (classId) => ["teachingClasses", "capacityPreview", String(classId)],
  resourceUsage: (classId) => ["teachingClasses", "resourceUsage", String(classId)],
  provisionStatus: (classId) => ["teachingClasses", "provisionStatus", String(classId)],
});

/* 表單型頁面（建班精靈）用：每次進頁都向伺服器拿最新的一份，快取裡的舊資料不拿來初始化表單；
   之後不輪詢、切回分頁也不重抓，打到一半的內容不會被背景資料蓋掉 */
const EDITING_OPTIONS = Object.freeze({ staleTime: 0, refetchOnMount: "always", refetchOnWindowFocus: false });

/** 我的班級清單（老師首頁、班級管理、課程 CMS 共用） */
export function useTeachingClassesQuery({ enabled = true, refetchInterval = LIST_REFRESH_INTERVAL, select } = {}) {
  return useQuery({
    queryKey: teachingClassKeys.list,
    queryFn: () => TeachingClassesService.list(),
    enabled,
    refetchInterval,
    select,
  });
}

/**
 * 單一班級；select 請傳模組層級的函式，結果才會被快取。
 * @param {object} [options]
 * @param {number|false|((query) => number|false)} [options.refetchInterval] 動態間隔可依 query.state.data 決定
 * @param {boolean} [options.refetchOnWindowFocus] 省略時用全站預設（切回分頁、資料過期才重抓）
 * @param {boolean} [options.forEditing] 表單型頁面：每次掛載都重抓、之後不自動重抓（見 EDITING_OPTIONS）
 */
export function useTeachingClassQuery(classId, {
  enabled,
  refetchInterval = false,
  select,
  refetchOnWindowFocus,
  forEditing = false,
} = {}) {
  return useQuery({
    queryKey: teachingClassKeys.detail(classId),
    queryFn: () => TeachingClassesService.get(classId),
    enabled: enabled ?? (classId != null),
    refetchInterval,
    select,
    /* 只有呼叫端有指定才覆寫；明寫 undefined 會蓋掉全站預設 */
    ...(refetchOnWindowFocus === undefined ? null : { refetchOnWindowFocus }),
    ...(forEditing ? EDITING_OPTIONS : null),
  });
}

/** 建置前的容量預覽（PVE 剩餘資源會變，切回分頁不重抓：結果只在進入確認步驟、班級內容改過時更新） */
export function useClassCapacityPreviewQuery(classId, { enabled } = {}) {
  return useQuery({
    queryKey: teachingClassKeys.capacityPreview(classId),
    queryFn: () => TeachingClassesService.capacityPreview(classId),
    enabled: enabled ?? (classId != null),
    refetchOnWindowFocus: false,
  });
}

/** 班級資源用量（熱力圖）；select 請傳模組層級的函式 */
export function useClassResourceUsageQuery(classId, { enabled, refetchInterval = LIST_REFRESH_INTERVAL, select } = {}) {
  return useQuery({
    queryKey: teachingClassKeys.resourceUsage(classId),
    queryFn: () => TeachingClassesService.resourceUsage(classId),
    enabled: enabled ?? (classId != null),
    refetchInterval,
    select,
  });
}

/**
 * 批次建置進度（回傳整個班級）；呼叫端用 enabled／refetchInterval 決定建置中才輪詢。
 * @param {object} [options]
 * @param {number} [options.reconcileEvery] 每 N 次改打 reconcile（第 1、N+1、2N+1… 次），請後端把 PVE 上的
 *   建機結果寫回班級；那一支會實際呼叫 PVE，不適合每次輪詢都打。0＝只讀進度
 * @param {(data) => any} [options.select] 請傳模組層級的函式
 */
export function useClassProvisionStatusQuery(classId, {
  enabled,
  refetchInterval = false,
  select,
  reconcileEvery = 0,
} = {}) {
  const fetchCount = useRef(0);
  return useQuery({
    queryKey: teachingClassKeys.provisionStatus(classId),
    queryFn: () => {
      fetchCount.current += 1;
      return reconcileEvery > 0 && fetchCount.current % reconcileEvery === 1 % reconcileEvery
        ? TeachingClassesService.reconcile(classId)
        : TeachingClassesService.provisionStatus(classId);
    },
    enabled: enabled ?? (classId != null),
    refetchInterval,
    select,
  });
}

export function useTeachingClassCacheUpdater() {
  const queryClient = useQueryClient();
  /* 只包著 queryClient：固定成同一個物件，放進 effect 的依賴也不會每次 render 都重跑 */
  return useMemo(() => ({
    /**
     * 寫入（改設定、加減學生、選環境、改課表…）回來的完整班級：寫進詳情快取。
     * 清單是摘要欄位（member_count、ready_machines…），詳情合併進去會留下舊的計數，所以標成過期讓它重抓；
     * 班級內容改了，容量預覽也跟著過期。
     */
    update(updated) {
      if (updated?.id == null) return;
      queryClient.setQueryData(teachingClassKeys.detail(updated.id), updated);
      void queryClient.invalidateQueries({ queryKey: teachingClassKeys.list });
      void queryClient.invalidateQueries({ queryKey: teachingClassKeys.capacityPreview(updated.id) });
    },
    /** 只換詳情（建置進度輪詢回來的整個班級）：不動清單與預覽，每 3 秒一次不必讓別的快取跟著過期 */
    setDetail(updated) {
      if (updated?.id == null) return;
      queryClient.setQueryData(teachingClassKeys.detail(updated.id), updated);
    },
    /** 指定班級：詳情、清單與該班的預覽／用量／建置進度都重抓；不帶 id：所有班級相關快取都重抓 */
    invalidate(classId) {
      if (classId == null) return queryClient.invalidateQueries({ queryKey: teachingClassKeys.all });
      return Promise.all([
        queryClient.invalidateQueries({ queryKey: teachingClassKeys.detail(classId) }),
        queryClient.invalidateQueries({ queryKey: teachingClassKeys.list }),
        queryClient.invalidateQueries({ queryKey: teachingClassKeys.capacityPreview(classId) }),
        queryClient.invalidateQueries({ queryKey: teachingClassKeys.resourceUsage(classId) }),
        queryClient.invalidateQueries({ queryKey: teachingClassKeys.provisionStatus(classId) }),
      ]);
    },
  }), [queryClient]);
}
