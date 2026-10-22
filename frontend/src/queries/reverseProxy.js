import { useQuery } from "@tanstack/react-query";

import { ReverseProxyService } from "../services/reverseProxy";

export const reverseProxyKeys = Object.freeze({
  all: ["reverseProxy"],
  setupContext: ["reverseProxy", "setupContext"],
});

/**
 * 建立對外網址前的環境檢查（Gateway／Cloudflare 是否就緒、可用的 zones）。
 * 連線對話框與課程編輯器共用一份；設定很少變，不輪詢。
 */
export function useReverseProxySetupContextQuery({ enabled = true } = {}) {
  return useQuery({
    queryKey: reverseProxyKeys.setupContext,
    queryFn: () => ReverseProxyService.setupContext(),
    enabled,
  });
}

/** 環境沒設定好或查詢失敗時一律當成沒有可用的 zone（發布方式只留「僅開防火牆」） */
export function usableZones(context) {
  return context?.enabled ? (context.zones ?? []) : [];
}
