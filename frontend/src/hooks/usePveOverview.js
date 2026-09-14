import { useCallback, useEffect, useRef, useState } from "react";
import useAutoRefresh from "./useAutoRefresh";
import { MonitoringService } from "../services/monitoring";

const REFRESH_INTERVAL_MS = 30_000;

/**
 * PVE 全域監控匯總。原本住在 PveOperationsQuickLook 裡，因為首頁改成
 * 按急迫度分層之後，issues 要放進「現在就處理」、運作數字要放進
 * 「只是知會」，同一份資料餵兩個區塊，就不能再綁在單一元件內。
 *
 * 自動更新期間的節流與 in-flight 防重入都保留：背景更新不會蓋掉
 * 使用者剛按下的手動重整，手動重整也不會排隊疊加。
 */
export default function usePveOverview() {
  const [overview, setOverview] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(false);
  const inFlightRef = useRef(false);
  const lastRefreshAtRef = useRef(0);

  const load = useCallback(async ({ silent = false, signal } = {}) => {
    const now = Date.now();
    if (silent && now - lastRefreshAtRef.current < REFRESH_INTERVAL_MS) return;
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    lastRefreshAtRef.current = now;
    if (!silent) setRefreshing(true);
    try {
      const next = await MonitoringService.getOverview({ signal });
      if (!signal?.aborted) {
        setOverview(next);
        setError(false);
      }
    } catch (err) {
      if (!err?.cancelled && !signal?.aborted) setError(true);
    } finally {
      inFlightRef.current = false;
      if (!signal?.aborted) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    load({ signal: controller.signal });
    return () => controller.abort();
  }, [load]);

  useAutoRefresh(() => load({ silent: true }), REFRESH_INTERVAL_MS);

  return { overview, loading, refreshing, error, reload: load };
}
