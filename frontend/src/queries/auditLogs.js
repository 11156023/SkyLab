import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { AuditLogsService } from "../services/auditLogs";
import { LIST_REFRESH_INTERVAL } from "./queryClient";

export const auditLogKeys = Object.freeze({
  all: ["auditLogs"],
  list: (params) => ["auditLogs", "list", params ?? {}],
  forResource: (vmid, params) => ["auditLogs", "resource", String(vmid), params ?? {}],
  stats: (params) => ["auditLogs", "stats", params ?? {}],
  actions: ["auditLogs", "actions"],
  users: ["auditLogs", "users"],
});

/**
 * 稽核日誌清單；params 與 AuditLogsService.list 相同（篩選、分頁），每組篩選一份快取。
 * 換篩選／翻頁時保留上一組的畫面直到新資料回來，不閃空白；
 * 回應順序錯亂也不會蓋錯（每組參數各自一份快取，畫面只看目前那組）。
 */
export function useAuditLogsQuery(params, { enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({
    queryKey: auditLogKeys.list(params),
    queryFn: () => AuditLogsService.list(params),
    enabled,
    refetchInterval,
    placeholderData: keepPreviousData,
  });
}

/** 單台機器的稽核日誌（資源詳情） */
export function useResourceAuditLogsQuery(vmid, params, { enabled, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({
    queryKey: auditLogKeys.forResource(vmid, params),
    queryFn: () => AuditLogsService.listForResource(vmid, params),
    /* 預設值寫在這裡而不是參數解構：依賴前一個參數的預設值，React Compiler 會略過整支 hook */
    enabled: enabled ?? vmid != null,
    refetchInterval,
  });
}

/** 摘要卡（依日期區間）；換區間時先留著上一個區間的數字，新區間抓失敗就沒有資料（卡片收掉） */
export function useAuditStatsQuery(params, { enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({
    queryKey: auditLogKeys.stats(params),
    queryFn: () => AuditLogsService.stats(params),
    enabled,
    refetchInterval,
    placeholderData: keepPreviousData,
  });
}

/** 篩選選單：動作種類、使用者（很少變，不輪詢） */
export function useAuditActionsQuery({ enabled = true } = {}) {
  return useQuery({ queryKey: auditLogKeys.actions, queryFn: () => AuditLogsService.actions(), enabled });
}

export function useAuditUsersQuery({ enabled = true } = {}) {
  return useQuery({ queryKey: auditLogKeys.users, queryFn: () => AuditLogsService.users(), enabled });
}
