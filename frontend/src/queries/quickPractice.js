import { useQuery, useQueryClient } from "@tanstack/react-query";

import { QuickPracticeService } from "../services/quickPractice";

export const quickPracticeKeys = Object.freeze({
  all: ["quickPractice"],
  templates: ["quickPractice", "templates"],
  template: (environmentId) => ["quickPractice", "template", String(environmentId)],
});

/** 可用的快速練習範本（學生首頁、快速練習入口共用；原本只在進頁時抓一次，不輪詢） */
export function useQuickPracticeTemplatesQuery({ enabled = true, refetchInterval = false } = {}) {
  return useQuery({
    queryKey: quickPracticeKeys.templates,
    queryFn: ({ signal }) => QuickPracticeService.listTemplates({ signal }),
    enabled,
    refetchInterval,
  });
}

/** 單一快速練習範本（啟動頁） */
export function useQuickPracticeTemplateQuery(environmentId, { enabled } = {}) {
  return useQuery({
    queryKey: quickPracticeKeys.template(environmentId),
    queryFn: () => QuickPracticeService.getTemplate(environmentId),
    enabled: enabled ?? (environmentId != null),
  });
}

export function useQuickPracticeCacheUpdater() {
  const queryClient = useQueryClient();
  return {
    invalidate() {
      return queryClient.invalidateQueries({ queryKey: quickPracticeKeys.all });
    },
  };
}
