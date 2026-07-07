import { apiGet, apiPut } from "./api";

/**
 * 治理設定（管理員）：閾值告警、TTL 回收、閒置偵測、反挖礦、
 * 克隆併發上限、快照治理。欄位見後端 GovernanceConfigPublic。
 */
export const GovernanceService = {
  getConfig() {
    return apiGet("/api/v1/governance/config");
  },

  /** partial 更新（只送有變更的欄位） */
  updateConfig(body) {
    return apiPut("/api/v1/governance/config", body);
  },
};
