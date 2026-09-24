/**
 * useSetupStatus — 初始化精靈狀態（GET /setup/status）的共用快取。
 *
 * 登入頁與精靈頁都要問「系統初始化過了沒」，狀態放在模組層讓兩邊共用同一次請求；
 * 精靈完成後呼叫 markSetupCompleted() 立即更新，不必再打一次 API。
 * 後端連不上時視為「不需要初始化」，讓登入頁照常顯示（那裡自有連線失敗的提示）。
 */

import { useCallback, useEffect, useState } from "react";
import { SetupService } from "../../services/setup";

let cache = null;
let inflight = null;
const listeners = new Set();

function notify() {
  for (const listener of listeners) listener(cache);
}

export function fetchSetupStatus({ force = false } = {}) {
  if (cache && !force) return Promise.resolve(cache);
  if (!inflight) {
    inflight = SetupService.getStatus()
      .then((status) => {
        cache = status;
        notify();
        return status;
      })
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}

export function markSetupCompleted() {
  cache = {
    ...(cache ?? { steps: { admin: true, proxmox: false, subnet: false } }),
    completed: true,
  };
  notify();
}

/** 測試用：清掉模組層快取 */
export function resetSetupStatusCache() {
  cache = null;
  inflight = null;
}

export function useSetupStatus() {
  const [status, setStatus] = useState(cache);
  const [loading, setLoading] = useState(!cache);
  const [error, setError] = useState(null);

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    return fetchSetupStatus({ force: true })
      .then((next) => {
        setStatus(next);
        return next;
      })
      .catch((err) => {
        setError(err);
        return null;
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const listener = (next) => setStatus(next);
    listeners.add(listener);
    let cancelled = false;
    if (!cache) {
      fetchSetupStatus()
        .then((next) => {
          if (!cancelled) setStatus(next);
        })
        .catch((err) => {
          if (!cancelled) setError(err);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }
    return () => {
      cancelled = true;
      listeners.delete(listener);
    };
  }, []);

  return {
    status,
    loading,
    error,
    refresh,
    /** 後端說尚未完成初始化才為 true；取不到狀態時視為不需要 */
    setupRequired: Boolean(status) && !status.completed,
  };
}
