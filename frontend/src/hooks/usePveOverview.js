import { usePveOverviewQuery } from "../queries/monitoring";
import { useLoadError } from "./useQueryLoadState";

/**
 * PVE 全域監控匯總。原本住在 PveOperationsQuickLook 裡，因為首頁改成
 * 按急迫度分層之後，issues 要放進「現在就處理」、運作數字要放進
 * 「只是知會」，同一份資料餵兩個區塊，就不能再綁在單一元件內。
 *
 * 資料走 TanStack Query（queries/monitoring）：30 秒背景刷新、分頁隱藏時暫停、
 * 管理首頁與監控頁共用同一份快取；重抓的防重入與節流由 query 本身處理。
 * 對外介面不變：
 * - loading：首次載入還沒有結果（成功或失敗都算結果）。PVE 連不上時背景每 30 秒重試，
 *   重試期間不回到 loading，否則首頁的「檢查中」與監控頁的載入畫面會每 30 秒閃一次
 * - refreshing：已有資料、背景或手動重抓中
 * - error：最近一次抓取失敗（有舊資料時 overview 照舊保留，頁面據此標「資料可能過時」）
 * - reload：手動重抓
 */
export default function usePveOverview() {
  const query = usePveOverviewQuery();
  /* 沒資料時 v5 每次重抓都把 status 改回 pending、清掉 error：首次就失敗的話，直接看 isPending／isError
     會在每次背景重試時閃回 loading。useLoadError 在重試期間維持失敗；reload 與原本相同，不回到 loading */
  const loadError = useLoadError(query).error;
  const hasData = query.data !== undefined;
  return {
    overview: query.data ?? null,
    loading: !hasData && !loadError && (query.isPending || query.isFetching),
    refreshing: hasData && query.isFetching,
    error: hasData ? query.isError : Boolean(loadError),
    reload: query.refetch,
  };
}
