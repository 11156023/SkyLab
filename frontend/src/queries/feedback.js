import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";

import { FeedbackService } from "../services/feedback";
import { LIST_REFRESH_INTERVAL } from "./queryClient";

export const feedbackKeys = Object.freeze({
  all: ["feedback"],
  list: (params) => ["feedback", "list", params ?? {}],
});

/**
 * 管理員的回饋清單；params 與 FeedbackService.listAll 相同（篩選、分頁），每組篩選一份快取。
 * 換篩選／翻頁時保留上一組畫面直到新資料回來，不閃空白。
 */
export function useFeedbackListQuery(params, { enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({
    queryKey: feedbackKeys.list(params),
    queryFn: () => FeedbackService.listAll(params),
    enabled,
    refetchInterval,
    placeholderData: keepPreviousData,
  });
}

/** 標記／刪除之後讓所有清單快取失效，靜默重抓 */
export function useFeedbackCacheUpdater() {
  const queryClient = useQueryClient();
  return {
    invalidate() {
      return queryClient.invalidateQueries({ queryKey: feedbackKeys.all });
    },
  };
}
