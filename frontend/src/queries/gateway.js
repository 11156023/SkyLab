import { useQuery, useQueryClient } from "@tanstack/react-query";

import { GatewayService } from "../services/gateway";
import { LIST_REFRESH_INTERVAL } from "./queryClient";

export const gatewayKeys = Object.freeze({
  all: ["gateway"],
  config: ["gateway", "config"],
  serviceStatus: (service) => ["gateway", "serviceStatus", String(service)],
  wireGuardOverview: ["gateway", "wireGuardOverview"],
  serviceConfig: (service) => ["gateway", "serviceConfig", String(service)],
  serviceLogs: (service, lines) => ["gateway", "serviceLogs", String(service), lines],
  installStatus: ["gateway", "installStatus"],
  certificate: ["gateway", "certificate"],
  certificateStatus: ["gateway", "certificateStatus"],
  platformEntry: ["gateway", "platformEntry"],
  platformEntryStatus: ["gateway", "platformEntryStatus"],
});

/* 一鍵安裝進行中每 3 秒經 SSH 讀一次日誌；其他狀態不輪詢 */
export const INSTALL_POLL_INTERVAL = 3_000;

export function installPollInterval(status) {
  return status?.state === "running" ? INSTALL_POLL_INTERVAL : false;
}

/*
 * 同一種 SSH 檢查一次只跑一條：檢查進行中又被要求刷新（例如儲存後）時，刷新會先取消進行中那次
 * （它的結果已經過時，不會顯示），新的這次等那條 SSH 跑完才真的送出；
 * 等待期間又被取消（連按多次）就直接放棄，所以連按多次也只多跑一次。
 */
const runningChecks = new Map();

async function serializedCheck(name, signal, run) {
  while (runningChecks.has(name)) {
    await runningChecks.get(name).catch(() => {});
  }
  if (signal?.aborted) throw signal.reason ?? new Error("cancelled");
  const call = run();
  runningChecks.set(name, call);
  try {
    return await call;
  } finally {
    if (runningChecks.get(name) === call) runningChecks.delete(name);
  }
}

/** Gateway VM 連線設定（表單型：不輪詢、切回分頁也不重抓，免得蓋掉正在編輯的欄位） */
export function useGatewayConfigQuery({ enabled = true } = {}) {
  return useQuery({
    queryKey: gatewayKeys.config,
    queryFn: () => GatewayService.getConfig(),
    enabled,
    refetchOnWindowFocus: false,
  });
}

