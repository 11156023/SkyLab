import { apiGet, apiPost, apiPut, apiPatch, apiDelete } from "./api";

function toQuery(params = {}) {
  const q = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") q.set(key, String(value));
  }
  const qs = q.toString();
  return qs ? `?${qs}` : "";
}

export const CloudflareService = {
  /** 取得供應商連線設定狀態 */
  getConfig() {
    return apiGet("/api/v1/cloudflare/config");
  },

  /** 更新 account_id / api_token / 預設 DNS target */
  updateConfig(body) {
    return apiPut("/api/v1/cloudflare/config", body);
  },

  /** 測試 Cloudflare 連線 */
  testConfig() {
    return apiPost("/api/v1/cloudflare/config/test");
  },

  /** Zone 列表（page / per_page / search / status） */
  listZones(params) {
    return apiGet(`/api/v1/cloudflare/zones${toQuery(params)}`);
  },

  /** 建立 Zone */
  createZone(body) {
    return apiPost("/api/v1/cloudflare/zones", body);
  },

  /** DNS record 列表（page / per_page / search / type / proxied） */
  listDnsRecords(zoneId, params) {
    return apiGet(`/api/v1/cloudflare/zones/${zoneId}/dns-records${toQuery(params)}`);
  },

  /** 新增 DNS record */
  createDnsRecord(zoneId, body) {
    return apiPost(`/api/v1/cloudflare/zones/${zoneId}/dns-records`, body);
  },

  /** 更新 DNS record */
  updateDnsRecord(zoneId, recordId, body) {
    return apiPatch(`/api/v1/cloudflare/zones/${zoneId}/dns-records/${recordId}`, body);
  },

  /** 刪除 DNS record */
  deleteDnsRecord(zoneId, recordId) {
    return apiDelete(`/api/v1/cloudflare/zones/${zoneId}/dns-records/${recordId}`);
  },
};
