import { apiGet } from "./api";

const BASE = "/api/v1/ai-api/monitoring";

function buildRange(q, params) {
  if (params?.startDate) q.set("start_date", params.startDate);
  if (params?.endDate)   q.set("end_date",   params.endDate);
}

export const AiMonitoringService = {
  /** 全域 AI 統計卡片 */
  stats(params) {
    const q = new URLSearchParams();
    buildRange(q, params);
    const qs = q.toString();
    return apiGet(`${BASE}/stats${qs ? `?${qs}` : ""}`);
  },

  /** Proxy 呼叫清單 */
  listProxyCalls(params) {
    const q = new URLSearchParams();
    buildRange(q, params);
    if (params?.userId)     q.set("user_id",    params.userId);
    if (params?.modelName)  q.set("model_name", params.modelName);
    if (params?.status)     q.set("status",     params.status);
    if (params?.skip != null)  q.set("skip",  String(params.skip));
    if (params?.limit != null) q.set("limit", String(params.limit));
    const qs = q.toString();
    return apiGet(`${BASE}/api-calls${qs ? `?${qs}` : ""}`);
  },

  /** Template 呼叫清單 */
  listTemplateCalls(params) {
    const q = new URLSearchParams();
    buildRange(q, params);
    if (params?.userId)    q.set("user_id",   params.userId);
    if (params?.callType)  q.set("call_type", params.callType);
    if (params?.preset)    q.set("preset",    params.preset);
    if (params?.status)    q.set("status",    params.status);
    if (params?.skip != null)  q.set("skip",  String(params.skip));
    if (params?.limit != null) q.set("limit", String(params.limit));
    const qs = q.toString();
    return apiGet(`${BASE}/template-calls${qs ? `?${qs}` : ""}`);
  },

  /** 使用者用量彙總 */
  listUsersUsage(params) {
    const q = new URLSearchParams();
    buildRange(q, params);
    if (params?.skip != null)  q.set("skip",  String(params.skip));
    if (params?.limit != null) q.set("limit", String(params.limit));
    const qs = q.toString();
    return apiGet(`${BASE}/users${qs ? `?${qs}` : ""}`);
  },
};
