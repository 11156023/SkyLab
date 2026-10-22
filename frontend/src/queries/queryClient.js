import { QueryClient } from "@tanstack/react-query";

/**
 * TanStack Query 的全站預設。
 *
 * - retry: false — services/api.js 已處理 401 續期；PVE 清單失敗要立刻顯示錯誤讓使用者重試，
 *   自動重試只會讓錯誤畫面晚好幾秒才出現。
 * - staleTime 5 秒 — 切回分頁、換頁回來時 5 秒內不重抓（沿用舊版自動刷新的節流）；換頁回來時先用快取畫面，
 *   背景再更新，不再每次進頁都空白等 API。
 * - refetchIntervalInBackground: false — 分頁隱藏時所有輪詢暫停（原本各頁 setInterval 裡的
 *   document.hidden 檢查）。
 */
export const DEFAULT_STALE_TIME = 5_000;
/** 清單頁的背景刷新間隔（30 秒） */
export const LIST_REFRESH_INTERVAL = 30_000;

export function createQueryClient(overrides = {}) {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        staleTime: DEFAULT_STALE_TIME,
        refetchOnWindowFocus: true,
        refetchIntervalInBackground: false,
        ...overrides.queries,
      },
      mutations: { retry: false, ...overrides.mutations },
    },
  });
}

/** 應用程式唯一的 QueryClient（main.jsx 掛進 QueryClientProvider） */
export const queryClient = createQueryClient();
