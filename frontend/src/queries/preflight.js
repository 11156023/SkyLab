import { useQuery } from "@tanstack/react-query";

import { AccountService } from "../services/account";

/**
 * 登入後的服務檢查（GET /users/me/preflight）。
 * 這是每次登入都要真的跑一次的檢查，不是「讀來顯示」的快取：
 * - 每一輪檢查用自己的 key（attempt 遞增），按「重新檢查」從空白開始轉圈圈，不沿用上一輪的結果
 * - 用完立刻丟掉（gcTime 0），下次登入一定重新檢查；不重試、不輪詢、切回分頁或網路恢復都不重跑
 * 後端自己有 10 秒 single-flight 快取，前端不再快取。
 */
export const preflightKeys = Object.freeze({
  all: ["preflight"],
  check: (attempt, refresh) => ["preflight", "check", attempt, Boolean(refresh)],
});

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/* 成功時至少等 minDurationMs 才回來；失敗不等（與 Promise.all 相同：任一個失敗就立刻結束） */
async function runPreflight(refresh, minDurationMs) {
  const [result] = await Promise.all([AccountService.preflight({ refresh }), wait(minDurationMs)]);
  // TanStack Query 不收 undefined；空回應照原本的流程當成「沒有通過」（每次都是新物件，頁面靠參照分辨是哪一輪的結果）
  return result ?? {};
}

/**
 * @param {object} [options]
 * @param {number} [options.attempt] 第幾輪檢查（按「重新檢查」就加一）
 * @param {boolean} [options.refresh] 略過後端快取重新探測（只對管理員有效）
 * @param {number} [options.minDurationMs] 圈圈至少轉這麼久，太快跑完反而像沒檢查
 */
export function useLoginPreflightQuery({ attempt, refresh, minDurationMs } = {}) {
  return useQuery({
    queryKey: preflightKeys.check(attempt ?? 0, refresh),
    queryFn: () => runPreflight(Boolean(refresh), minDurationMs ?? 0),
    staleTime: 0,
    gcTime: 0,
    retry: false,
    refetchInterval: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
}
