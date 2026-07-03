import { apiPost } from "./api";

export const AiNavigationService = {
  /**
   * 以自然語言解析導航意圖。
   * 回傳 { intent, confidence, action: "navigate"|"suggest"|"clarify",
   *        primary?: { title, path, reason }, suggestions: [...], clarification_question? }
   */
  resolve(query) {
    return apiPost("/api/v1/ai/navigation/resolve", { query });
  },
};
