/**
 * useSetupStatus — 初始化精靈狀態（GET /setup/status）的共用快取。
 *
 * 登入頁與精靈頁都要問「系統初始化過了沒」，狀態放在 TanStack Query（queries/setup.js）讓兩邊共用同一次請求；
 * 拿到一次就不再自動重問，AuthContext 換身分清快取後也沿用（見 setupStatusQueryOptions）。
 * 精靈完成後呼叫 markSetupCompleted() 立即更新，不必再打一次 API。
 * 後端連不上時視為「不需要初始化」，讓登入頁照常顯示（那裡自有連線失敗的提示）。
 */

import { useQueryView } from "../../hooks/useQueryLoadState";
import { queryClient } from "../../queries/queryClient";
import { setSetupStatusData, setupStatusQueryOptions, useSetupStatusQuery } from "../../queries/setup";

/** 非元件程式碼取狀態：已有就直接回傳，`force` 時一定重問（同時只會有一個請求） */
export function fetchSetupStatus({ force = false } = {}) {
  const options = setupStatusQueryOptions();
  return queryClient.fetchQuery(force ? { ...options, staleTime: 0 } : options);
}

export function markSetupCompleted() {
  setSetupStatusData(queryClient, (prev) => ({
    ...(prev ?? { steps: { admin: true, proxmox: false, subnet: false } }),
    completed: true,
  }));
}

export function useSetupStatus() {
  const query = useSetupStatusQuery();
  /* error：從沒拿到狀態就失敗（背景重試期間不閃）；refresh 回到載入中再問一次 */
  const { loading, error, retry } = useQueryView(query);
  const status = query.data ?? null;

  return {
    status,
    loading,
    error,
    refresh: () => retry().then((result) => (result.isError ? null : result.data ?? null)),
    /** 後端說尚未完成初始化才為 true；取不到狀態時視為不需要 */
    setupRequired: Boolean(status) && !status.completed,
  };
}
