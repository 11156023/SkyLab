import { apiGet, apiPost } from "./api";

/** 畫面清單只跟身分有關，一次對話期間不會變，載一次就好。 */
let surfacesPromise = null;

export const AiContextualHelpService = {
  /**
   * 目前身分看得到的畫面清單（id / path / title / has_fields）。
   * 用來把 react-router 的路徑對應成 surface_id。
   */
  surfaces() {
    if (!surfacesPromise) {
      surfacesPromise = apiGet("/api/v1/ai/contextual-help/surfaces").catch((error) => {
        surfacesPromise = null; // 失敗不要卡住之後的嘗試
        throw error;
      });
    }
    return surfacesPromise;
  },

  /** 登出或換身分時清掉快取，免得看到上一個帳號的畫面清單。 */
  resetSurfaces() {
    surfacesPromise = null;
  },

  /**
   * 解釋目前畫面：欄位用途、被擋的原因，或這一頁在做什麼。
   *
   * @param {{question: string, surfaceId: string, activeTarget?: string|null,
   *          contextVersion?: number, state?: Record<string, object>}} input
   *   state 只帶與問題相關的欄位，不是整張表單；後端還會依 surface 再過濾一次。
   * @returns {Promise<{intent: string, answer: string, target: string|null,
   *   grounded_in: string[], context_level: number, context_version: number,
   *   used_model: boolean}>}
   */
  explain({ question, surfaceId, activeTarget = null, contextVersion = 0, state = {} }) {
    return apiPost("/api/v1/ai/contextual-help/explain", {
      question: String(question ?? "").slice(0, 500),
      surface_id: surfaceId,
      active_target: activeTarget,
      context_version: contextVersion,
      state,
    });
  },
};

/**
 * 路徑對應到 surface。
 *
 * 先找完全相同的路徑；沒有才比對帶參數的樣板（/my-resources/:vmid）。同一個
 * 路徑可能有多個畫面（/my-requests 同時是列表與表單），那種情況要由頁面自己
 * 註冊來決定是哪一個，這裡只回最後的預設值。
 */
export function matchSurface(surfaces, path) {
  if (!Array.isArray(surfaces) || !path) return null;
  const clean = path.split("?")[0].replace(/\/+$/, "") || "/";

  const exact = surfaces.find((surface) => surface.path === clean);
  if (exact) return exact;

  const segments = clean.split("/");
  return (
    surfaces.find((surface) => {
      const parts = surface.path.split("/");
      if (parts.length !== segments.length) return false;
      return parts.every(
        (part, index) => part.startsWith(":") || part === segments[index],
      );
    }) ?? null
  );
}
