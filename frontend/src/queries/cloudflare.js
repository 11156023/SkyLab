import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";

import { CloudflareService } from "../services/cloudflare";

export const cloudflareKeys = Object.freeze({
  all: ["cloudflare"],
  config: ["cloudflare", "config"],
  zones: (params) => ["cloudflare", "zones", params ?? {}],
  dnsRecords: (zoneId, params) => ["cloudflare", "dnsRecords", String(zoneId), params ?? {}],
});

/** 供應商連線設定（表單型：不輪詢、不因切回分頁重抓） */
export function useCloudflareConfigQuery({ enabled = true } = {}) {
  return useQuery({
    queryKey: cloudflareKeys.config,
    queryFn: () => CloudflareService.getConfig(),
    enabled,
    refetchOnWindowFocus: false,
  });
}

/** Zone 列表；params 與 CloudflareService.listZones 相同（page／per_page／search／status），換頁保留上一頁畫面 */
export function useCloudflareZonesQuery(params, { enabled = true } = {}) {
  return useQuery({
    queryKey: cloudflareKeys.zones(params),
    queryFn: () => CloudflareService.listZones(params),
    enabled,
    placeholderData: keepPreviousData,
    /* 只在進頁與存連線設定後抓（同原本）：沒設定 Cloudflare 時清單一律 400，切回視窗重抓會讓空狀態閃成 zone 工作區 */
    refetchOnWindowFocus: false,
  });
}

/**
 * 指定 zone 的 DNS 紀錄；params 與 CloudflareService.listDnsRecords 相同。
 * 刻意不保留上一組資料（不用 keepPreviousData）：切換 zone 時若先顯示 A 的紀錄，
 * 這時按編輯／刪除會拿 A 的 record id 打到 B 的 zone。換 zone 或關鍵字一律等新資料回來。
 */
export function useCloudflareDnsRecordsQuery(zoneId, params, { enabled = Boolean(zoneId) } = {}) {
  return useQuery({
    queryKey: cloudflareKeys.dnsRecords(zoneId, params),
    queryFn: () => CloudflareService.listDnsRecords(zoneId, params),
    enabled,
    /* 只在選 zone、搜尋與增刪改後抓（同原本），讀不到時切回視窗不閃回載入中 */
    refetchOnWindowFocus: false,
  });
}

export function useCloudflareCacheUpdater() {
  const queryClient = useQueryClient();
  return {
    setConfig(config) {
      queryClient.setQueryData(cloudflareKeys.config, config);
    },
    /** DNS 紀錄新增／修改／刪除後：該 zone 的各頁都重抓；不給 zoneId 就是所有 zone（例如換了連線設定） */
    invalidateDnsRecords(zoneId) {
      const queryKey = zoneId == null ? ["cloudflare", "dnsRecords"] : ["cloudflare", "dnsRecords", String(zoneId)];
      return queryClient.invalidateQueries({ queryKey });
    },
    invalidateZones() {
      return queryClient.invalidateQueries({ queryKey: ["cloudflare", "zones"] });
    },
    invalidate() {
      return queryClient.invalidateQueries({ queryKey: cloudflareKeys.all });
    },
  };
}
