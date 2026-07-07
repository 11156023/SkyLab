import { useEffect, useRef } from "react";

const DEFAULT_INTERVAL = 30_000;

/**
 * 週期性自動刷新資料：每 intervalMs 靜默呼叫一次 refresh（分頁隱藏時暫停），
 * 視窗重新聚焦時也立即刷新一次。refresh 應為靜默載入（不觸發 loading skeleton）。
 */
export default function useAutoRefresh(refresh, intervalMs = DEFAULT_INTERVAL) {
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;

  useEffect(() => {
    const timer = setInterval(() => {
      if (!document.hidden) refreshRef.current();
    }, intervalMs);
    const onFocus = () => refreshRef.current();
    window.addEventListener("focus", onFocus);
    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", onFocus);
    };
  }, [intervalMs]);
}
