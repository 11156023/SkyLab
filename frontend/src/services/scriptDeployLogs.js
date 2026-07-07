import { apiGet } from "./api";

export const ScriptDeployLogsService = {
  /** 列出腳本部署日誌（limit / offset / status / templateSlug / vmid） */
  list(params = {}) {
    const q = new URLSearchParams();
    q.set("limit", String(params.limit ?? 50));
    q.set("offset", String(params.offset ?? 0));
    if (params.status) q.set("status", params.status);
    if (params.templateSlug) q.set("template_slug", params.templateSlug);
    if (params.vmid != null && params.vmid !== "") q.set("vmid", String(params.vmid));
    return apiGet(`/api/v1/script-deploy/logs?${q.toString()}`);
  },

  /** 單筆部署日誌詳情（含 output / error） */
  detail(taskId) {
    return apiGet(`/api/v1/script-deploy/logs/${encodeURIComponent(taskId)}`);
  },
};
