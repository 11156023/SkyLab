import { useQuery, useQueryClient } from "@tanstack/react-query";

import { ResourcesService } from "../services/resources";
import { QuickPracticeService } from "../services/quickPractice";
import { LIST_REFRESH_INTERVAL } from "./queryClient";

/** 有機器開機中時追 5 秒一次（原 resourceRows.BOOTING_POLL_INTERVAL） */
export const BOOTING_POLL_INTERVAL = 5_000;

export const resourceKeys = Object.freeze({
  all: ["resources"],
  mine: ["resources", "mine"],
});

/** 快速練習 session 失敗不拖垮機器清單（與原頁面相同） */
async function fetchMyResources({ signal }) {
  const [resources, sessions] = await Promise.all([
    ResourcesService.list({ signal }),
    QuickPracticeService.listMySessions({ signal }).catch(() => []),
  ]);
  return { resources: resources ?? [], sessions: sessions ?? [] };
}

export function bootingPollInterval(data) {
  return (data?.resources ?? []).some((r) => r.status === "starting")
    ? BOOTING_POLL_INTERVAL
    : LIST_REFRESH_INTERVAL;
}

/** 我的機器＋我的快速練習 session（一次抓齊，背景 30 秒刷新、開機中 5 秒） */
export function useMyResourcesQuery() {
  return useQuery({
    queryKey: resourceKeys.mine,
    queryFn: fetchMyResources,
    refetchInterval: (query) => bootingPollInterval(query.state.data),
  });
}

/**
 * 單列操作（開關機、刪除）後就地更新快取。
 * 更新前先取消還在路上的背景刷新：清單經 PVE 要好幾秒，操作前送出的刷新可能比操作後才回來，
 * 舊快照不能蓋掉剛改好的那一列。首次載入還沒資料時不動（沒有東西可更新，也不能把首載取消掉）。
 */
export function useResourceCacheUpdater(queryKey = resourceKeys.mine) {
  const queryClient = useQueryClient();
  /* cancelQueries 要等它完成再寫：取消會把 query 還原成「這次 fetch 開始前」的狀態，
     沒等就先 setQueryData 的話，還原會把剛寫進去的那一列蓋掉 */
  const patch = async (mapper) => {
    if (queryClient.getQueryData(queryKey) === undefined) return;
    await queryClient.cancelQueries({ queryKey });
    queryClient.setQueryData(queryKey, (prev) => (prev ? { ...prev, resources: mapper(prev.resources) } : prev));
  };
  return {
    update(updated) {
      return patch((list) => list.map((r) => (r.vmid === updated.vmid ? updated : r)));
    },
    remove(vmid) {
      return patch((list) => list.filter((r) => r.vmid !== vmid));
    },
    invalidate() {
      return queryClient.invalidateQueries({ queryKey });
    },
  };
}
