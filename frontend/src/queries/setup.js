import { useQuery, useQueryClient } from "@tanstack/react-query";

import { SetupService } from "../services/setup";
import { installPollInterval } from "./gateway";

/**
 * 首次安裝初始化精靈（/api/v1/setup/*，免登入）。
 * 登入頁與精靈頁共用「系統初始化過了沒」；Gateway／平台入口兩步的設定也放這裡。
 */
export const setupKeys = Object.freeze({
  all: ["setup"],
  status: ["setup", "status"],
  gateway: ["setup", "gateway"],
  /* 不叫 ["setup", "gateway", "install"]：清掉安裝狀態時不能用前綴比對連帶清掉連線設定 */
  gatewayInstall: ["setup", "gatewayInstall"],
  platformEntry: ["setup", "platformEntry"],
});

/*
 * 最後一次拿到的初始化狀態。
 * AuthContext 換身分（登入、登出、session 過期）時會 queryClient.clear()，但初始化狀態不綁身分，
 * 而且整個頁面生命週期只問一次（原本模組層快取的語意）。沒有這份的話，登出回到登入頁、
 * 或裝置授權頁登入後，都要再等一次 API，畫面會先閃「正在驗證登入狀態」才回到登入頁。
 * clear 之後重建的 query 直接以它當初始資料（staleTime Infinity，不會再打 API）。
 */
let lastStatus;

async function fetchStatus() {
  /* 不傳 signal：登入頁卸載、精靈頁接著掛上時，沿用同一個還在路上的請求，不中途取消 */
  const status = await SetupService.getStatus();
  lastStatus = status;
  return status;
}

function initialStatus() {
  return lastStatus;
}

/**
 * 初始化狀態的 query 設定（key＋queryFn）。hook 與非元件程式碼的
 * `queryClient.fetchQuery(setupStatusQueryOptions())` 共用，快取設定只寫在這裡。
 * 拿到一次就不再自動重抓：完成初始化只會從精靈這裡發生（markSetupCompleted 直接寫進快取），
 * 要重問就明確 refetch／fetchQuery 帶 staleTime 0。
 */
export function setupStatusQueryOptions() {
  return {
    queryKey: setupKeys.status,
    queryFn: fetchStatus,
    initialData: initialStatus,
    staleTime: Infinity,
    gcTime: Infinity,
  };
}

/** 初始化狀態：{ completed, completed_at, steps: { admin, proxmox, subnet, gateway, platform_entry } } */
export function useSetupStatusQuery({ enabled = true } = {}) {
  return useQuery({ ...setupStatusQueryOptions(), enabled });
}

/**
 * 寫入初始化狀態（精靈完成時）。updater 拿到的是目前的狀態；快取剛被 clear 掉時拿到最後一次的那份。
 * 也更新 clear 之後重建用的那份，否則換身分後又會讀回舊狀態。
 */
export function setSetupStatusData(client, updater) {
  const next = client.setQueryData(setupKeys.status, (prev) => updater(prev ?? lastStatus));
  if (next !== undefined) lastStatus = next;
  return next;
}

/** 精靈 Gateway 步驟：SSH 連線設定與公鑰 { host, ssh_port, ssh_user, public_key, is_configured }（表單型：不輪詢、切回分頁不重抓） */
export function useSetupGatewayQuery({ enabled = true } = {}) {
  return useQuery({
    queryKey: setupKeys.gateway,
    queryFn: () => SetupService.getGateway(),
    enabled,
    refetchOnWindowFocus: false,
  });
}

/* 安裝進行中每 3 秒經 SSH 讀一次日誌；其他狀態不輪詢（與閘道頁同一條規則） */
function installRefetchInterval(query) {
  return installPollInterval(query.state.data);
}

/**
 * 精靈 Gateway 步驟：一鍵安裝的狀態、日誌、網卡與建議參數（經 SSH）。
 * 安裝中才 3 秒問一次；切回分頁不重抓（每次都是一條 SSH，原本也只在安裝中才刷新）。
 */
export function useSetupGatewayInstallQuery({ enabled = true } = {}) {
  return useQuery({
    queryKey: setupKeys.gatewayInstall,
    queryFn: () => SetupService.getGatewayInstallStatus(),
    enabled,
    refetchInterval: installRefetchInterval,
    refetchOnWindowFocus: false,
  });
}

/** 精靈平台入口步驟：目前的平台入口設定（表單型） */
export function useSetupPlatformEntryQuery({ enabled = true } = {}) {
  return useQuery({
    queryKey: setupKeys.platformEntry,
    queryFn: () => SetupService.getPlatformEntry(),
    enabled,
    refetchOnWindowFocus: false,
  });
}

export function useSetupCacheUpdater() {
  const queryClient = useQueryClient();
  return {
    setGateway(config) {
      queryClient.setQueryData(setupKeys.gateway, config);
    },
    /** 連線對象換了：之前的安裝狀態不算數，還在路上的讀取（輪詢）一併作廢 */
    clearGatewayInstall() {
      queryClient.removeQueries({ queryKey: setupKeys.gatewayInstall });
    },
    /**
     * 開始安裝回來的狀態：先取消還在路上的輪詢（之前送出、晚回來的結果還是上次的狀態，
     * 套用的話會誤報安裝成功／失敗，並因狀態不是 running 而停止輪詢）
     */
    async setGatewayInstall(status) {
      await queryClient.cancelQueries({ queryKey: setupKeys.gatewayInstall });
      queryClient.setQueryData(setupKeys.gatewayInstall, status);
    },
    setPlatformEntry(entry) {
      queryClient.setQueryData(setupKeys.platformEntry, entry);
    },
  };
}
