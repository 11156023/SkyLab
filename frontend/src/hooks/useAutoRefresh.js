import { useEffect, useRef } from "react";

const DEFAULT_INTERVAL = 30_000;
// 切回分頁／視窗時的最短刷新間隔：alt-tab 來回切換不該每次都打一輪 API
const RESUME_MIN_GAP = 5_000;

/**
 * 週期性自動刷新資料：每 intervalMs 靜默呼叫一次 refresh（分頁隱藏時暫停），
 * 分頁重新可見或視窗重新聚焦時也立即刷新一次（距上次不足 5 秒則略過）。
 * refresh 應為靜默載入（不觸發 loading skeleton）；回傳 Promise 時，
 * 上一輪還沒結束就不會再疊一輪（清單經 PVE 可能要好幾秒）。
 */
export default function useAutoRefresh(refresh, intervalMs = DEFAULT_INTERVAL) {
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;

  useEffect(() => {
    let inFlight = false;
    // 掛載時頁面自己剛載過資料
    let lastRunAt = Date.now();

    const run = () => {
      if (inFlight || document.hidden) return;
      lastRunAt = Date.now();
      const result = refreshRef.current();
      if (result && typeof result.then === "function") {
        inFlight = true;
        Promise.resolve(result)
          .catch(() => {})
          .finally(() => { inFlight = false; });
      }
    };
    const onResume = () => {
      if (Date.now() - lastRunAt >= RESUME_MIN_GAP) run();
    };
    const onVisibility = () => {
      if (!document.hidden) onResume();
    };

    const timer = setInterval(run, intervalMs);
    window.addEventListener("focus", onResume);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", onResume);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [intervalMs]);
}
