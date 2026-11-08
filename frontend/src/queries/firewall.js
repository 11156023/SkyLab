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

/*
 * 單台機器的三個查詢預設只在打開時與寫入後抓（規則面板沿用原本行為）。
 * 要背景刷新的地方（資源詳情的防火牆卡）自己傳 refetchInterval／refetchOnWindowFocus，
 * 而且要自己處理「同一串失敗只提示一次」，不然每次重抓失敗都會再跳一次錯誤。
 */

/** 單台機器的規則清單（規則面板、資源詳情的防火牆卡） */
export function useVmFirewallRulesQuery(vmid, { enabled, refetchInterval = false, refetchOnWindowFocus = false } = {}) {
  return useQuery({
    queryKey: firewallKeys.vmRules(vmid),
    queryFn: () => getVmRules(vmid),
    enabled: enabled ?? (vmid != null),
    refetchInterval,
    refetchOnWindowFocus,
  });
}

/** 單台機器的防火牆選項（啟用狀態、預設策略） */
export function useVmFirewallOptionsQuery(vmid, { enabled, refetchInterval = false, refetchOnWindowFocus = false } = {}) {
  return useQuery({
    queryKey: firewallKeys.vmOptions(vmid),
    queryFn: () => getVmOptions(vmid),
    enabled: enabled ?? (vmid != null),
    refetchInterval,
    refetchOnWindowFocus,
  });
}

/** 以單台機器為中心的迷你拓撲（資源詳情的防火牆卡） */
export function useVmFirewallTopologyQuery(vmid, { enabled, refetchInterval = false, refetchOnWindowFocus = false } = {}) {
  return useQuery({
    queryKey: firewallKeys.vmTopology(vmid),
    queryFn: () => getVmTopology(vmid),
    enabled: enabled ?? (vmid != null),
    refetchInterval,
    refetchOnWindowFocus,
  });
}

/**
 * 畫面上這條規則在最新清單裡是不是還在同一個位置、內容也沒變。
 * 停用／刪除是用位置（pos）指定的，別人在前面插了規則、這邊又沒更新到時，同一個位置已經是別條。
 */
export function isSameRuleAt(latest, rule) {
  const current = (latest ?? []).find((item) => item.pos === rule.pos);
  return Boolean(current) && JSON.stringify(current) === JSON.stringify(rule);
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
    /**
     * 停用／刪除前向後端抓這台最新的規則清單（不看快取：背景刷新失敗時快取就是舊的，對不出差異），
     * 抓到就寫回快取，畫面跟著換成最新的。
     * 直接打 API、不經 fetchQuery：抓失敗時不把查詢標成失敗，不會再多跳一次「更新失敗」，
     * 由呼叫端提示「這次沒有送出」。失敗時丟出原本的錯誤。
     */
    async fetchLatestVmRules(vmid) {
      const latest = await getVmRules(vmid);
      queryClient.setQueryData(firewallKeys.vmRules(vmid), latest);
      return latest;
    },
  };
}
