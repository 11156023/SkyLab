import { apiGet, apiPost, apiPostMultipart } from "./api";

/** 老師教學面板（InstructorUser）：熱圖、配置分發、批次規格。 */
export const TeachingService = {
  /** 群組學習進度熱圖 */
  getHeatmap(groupId) {
    return apiGet(`/api/v1/teaching/heatmap?group_id=${groupId}`);
  },

  /** 配置分發：上傳檔案推送到多台 VM（202，回 task_id） */
  startConfigPush({ file, targetPath, vmids }) {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("target_path", targetPath);
    for (const vmid of vmids) formData.append("vmids", String(vmid));
    return apiPostMultipart("/api/v1/teaching/config-push", formData);
  },

  /** 配置分發任務狀態 */
  getConfigPushStatus(taskId) {
    return apiGet(`/api/v1/teaching/config-push/${taskId}`);
  },

  /** 批次規格調整（202，回 task_id） */
  startBatchSpec({ vmids, group_id, cores, memory_mb }) {
    return apiPost("/api/v1/teaching/batch-spec", {
      vmids,
      group_id,
      cores,
      memory_mb,
    });
  },

  /** 批次規格任務狀態 */
  getBatchSpecStatus(taskId) {
    return apiGet(`/api/v1/teaching/batch-spec/${taskId}`);
  },
};
