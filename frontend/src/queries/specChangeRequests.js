import { useQuery, useQueryClient } from "@tanstack/react-query";

import { SpecChangeRequestsService } from "../services/specChangeRequests";
import { LIST_REFRESH_INTERVAL } from "./queryClient";

/** 規格調整套用中時 5 秒追一次（原 RequestsPage.SPEC_APPLY_POLL_MS） */
export const SPEC_APPLY_POLL_INTERVAL = 5_000;

export const specChangeRequestKeys = Object.freeze({
  all: ["specChangeRequests"],
  mine: ["specChangeRequests", "mine"],
  everyone: (params) => ["specChangeRequests", "everyone", params ?? {}],
});

/** 管理員：全部規格調整申請；params 與 SpecChangeRequestsService.listAll 相同 */
export function useAllSpecChangeRequestsQuery(params, { enabled = true, refetchInterval = LIST_REFRESH_INTERVAL, select } = {}) {
  return useQuery({
    queryKey: specChangeRequestKeys.everyone(params),
    queryFn: () => SpecChangeRequestsService.listAll(params),
    enabled,
    refetchInterval,
    select,
  });
}

async function fetchMySpecChangeRequests() {
  const res = await SpecChangeRequestsService.listMy();
  return res?.data ?? [];
}

export function isApplying(items) {
  return (items ?? []).some((r) => r.apply_status === "applying");
}

/** 我的規格調整申請；載入失敗只影響自己那一段，不拖垮主列表（頁面各自看 data） */
export function useMySpecChangeRequestsQuery({ enabled = true } = {}) {
  return useQuery({
    queryKey: specChangeRequestKeys.mine,
    queryFn: fetchMySpecChangeRequests,
    enabled,
    refetchInterval: (query) => (isApplying(query.state.data) ? SPEC_APPLY_POLL_INTERVAL : LIST_REFRESH_INTERVAL),
  });
}

export function useSpecChangeRequestCacheUpdater() {
  const queryClient = useQueryClient();
  return {
    /** 寫入回應就地換掉那一張。先取消在途的背景刷新：它在操作前送出，晚回來會把剛寫的「套用中」蓋回「可套用」 */
    async update(updated) {
      await queryClient.cancelQueries({ queryKey: specChangeRequestKeys.mine });
      queryClient.setQueryData(specChangeRequestKeys.mine, (prev) => (
        prev ? prev.map((r) => (r.id === updated.id ? updated : r)) : prev
      ));
    },
    invalidate() {
      return queryClient.invalidateQueries({ queryKey: specChangeRequestKeys.mine });
    },
    invalidateEveryone() {
      return queryClient.invalidateQueries({ queryKey: ["specChangeRequests", "everyone"] });
    },
  };
}
