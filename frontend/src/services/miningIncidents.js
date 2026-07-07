import { apiGet, apiPost, apiPut } from "./api";

/** 反挖礦事件（管理員）：清單、停權、誤判解除、資源豁免。 */
export const MiningIncidentsService = {
  /** 事件列表（status: detected|suspended|banned|dismissed；不帶 = 全部） */
  list({ status, limit = 200 } = {}) {
    const q = new URLSearchParams();
    if (status) q.set("status", status);
    q.set("limit", String(limit));
    return apiGet(`/api/v1/mining-incidents?${q.toString()}`);
  },

  /** 確認挖礦 → 停權帳號（VM 維持暫停留證） */
  ban(incidentId) {
    return apiPost(`/api/v1/mining-incidents/${incidentId}/ban`, {});
  },

  /** 判定誤判 → 恢復 VM；exempt=true 一併加入豁免 */
  dismiss(incidentId, { exempt = false, note = null } = {}) {
    return apiPost(`/api/v1/mining-incidents/${incidentId}/dismiss`, { exempt, note });
  },

  /** 設定/解除資源的挖礦偵測豁免 */
  setExemption(vmid, exempt) {
    return apiPut(`/api/v1/mining-incidents/exemptions/${vmid}`, { exempt });
  },
};
