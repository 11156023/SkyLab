import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";

import { ResourcesService } from "../services/resources";
import { LIST_REFRESH_INTERVAL } from "./queryClient";
import { resourceKeys, useResourceCacheUpdater } from "./resources";

/* 資源詳情頁的讀取全部以 vmid 為 key 的前綴，換機器時各分頁各自一份快取，
   invalidate(vmid) 一次把這台的所有讀取標成過期 */
export const resourceDetailKeys = Object.freeze({
  all: ["resourceDetail"],
  scope: (vmid) => ["resourceDetail", String(vmid)],
  detail: (vmid) => ["resourceDetail", String(vmid), "detail"],
  currentStats: (vmid) => ["resourceDetail", String(vmid), "currentStats"],
  stats: (vmid, timeframe) => ["resourceDetail", String(vmid), "stats", timeframe],
  snapshots: (vmid) => ["resourceDetail", String(vmid), "snapshots"],
  backups: (vmid) => ["resourceDetail", String(vmid), "backups"],
  snapshotCapability: (vmid) => ["resourceDetail", String(vmid), "snapshotCapability"],
  backupCapability: (vmid) => ["resourceDetail", String(vmid), "backupCapability"],
  sshKey: (vmid) => ["resourceDetail", String(vmid), "sshKey"],
  templateManual: (vmid) => ["resourceDetail", String(vmid), "templateManual"],
  shares: (vmid) => ["resourceDetail", String(vmid), "shares"],
  credentials: (vmid) => ["resourceDetail", String(vmid), "credentials"],
  bootOptions: (vmid) => ["resourceDetail", String(vmid), "bootOptions"],
  isoImages: (vmid) => ["resourceDetail", String(vmid), "isoImages"],
  specs: (vmid) => ["resourceDetail", String(vmid), "specs"],
});

/** 即時用量的預設輪詢（監控分頁 5 秒；總覽分頁執行中才 10 秒） */
export const CURRENT_STATS_INTERVAL = 5_000;
/** RRD 趨勢的輪詢 */
export const RRD_STATS_INTERVAL = 30_000;

const fetchDetail = (vmid) => ResourcesService.get(vmid);

/* 每支讀取的 enabled 預設為「有 vmid 才抓」；寫在函式內而不是參數預設值，
   參數預設值是運算式時 React Compiler 會略過整支 hook */

/**
 * 單台機器（頁面權限、總覽身分卡、規格分頁、進階設定共用一份快取）。
 * 總覽分頁要跟上別處改的狀態、到期、自動關機，預設每 30 秒靜默重抓；
 * 只拿權限或表單旗標的地方傳 refetchInterval: false。
 */
