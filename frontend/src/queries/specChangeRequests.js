import { useQuery, useQueryClient } from "@tanstack/react-query";

import { SpecChangeRequestsService } from "../services/specChangeRequests";
import { LIST_REFRESH_INTERVAL } from "./queryClient";

/** 規格調整套用中時 5 秒追一次（原 RequestsPage.SPEC_APPLY_POLL_MS） */
export const SPEC_APPLY_POLL_INTERVAL = 5_000;

export const specChangeRequestKeys = Object.freeze({
  all: ["specChangeRequests"],
  mine: ["specChangeRequests", "mine"],
});

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
    update(updated) {
      queryClient.setQueryData(specChangeRequestKeys.mine, (prev) => (
        prev ? prev.map((r) => (r.id === updated.id ? updated : r)) : prev
      ));
    },
    invalidate() {
      return queryClient.invalidateQueries({ queryKey: specChangeRequestKeys.mine });
    },
  };
}
