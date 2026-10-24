import { QueryClientProvider } from "@tanstack/react-query";

import { createQueryClient } from "./queryClient";

/**
 * 測試用：每個測試自己的 QueryClient（不重試、不保留快取、不輪詢），
 * 頁面測試把元件包進 `withQueryClient(<Page />)` 就能跑 useQuery。
 */
export function createTestQueryClient() {
  return createQueryClient({
    queries: { retry: false, staleTime: 0, gcTime: 0, refetchInterval: false, refetchOnWindowFocus: false },
  });
}

export function withQueryClient(ui, client = createTestQueryClient()) {
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}
