import { apiGet, apiPost, apiPut, apiDelete } from "./api";

export const ReverseProxyService = {
  /** Admin: Traefik runtime 快照（entrypoints / routers / services） */
  runtime() {
    return apiGet("/api/v1/reverse-proxy/runtime");
  },

  /** 目前使用者可見的網域規則 */
  listRules() {
    return apiGet("/api/v1/reverse-proxy/rules");
  },

  /** 建立規則前的環境檢查（gateway / cloudflare / 可用 zones） */
  setupContext() {
    return apiGet("/api/v1/reverse-proxy/setup-context");
  },

  /** 建立網域規則 */
  createRule(body) {
    return apiPost("/api/v1/reverse-proxy/rules", body);
  },

  /** 更新網域規則 */
  updateRule(ruleId, body) {
    return apiPut(`/api/v1/reverse-proxy/rules/${ruleId}`, body);
  },

  /** 刪除網域規則 */
  deleteRule(ruleId) {
    return apiDelete(`/api/v1/reverse-proxy/rules/${ruleId}`);
  },

  /** Admin: 重新同步所有規則到 Gateway */
  syncRules() {
    return apiPost("/api/v1/reverse-proxy/rules/sync");
  },
};
