import { apiGet, apiPost } from "./api";

export const ReverseProxyService = {
  /** 建立規則前的環境檢查（gateway / cloudflare / 可用 zones） */
  setupContext() {
    return apiGet("/api/v1/reverse-proxy/setup-context");
  },

  /** Admin: 重新同步所有規則到 Gateway */
  syncRules() {
    return apiPost("/api/v1/reverse-proxy/rules/sync");
  },

  /**
   * 網域是否可用（同時查本系統紀錄與 Cloudflare 既有紀錄）
   * → { domain, available, reason: system|external|invalid|no_zone|unverified|null, message }
   */
  checkDomainAvailability(domain, excludeRuleId) {
    const query = new URLSearchParams({ domain });
    if (excludeRuleId) query.set("exclude_rule_id", excludeRuleId);
    return apiGet(`/api/v1/reverse-proxy/domain-availability?${query.toString()}`);
  },
};
