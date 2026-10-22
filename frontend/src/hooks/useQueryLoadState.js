import { useEffect, useRef, useState } from "react";

/**
 * TanStack Query 結果 → 頁面的載入／錯誤畫面狀態（全站共用）。
 *
 * 為什麼不直接看 isError：沒有資料時 TanStack Query v5 每次重抓都把 status 改回 pending、
 * error 清空，所以「首載失敗 → 背景輪詢重試」期間，直接看 isError 的錯誤畫面會在錯誤與轉圈圈
 * 之間閃（輪詢 5 秒的頁面每 5 秒閃一次），NotFound 也會在重抓時跳成一般錯誤。
 */

/**
 * 「從沒載到資料就失敗」的錯誤畫面狀態，給會輪詢、又要顯示錯誤畫面的地方用。
 * - error：掛載（或上次按重試）之後失敗過、而且還沒有資料時，是最後一次的錯誤；背景重試中照樣保留
 * - retry()：使用者按「重試」：回到載入中並重抓
 * 掛載時快取裡留著的舊錯誤不算，先顯示載入中等這次的結果。
 *
 * 「這次失敗是不是新的」用最後一次失敗的時間加次數判斷：
 * - 只看次數不行：次數是每個 query key 各自算的，篩選或分頁換 key 之後新 key 從 0 起算，
 *   會一直追不上舊 key 時記下的基準，錯誤畫面就出不來
 * - 只看時間也不行：重試很快又失敗時可能落在同一毫秒；同一毫秒就再比次數
 */
export function useLoadError(query) {
  const { error, errorUpdatedAt, errorUpdateCount, refetch, isPlaceholderData } = query;
  /* keepPreviousData 的佔位資料是上一個 key 的，不算這個 key 自己的資料：換篩選後失敗、背景重試期間
     data 會變回上一組的佔位資料，若把它當成有資料，錯誤畫面就會閃成上一個篩選的清單 */
  const data = isPlaceholderData ? undefined : query.data;
  const [base, setBase] = useState(() => ({ at: errorUpdatedAt, count: errorUpdateCount }));
  const [lastError, setLastError] = useState(null);
  useEffect(() => {
    if (error) setLastError(error);
  }, [error]);
  const isNewFailure = errorUpdatedAt > base.at
    || (errorUpdatedAt === base.at && errorUpdateCount > base.count);
  const failed = data === undefined && errorUpdatedAt > 0 && isNewFailure;
  return {
    error: failed ? (error ?? lastError ?? true) : null,
    retry() {
      setBase({ at: errorUpdatedAt, count: errorUpdateCount });
      return refetch();
    },
  };
}

/**
 * 每次「新的」讀取失敗呼叫一次 onError(error)（toast、請上層重查可用性）。
 * 以 errorUpdateCount 判斷：掛載時快取裡留著的舊錯誤不算，之後每失敗一次（含操作後重抓、背景重抓）
 * 通知一次，與原本 load() 的 catch 每次失敗都 toast 的行為一致。
 */
export function useQueryErrorEffect(query, onError) {
  const { errorUpdateCount, error } = query;
  const seenRef = useRef(errorUpdateCount);
  const onErrorRef = useRef(onError);
  useEffect(() => {
    onErrorRef.current = onError;
  }, [onError]);
  useEffect(() => {
    if (errorUpdateCount === seenRef.current) return;
    const isNew = errorUpdateCount > seenRef.current;
    seenRef.current = errorUpdateCount;
    if (isNew && error) onErrorRef.current?.(error);
  }, [errorUpdateCount, error]);
}

/**
 * 清單頁的標準畫面狀態：
 * - loading：還沒有資料、也還沒失敗，正在抓（skeleton）
 * - error／retry：見 useLoadError（錯誤畫面在背景重試期間不閃）
 * - refreshing：已有資料、背景更新中（「更新中」指示）
 * 背景刷新失敗時沿用舊資料，不切到錯誤畫面。
 */
export function useQueryView(query) {
  const { error, retry } = useLoadError(query);
  const noData = query.data === undefined;
  return {
    loading: noData && !error && (query.isPending || query.isFetching),
    error,
    retry,
    refreshing: !noData && query.isFetching,
  };
}

/**
 * 讀取失敗時要提示的訊息（給 toast 的 effect 當依賴）：同一串失敗只提示一次。
 * - 還沒有資料：用 useLoadError 的錯誤，背景重試（輪詢、切回視窗）期間不會先清掉再出現
 * - 已有資料：最近一次重抓失敗才有；有資料時 v5 重抓不會清掉 error，持續失敗訊息不變，成功一次後清掉
 * 回傳字串或 null。effect 要依賴這個字串，不要依賴錯誤物件：每次失敗都是新的物件，會每次都再提示。
 *
 *   const failed = useLoadFailureMessage(query, t("Error.generic", { ns: "common" }));
 *   useEffect(() => { if (failed) toast.error(failed); }, [failed, toast]);
 */
export function useLoadFailureMessage(query, fallbackMessage) {
  const { error } = useLoadError(query);
  const hasData = query.data !== undefined && !query.isPlaceholderData;
  const failure = hasData ? (query.isError ? query.error : null) : error;
  if (!failure) return null;
  return failure.message ?? fallbackMessage;
}