/** 服務（nginx／wireguard）狀態 */
export function useGatewayServiceStatusQuery(service, { enabled = Boolean(service), refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({
    queryKey: gatewayKeys.serviceStatus(service),
    queryFn: () => GatewayService.getServiceStatus(service),
    enabled,
    refetchInterval,
    /* 只在進分頁、操作後與按重新載入時抓（同原本）：每次都要經 SSH 問 Gateway，連不上時切回視窗重抓會把整塊換成轉圈、重跳錯誤 */
    refetchOnWindowFocus: false,
  });
}

/** WireGuard 執行期摘要（不含金鑰） */
export function useWireGuardOverviewQuery({ enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({
    queryKey: gatewayKeys.wireGuardOverview,
    queryFn: () => GatewayService.getWireGuardOverview(),
    enabled,
    refetchInterval,
    /* 只在進分頁、操作後與按重新載入時抓（同原本）：每次都要經 SSH 問 Gateway，連不上時切回視窗重抓會把整塊換成轉圈、重跳錯誤 */
    refetchOnWindowFocus: false,
  });
}

/** 服務設定檔內容（編輯器：不輪詢、不因切回分頁重抓） */
export function useGatewayServiceConfigQuery(service, { enabled = Boolean(service) } = {}) {
  return useQuery({
    queryKey: gatewayKeys.serviceConfig(service),
    queryFn: () => GatewayService.readServiceConfig(service),
    enabled,
    refetchOnWindowFocus: false,
  });
}

/**
 * 服務日誌（純文字）。輪詢時前一次還沒回來不會再送（Query 會併進進行中的那次），
 * Gateway 連不上時 SSH 不會越堆越多；抓失敗保留畫面上的內容。
 */
export function useGatewayServiceLogsQuery(service, lines = 100, { enabled = Boolean(service), refetchInterval = false } = {}) {
  return useQuery({
    queryKey: gatewayKeys.serviceLogs(service, lines),
    queryFn: () => GatewayService.getServiceLogs(service, lines),
    enabled,
    refetchInterval,
  });
}

/** 一鍵安裝狀態；refetchInterval 省略時安裝中才 3 秒問一次 */
export function useGatewayInstallStatusQuery({ enabled = true, refetchInterval } = {}) {
  return useQuery({
    queryKey: gatewayKeys.installStatus,
    queryFn: () => GatewayService.getInstallStatus(),
    enabled,
    refetchInterval: refetchInterval ?? ((query) => installPollInterval(query.state.data)),
    /* 安裝中靠輪詢追進度；其餘只在進分頁、操作後與按重新整理時抓（同原本），讀不到時切回視窗不會整塊閃成轉圈 */
    refetchOnWindowFocus: false,
  });
}

/** HTTPS 憑證路徑設定（表單型） */
export function useGatewayCertificateQuery({ enabled = true } = {}) {
  return useQuery({
    queryKey: gatewayKeys.certificate,
    queryFn: () => GatewayService.getCertificate(),
    enabled,
    refetchOnWindowFocus: false,
  });
}

/** Gateway 上實際的憑證狀態（經 SSH 檢查，慢；不自動輪詢，同時只跑一條） */
export function useGatewayCertificateStatusQuery({ enabled = true } = {}) {
  return useQuery({
    queryKey: gatewayKeys.certificateStatus,
    queryFn: ({ signal }) => serializedCheck("certificateStatus", signal, () => GatewayService.getCertificateStatus()),
    enabled,
    refetchOnWindowFocus: false,
  });
}

/** 平台入口設定（表單型） */
export function usePlatformEntryQuery({ enabled = true } = {}) {
  return useQuery({
    queryKey: gatewayKeys.platformEntry,
    queryFn: () => GatewayService.getPlatformEntry(),
    enabled,
    refetchOnWindowFocus: false,
  });
}

/** Gateway 上實際套用的平台入口狀態（經 SSH，慢；不自動輪詢，同時只跑一條） */
export function usePlatformEntryStatusQuery({ enabled = true } = {}) {
  return useQuery({
    queryKey: gatewayKeys.platformEntryStatus,
    queryFn: ({ signal }) => serializedCheck("platformEntryStatus", signal, () => GatewayService.getPlatformEntryStatus()),
    enabled,
    refetchOnWindowFocus: false,
  });
}

export function useGatewayCacheUpdater() {
  const queryClient = useQueryClient();
  /* 取消進行中的那次（結果作廢）再重查；沒有資料時直接 refetch 會併進進行中的請求，所以一定要先取消 */
  const restart = async (queryKey) => {
    await queryClient.cancelQueries({ queryKey });
    return queryClient.refetchQueries({ queryKey });
  };
  return {
    setConfig(config) {
      queryClient.setQueryData(gatewayKeys.config, config);
    },
    setCertificate(certificate) {
      queryClient.setQueryData(gatewayKeys.certificate, certificate);
    },
    setPlatformEntry(entry) {
      queryClient.setQueryData(gatewayKeys.platformEntry, entry);
    },
    /** 寫入設定檔後，快取換成剛寫進去的內容（下次開分頁先顯示這份） */
    setServiceConfig(service, content) {
      queryClient.setQueryData(gatewayKeys.serviceConfig(service), (prev) => ({ ...(prev ?? {}), content }));
    },
    /**
     * 開始安裝回來的狀態：先取消還在路上的重新整理（之前送出、晚回來的結果還是上次的 succeeded，
     * 套用的話會誤報安裝成功／失敗、在安裝中同步規則，並因狀態不是 running 而停止輪詢）
     */
    async setInstallStatus(status) {
      await queryClient.cancelQueries({ queryKey: gatewayKeys.installStatus });
      queryClient.setQueryData(gatewayKeys.installStatus, status);
    },
    /** 憑證狀態重查（儲存後、按重新整理）；進行中那次作廢，等它的 SSH 跑完再查一次 */
    refreshCertificateStatus() {
      return restart(gatewayKeys.certificateStatus);
    },
    refreshPlatformEntryStatus() {
      return restart(gatewayKeys.platformEntryStatus);
    },
    invalidateService(service) {
      return Promise.all([
        queryClient.invalidateQueries({ queryKey: gatewayKeys.serviceStatus(service) }),
        queryClient.invalidateQueries({ queryKey: gatewayKeys.serviceConfig(service) }),
      ]);
    },
    invalidate() {
      return queryClient.invalidateQueries({ queryKey: gatewayKeys.all });
    },
  };
}
