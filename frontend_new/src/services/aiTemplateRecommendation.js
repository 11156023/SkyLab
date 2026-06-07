import { apiGet, apiPost } from "./api";

const BASE = "/api/v1/ai/template-recommendation";

export const AiTemplateRecommendationApi = {
  chat(requestBody) {
    return apiPost(`${BASE}/chat`, requestBody);
  },

  recommend(requestBody) {
    return apiPost(`${BASE}/recommend`, requestBody);
  },

  myUsage(params = {}) {
    const query = new URLSearchParams();
    if (params.startDate) query.set("start_date", params.startDate);
    if (params.endDate) query.set("end_date", params.endDate);
    const qs = query.toString();
    return apiGet(`${BASE}/usage/my${qs ? `?${qs}` : ""}`);
  },
};
