import { apiPost } from "./api";

const BASE = "/api/v1/ai/template-recommendation";

export const AiTemplateRecommendationApi = {
  chat(requestBody) {
    return apiPost(`${BASE}/chat`, requestBody);
  },

  recommend(requestBody) {
    return apiPost(`${BASE}/recommend`, requestBody);
  },
};