export function useResourceQuery(vmid, { enabled, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({
    queryKey: resourceDetailKeys.detail(vmid),
    queryFn: () => fetchDetail(vmid),
    enabled: enabled ?? vmid != null,
    refetchInterval,
  });
}

/**
 * 即時用量（CPU／記憶體／磁碟／uptime）。
 * 關掉 structuralSharing：每次抓回來都要是新物件，指標卡靠它判斷「剛到一筆」而閃綠點。
 */
export function useCurrentStatsQuery(vmid, { enabled, refetchInterval = CURRENT_STATS_INTERVAL } = {}) {
  return useQuery({
    queryKey: resourceDetailKeys.currentStats(vmid),
    queryFn: () => ResourcesService.getCurrentStats(vmid),
    enabled: enabled ?? vmid != null,
    refetchInterval,
    structuralSharing: false,
  });
}

/** RRD 趨勢；換時間範圍時先沿用上一段的資料，不閃空白 */
export function useResourceStatsQuery(vmid, timeframe = "hour", { enabled, refetchInterval = RRD_STATS_INTERVAL } = {}) {
  return useQuery({
    queryKey: resourceDetailKeys.stats(vmid, timeframe),
    queryFn: () => ResourcesService.getStats(vmid, timeframe),
    select: (res) => res?.data ?? [],
    enabled: enabled ?? vmid != null,
    refetchInterval,
    placeholderData: keepPreviousData,
  });
}

export function useSnapshotsQuery(vmid, { enabled } = {}) {
  return useQuery({
    queryKey: resourceDetailKeys.snapshots(vmid),
    queryFn: () => ResourcesService.listSnapshots(vmid),
    enabled: enabled ?? vmid != null,
    /* 只在進分頁與操作後抓（同原本）：切回視窗重抓失敗會再跳一次錯誤，快照／備份清單失敗還會觸發可用性重查 */
    refetchOnWindowFocus: false,
  });
}

export function useBackupsQuery(vmid, { enabled } = {}) {
  return useQuery({
    queryKey: resourceDetailKeys.backups(vmid),
    queryFn: () => ResourcesService.listBackups(vmid),
    enabled: enabled ?? vmid != null,
    /* 只在進分頁與操作後抓（同原本）：切回視窗重抓失敗會再跳一次錯誤，快照／備份清單失敗還會觸發可用性重查 */
    refetchOnWindowFocus: false,
  });
}

/** 這台機器「當下」能不能做快照（磁碟所在 storage 不支援時為否） */
export function useSnapshotCapabilityQuery(vmid, { enabled } = {}) {
  return useQuery({
    queryKey: resourceDetailKeys.snapshotCapability(vmid),
    queryFn: () => ResourcesService.getSnapshotCapability(vmid),
    enabled: enabled ?? vmid != null,
    /* 只在進頁與操作失敗後明確重查（同原本）：查不到一律當不可用，切回視窗時剛好重查失敗會把使用者
       正在用的快照／備份分頁收掉、踢回總覽，所以不跟著切回視窗或恢復連線重抓 */
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
}

/** 所屬叢集有沒有開放備份（快照不能用的機器才查） */
export function useBackupCapabilityQuery(vmid, { enabled } = {}) {
  return useQuery({
    queryKey: resourceDetailKeys.backupCapability(vmid),
    queryFn: () => ResourcesService.getBackupCapability(vmid),
    enabled: enabled ?? vmid != null,
    /* 只在進頁與操作失敗後明確重查（同原本）：查不到一律當不可用，切回視窗時剛好重查失敗會把使用者
       正在用的快照／備份分頁收掉、踢回總覽，所以不跟著切回視窗或恢復連線重抓 */
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
}

/**
 * 登入帳號／代管密碼／金鑰（端點只給擁有者，被分享者不要抓）。
 * 回應含私鑰與密碼明文：沒有畫面在看就立刻丟掉（gcTime 0），不沿用一般清單保留五分鐘的政策；
 * 請求帶 signal，離頁時還在路上的回應不會再被寫回快取。
 */
export function useSshKeyQuery(vmid, { enabled } = {}) {
  return useQuery({
    queryKey: resourceDetailKeys.sshKey(vmid),
    queryFn: ({ signal }) => ResourcesService.getSshKey(vmid, { signal }),
    enabled: enabled ?? vmid != null,
    gcTime: 0,
    /* 只在進分頁與操作後抓（同原本）：切回視窗重抓失敗會再跳一次錯誤，快照／備份清單失敗還會觸發可用性重查 */
    refetchOnWindowFocus: false,
  });
}

/** 來源範本的使用手冊附件（非克隆機或無附件時 count=0） */
export function useTemplateManualQuery(vmid, { enabled } = {}) {
  return useQuery({
    queryKey: resourceDetailKeys.templateManual(vmid),
    queryFn: () => ResourcesService.getTemplateManual(vmid),
    enabled: enabled ?? vmid != null,
  });
}

export function useSharesQuery(vmid, { enabled } = {}) {
  return useQuery({
    queryKey: resourceDetailKeys.shares(vmid),
    queryFn: async () => (await ResourcesService.listShares(vmid)) ?? [],
    enabled: enabled ?? vmid != null,
    /* 只在進分頁與操作後抓（同原本）：切回視窗重抓失敗會再跳一次錯誤，快照／備份清單失敗還會觸發可用性重查 */
    refetchOnWindowFocus: false,
  });
}

export function useCredentialsQuery(vmid, { enabled } = {}) {
  return useQuery({
    queryKey: resourceDetailKeys.credentials(vmid),
    queryFn: () => ResourcesService.getCredentials(vmid),
    enabled: enabled ?? vmid != null,
    /* 只在進分頁與操作後抓（同原本）：切回視窗重抓失敗會再跳一次錯誤，快照／備份清單失敗還會觸發可用性重查 */
    refetchOnWindowFocus: false,
  });
}

/** 開機順序／ISO 掛載；卡片是編輯器，切回視窗不重抓，免得蓋掉排到一半的順序 */
export function useBootOptionsQuery(vmid, { enabled } = {}) {
  return useQuery({
    queryKey: resourceDetailKeys.bootOptions(vmid),
    queryFn: () => ResourcesService.getBootOptions(vmid),
    enabled: enabled ?? vmid != null,
    refetchOnWindowFocus: false,
  });
}

/** 可掛載的 ISO（只有支援 CD-ROM 的機器才查；查不到當成沒有） */
export function useIsoImagesQuery(vmid, { enabled } = {}) {
  return useQuery({
    queryKey: resourceDetailKeys.isoImages(vmid),
    queryFn: async () => (await ResourcesService.listIsoImages(vmid).catch(() => [])) ?? [],
    enabled: enabled ?? vmid != null,
  });
}

/** 實際生效的規格（cpu_cores／memory_mb／disk_gb）；表單用，不輪詢、切回視窗也不重抓，只在明確事件後重抓 */
export function useSpecsQuery(vmid, { enabled } = {}) {
  return useQuery({
    queryKey: resourceDetailKeys.specs(vmid),
    queryFn: () => ResourcesService.getSpecs(vmid),
    enabled: enabled ?? vmid != null,
    refetchInterval: false,
    refetchOnWindowFocus: false,
  });
}

export function useResourceDetailCacheUpdater() {
  const queryClient = useQueryClient();
  const invalidateKey = (key) => queryClient.invalidateQueries({ queryKey: key });
  return {
    /** 單台機器更新回來的資料就地寫進詳情快取 */
    update(detail) {
      if (detail?.vmid == null) return;
      queryClient.setQueryData(resourceDetailKeys.detail(detail.vmid), detail);
    },
    /**
     * 操作後重抓這台的詳情並回傳新資料（失敗時回 undefined、快取保留上一筆）。
     * 先取消還在路上的背景刷新：操作前送出的舊請求不能比操作後的這次晚到而蓋掉。
     */
    async refreshDetail(vmid) {
      const queryKey = resourceDetailKeys.detail(vmid);
      await queryClient.cancelQueries({ queryKey });
      try {
        return await queryClient.fetchQuery({ queryKey, queryFn: () => fetchDetail(vmid), staleTime: 0 });
      } catch {
        /* 錯誤已記在 query 狀態上；畫面沿用上一筆，下次背景刷新再試 */
        return undefined;
      }
    },
    /** 寫入前先取消還在路上的重抓，操作前送出的舊清單不能晚到而蓋掉剛寫入的 */
    async setCredentials(vmid, updater) {
      const queryKey = resourceDetailKeys.credentials(vmid);
      await queryClient.cancelQueries({ queryKey });
      queryClient.setQueryData(queryKey, updater);
    },
    setBootOptions(vmid, data) {
      queryClient.setQueryData(resourceDetailKeys.bootOptions(vmid), data);
    },
    /** 這台機器的所有讀取都重抓 */
    invalidate(vmid) {
      return invalidateKey(resourceDetailKeys.scope(vmid));
    },
    invalidateDetail(vmid) {
      return invalidateKey(resourceDetailKeys.detail(vmid));
    },
    invalidateSnapshots(vmid) {
      return invalidateKey(resourceDetailKeys.snapshots(vmid));
    },
    invalidateBackups(vmid) {
      return invalidateKey(resourceDetailKeys.backups(vmid));
    },
    /** 快照／備份操作失敗後重查可用性（快照與備份兩個都查；停用中的那個只標過期，啟用時才抓） */
    invalidateCapabilities(vmid) {
      return Promise.all([
        invalidateKey(resourceDetailKeys.snapshotCapability(vmid)),
        invalidateKey(resourceDetailKeys.backupCapability(vmid)),
      ]);
    },
    /**
     * 換密碼或金鑰之後：舊的明文不能再顯示，所以不是標過期（畫面會先拿舊值再背景重抓），
     * 而是取消在途讀取、清空快取；有畫面在看就重抓，重抓失敗也不會留著舊秘密。
     */
    async resetSshKey(vmid) {
      const queryKey = resourceDetailKeys.sshKey(vmid);
      await queryClient.cancelQueries({ queryKey });
      return queryClient.resetQueries({ queryKey });
    },
    invalidateShares(vmid) {
      return invalidateKey(resourceDetailKeys.shares(vmid));
    },
    invalidateCredentials(vmid) {
      return invalidateKey(resourceDetailKeys.credentials(vmid));
    },
    invalidateSpecs(vmid) {
      return invalidateKey(resourceDetailKeys.specs(vmid));
    },
  };
}

/**
 * 在詳情頁改了這台機器之後（規格、生命週期、對外連線…）：重抓詳情，
 * 並把新的那一筆就地寫回「我的資源」與「資源管理」清單快取（清單沒載過就不動）。
 * 回傳新資料；重抓失敗回 undefined，畫面與清單都沿用上一筆。
 */
export function useRefreshResourceDetail() {
  const detailCache = useResourceDetailCacheUpdater();
  const mineCache = useResourceCacheUpdater(resourceKeys.mine);
  const everyoneCache = useResourceCacheUpdater(resourceKeys.everyone);
  return async (vmid) => {
    const fresh = await detailCache.refreshDetail(vmid);
    if (fresh?.vmid != null) await Promise.all([mineCache.update(fresh), everyoneCache.update(fresh)]);
    return fresh;
  };
}

/* 通用的載入／錯誤狀態 hook 搬到 hooks/useQueryLoadState（全站共用），這裡轉出給既有的 import */
export { useLoadError, useQueryErrorEffect } from "../hooks/useQueryLoadState";
