import { useQuery } from "@tanstack/react-query";

import { AiApiChatService } from "../services/aiApiChat";

/**
 * 金鑰的短指紋（FNV-1a 32 位元＋長度）：模型清單依「用哪把金鑰查的」分快取，
 * 但 key 不放明文金鑰（query hash 會留在快取的索引裡）。
 */
export function apiKeyFingerprint(apiKey) {
  const text = String(apiKey ?? "");
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return `${text.length}:${hash.toString(16)}`;
}

export const aiApiChatKeys = Object.freeze({
  all: ["aiApiChat"],
  models: (apiKey) => ["aiApiChat", "models", apiKey ? apiKeyFingerprint(apiKey) : null],
});

/**
 * 「API 聊天」可用的模型（用登入者自己的金鑰向 /ai-proxy/models 查）。
 * 不輪詢；切換金鑰會換 key 重查。gcTime 0：聊天分頁一卸載，帶著金鑰的 queryFn 就從快取移除。
 */
export function useAiModelsQuery(apiKey, { enabled = Boolean(apiKey) } = {}) {
  return useQuery({
    queryKey: aiApiChatKeys.models(apiKey),
    queryFn: ({ signal }) => AiApiChatService.listModels({ signal, apiKey }),
    enabled,
    gcTime: 0,
  });
}
