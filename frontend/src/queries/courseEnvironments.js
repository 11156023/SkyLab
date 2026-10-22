import { useQuery, useQueryClient } from "@tanstack/react-query";

import { CourseEnvironmentsService } from "../services/courseEnvironments";
import { LIST_REFRESH_INTERVAL } from "./queryClient";

export const courseEnvironmentKeys = Object.freeze({
  all: ["courseEnvironments"],
  list: ["courseEnvironments", "list"],
  published: ["courseEnvironments", "published"],
  detail: (environmentId) => ["courseEnvironments", "detail", String(environmentId)],
});

/** 老師的教學環境（含草稿） */
export function useCourseEnvironmentsQuery({ enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({
    queryKey: courseEnvironmentKeys.list,
    queryFn: () => CourseEnvironmentsService.list(),
    enabled,
    refetchInterval,
  });
}

/** 已發布的教學環境（建班、班級工作區選課程用） */
export function usePublishedCourseEnvironmentsQuery({ enabled = true } = {}) {
  return useQuery({
    queryKey: courseEnvironmentKeys.published,
    queryFn: () => CourseEnvironmentsService.listPublished(),
    enabled,
  });
}

/* 編輯器用：每次進頁（或換到另一個環境）都向伺服器拿最新的一份，快取裡的舊資料不算數；
   之後不輪詢、切回分頁也不重抓，編輯中的草稿不會被背景資料蓋掉 */
const EDITING_OPTIONS = Object.freeze({ staleTime: 0, refetchOnMount: "always", refetchOnWindowFocus: false });

/**
 * 單一教學環境。
 * @param {object} [options]
 * @param {boolean} [options.forEditing] 編輯器：每次掛載都重抓、之後不自動重抓（見 EDITING_OPTIONS）
 */
export function useCourseEnvironmentQuery(environmentId, { enabled, forEditing = false } = {}) {
  return useQuery({
    queryKey: courseEnvironmentKeys.detail(environmentId),
    queryFn: () => CourseEnvironmentsService.get(environmentId),
    enabled: enabled ?? (environmentId != null),
    ...(forEditing ? EDITING_OPTIONS : null),
  });
}

export function useCourseEnvironmentCacheUpdater() {
  const queryClient = useQueryClient();
  return {
    /**
     * 存檔／發布／上傳回來的完整環境：寫進詳情，清單那一列就地換掉；
     * 清單裡還沒有（剛建立的草稿）就讓清單重抓。發布、退役會改變「已發布」清單的內容，標成過期。
     */
    update(updated) {
      if (updated?.id == null) return;
      queryClient.setQueryData(courseEnvironmentKeys.detail(updated.id), updated);
      let found = false;
      queryClient.setQueryData(courseEnvironmentKeys.list, (prev) => {
        if (!Array.isArray(prev)) return prev;
        return prev.map((row) => {
          if (row.id !== updated.id) return row;
          found = true;
          return updated;
        });
      });
      if (!found && queryClient.getQueryData(courseEnvironmentKeys.list) !== undefined) {
        void queryClient.invalidateQueries({ queryKey: courseEnvironmentKeys.list });
      }
      if (updated.status !== "draft") void queryClient.invalidateQueries({ queryKey: courseEnvironmentKeys.published });
    },
    /**
     * 刪除後就地從清單拿掉，詳情快取也清掉。
     * 先取消還在路上的背景刷新：刪除前送出的刷新晚回來會把剛刪掉的那一列帶回來
     * （cancelQueries 要等它完成再寫，否則還原會蓋掉剛寫好的清單）。
     */
    async remove(environmentId) {
      const lists = [courseEnvironmentKeys.list, courseEnvironmentKeys.published];
      for (const queryKey of lists) {
        if (queryClient.getQueryData(queryKey) === undefined) continue;
        await queryClient.cancelQueries({ queryKey, exact: true });
        queryClient.setQueryData(queryKey, (prev) => (
          Array.isArray(prev) ? prev.filter((row) => row.id !== environmentId) : prev
        ));
      }
      queryClient.removeQueries({ queryKey: courseEnvironmentKeys.detail(environmentId) });
    },
    invalidate() {
      return queryClient.invalidateQueries({ queryKey: courseEnvironmentKeys.all });
    },
  };
}
