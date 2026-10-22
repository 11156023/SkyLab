import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { presetToRange } from "../utils/aiTimeRange";
import { AiMonitoringService } from "../services/aiMonitoring";
import { LIST_REFRESH_INTERVAL } from "./queryClient";

export const aiMonitoringKeys = Object.freeze({
  all: ["aiMonitoring"],
  overview: (params) => ["aiMonitoring", "overview", params ?? {}],
  proxyCalls: (params) => ["aiMonitoring", "proxyCalls", params ?? {}],
  templateCalls: (params) => ["aiMonitoring", "templateCalls", params ?? {}],
  usersUsage: (params) => ["aiMonitoring", "usersUsage", params ?? {}],
  runtime: ["aiMonitoring", "runtime"],
});

/**
 * key 只放區間代號 preset（"7d"／"30d"／"90d"），起訖時間在每次抓取時才算：
 * 背景刷新要跟著「現在」往前滑，新的呼叫才會出現；key 放算好的時間的話，
 * 每次 render 時間都不同會一直換 key，固定住又會讓刷新一直查同一段舊區間。
 */
export function resolveMonitoringWindow({ preset, ...rest } = {}) {
  return preset ? { ...presetToRange(preset), ...rest } : rest;
}

/** 用量、錯誤與模型聚合趨勢；params = { preset, bucket, compare, source }。換區間時先沿用上一個區間 */
export function useAiMonitoringOverviewQuery(params, { enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({
    queryKey: aiMonitoringKeys.overview(params),
    queryFn: () => AiMonitoringService.overview(resolveMonitoringWindow(params)),
    enabled,
    refetchInterval,
    placeholderData: keepPreviousData,
  });
}

/** 金鑰 API（AI 代理）呼叫清單；params = { preset, limit, … } */
export function useAiProxyCallsQuery(params, { enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({
    queryKey: aiMonitoringKeys.proxyCalls(params),
    queryFn: () => AiMonitoringService.listProxyCalls(resolveMonitoringWindow(params)),
    enabled,
    refetchInterval,
    placeholderData: keepPreviousData,
  });
}

/** Template 呼叫清單；params = { preset, limit, … } */
export function useAiTemplateCallsQuery(params, { enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({
    queryKey: aiMonitoringKeys.templateCalls(params),
    queryFn: () => AiMonitoringService.listTemplateCalls(resolveMonitoringWindow(params)),
    enabled,
    refetchInterval,
    placeholderData: keepPreviousData,
  });
}

/** 使用者用量彙總；params = { preset, limit, source } */
export function useAiUsersUsageQuery(params, { enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({
    queryKey: aiMonitoringKeys.usersUsage(params),
    queryFn: () => AiMonitoringService.listUsersUsage(resolveMonitoringWindow(params)),
    enabled,
    refetchInterval,
    placeholderData: keepPreviousData,
  });
}

/** LiteLLM gateway 與模型健康摘要（與區間無關，換區間不重抓） */
export function useAiMonitoringRuntimeQuery({ enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({
    queryKey: aiMonitoringKeys.runtime,
    queryFn: () => AiMonitoringService.runtime(),
    enabled,
    refetchInterval,
  });
}
