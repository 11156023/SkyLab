import { useQuery, useQueryClient } from "@tanstack/react-query";

import { getTopology, getVmOptions, getVmRules, getVmTopology } from "../services/firewall";
import { LIST_REFRESH_INTERVAL } from "./queryClient";

export const firewallKeys = Object.freeze({
  all: ["firewall"],
  topology: ["firewall", "topology"],
  vmRules: (vmid) => ["firewall", "vm", String(vmid), "rules"],
  vmOptions: (vmid) => ["firewall", "vm", String(vmid), "options"],
  vmTopology: (vmid) => ["firewall", "vm", String(vmid), "topology"],
});

const EMPTY_TOPOLOGY = Object.freeze({ nodes: [], edges: [] });

/**
 * 整張防火牆拓撲（防火牆頁）。背景 30 秒刷新；結構相同時 TanStack Query 會沿用同一個物件，
 * 頁面依 topology 重建節點／邊的 effect 就不會白跑，使用者拖過的位置也不會被重設。
 * 刪線、改規則後呼叫 refetch()／invalidate：在途的舊請求會被取消，不會把已刪的線蓋回來。
 */
export function useFirewallTopologyQuery({ enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({
    queryKey: firewallKeys.topology,
    queryFn: async ({ signal }) => (await getTopology({ signal })) ?? EMPTY_TOPOLOGY,
    enabled,
    refetchInterval,
  });
}

/** 單台機器的規則清單（規則面板、資源詳情的防火牆卡） */
export function useVmFirewallRulesQuery(vmid, { enabled, refetchInterval = false } = {}) {
  return useQuery({
    queryKey: firewallKeys.vmRules(vmid),
    queryFn: () => getVmRules(vmid),
    enabled: enabled ?? (vmid != null),
    refetchInterval,
    /* 只在打開時與寫入後抓（同原本）：切回視窗重抓失敗會再跳一次錯誤 */
    refetchOnWindowFocus: false,
  });
}

/** 單台機器的防火牆選項（啟用狀態、預設策略） */
export function useVmFirewallOptionsQuery(vmid, { enabled, refetchInterval = false } = {}) {
  return useQuery({
    queryKey: firewallKeys.vmOptions(vmid),
    queryFn: () => getVmOptions(vmid),
    enabled: enabled ?? (vmid != null),
    refetchInterval,
    /* 只在打開時與寫入後抓（同原本）：切回視窗重抓失敗會再跳一次錯誤 */
    refetchOnWindowFocus: false,
  });
}

/** 以單台機器為中心的迷你拓撲（資源詳情的防火牆卡） */
export function useVmFirewallTopologyQuery(vmid, { enabled, refetchInterval = false } = {}) {
  return useQuery({
    queryKey: firewallKeys.vmTopology(vmid),
    queryFn: () => getVmTopology(vmid),
    enabled: enabled ?? (vmid != null),
    refetchInterval,
    /* 只在打開時與寫入後抓（同原本）：切回視窗重抓失敗會再跳一次錯誤 */
    refetchOnWindowFocus: false,
  });
}

export function useFirewallCacheUpdater() {
  const queryClient = useQueryClient();
  return {
    /** 連線增刪後：整張拓撲重抓（在途的舊請求會被取消） */
    invalidateTopology() {
      return queryClient.invalidateQueries({ queryKey: firewallKeys.topology });
    },
    /** 某台機器的規則／選項改了：它的規則、選項、迷你拓撲一起重抓 */
    invalidateVm(vmid) {
      return queryClient.invalidateQueries({ queryKey: ["firewall", "vm", String(vmid)] });
    },
    /** 連線增刪後：拓撲與每台機器的規則都可能變（受管規則插在前面，後面的位置跟著位移），全部標成過期重抓 */
    invalidate() {
      return queryClient.invalidateQueries({ queryKey: firewallKeys.all });
    },
    /** 目前快取裡這台的規則（確認視窗按下確認時再對一次位置用） */
    getVmRules(vmid) {
      return queryClient.getQueryData(firewallKeys.vmRules(vmid));
    },
  };
}
