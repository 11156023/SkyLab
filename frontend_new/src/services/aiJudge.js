import {
  apiDelete,
  apiGet,
  apiGetBlob,
  apiPatch,
  apiPost,
  apiPostBlob,
  apiPostMultipart,
} from "./api";

/** 評分環境模板選項 */
export const TEMPLATE_OPTIONS = [
  { key: "linux", label: "一般 Linux/LXC" },
  { key: "python", label: "Python" },
  { key: "n8n", label: "n8n" },
];

export function getTemplateLabel(templateKey) {
  return (
    TEMPLATE_OPTIONS.find((option) => option.key === templateKey)?.label ??
    "一般 Linux/LXC"
  );
}

/** 把 rubric 分析結果轉成 AI 對話用的 context 字串 */
export function rubricToContext(analysis) {
  return JSON.stringify({
    items: analysis.items,
    total_items: analysis.total_items,
    checked_count: analysis.checked_count,
    summary: analysis.summary,
  });
}

export const AiJudgeService = {
  /* ── 評分表文件 ── */

  /** 列出群組已保存的評分表 */
  listFiles(groupId) {
    return apiGet(`/api/v1/groups/${groupId}/judge/files/`);
  },

  /**
   * 上傳評分表文件並觸發 AI 分析。
   * 同名檔案已存在時後端回 409，可帶 conflictStrategy（"overwrite" | "copy"）重送。
   */
  uploadFile(groupId, file, templateKey, conflictStrategy) {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("template_key", templateKey);
    if (conflictStrategy) formData.append("conflict_strategy", conflictStrategy);
    return apiPostMultipart(`/api/v1/groups/${groupId}/judge/files/`, formData);
  },

  /** 更新已保存評分表的分析結果（項目編輯後持久化） */
  updateFileAnalysis(groupId, fileId, analysis) {
    return apiPatch(
      `/api/v1/groups/${groupId}/judge/files/${fileId}/analysis`,
      { analysis },
    );
  },

  /** 下載評分表原始檔 */
  downloadFile(groupId, fileId) {
    return apiGetBlob(`/api/v1/groups/${groupId}/judge/files/${fileId}/download`);
  },

  /** 刪除評分表（原始檔＋分析結果） */
  deleteFile(groupId, fileId) {
    return apiDelete(`/api/v1/groups/${groupId}/judge/files/${fileId}`);
  },

  /* ── AI 對話與匯出 ── */

  /** 與 AI 對話精煉評分表；isRefine 為全表潤飾 */
  chat({ messages, rubricContext, isRefine = false, templateKey = "linux" }) {
    return apiPost("/api/v1/rubric/chat", {
      messages,
      rubric_context: rubricContext,
      is_refine: isRefine,
      template_key: templateKey,
    });
  },

  /** 將評分項目匯出成 Excel（回傳 Blob） */
  downloadExcel(items, summary) {
    return apiPostBlob("/api/v1/rubric/download-excel", { items, summary });
  },

  /* ── 收集腳本 ── */

  /** 列出群組收集腳本 */
  listScripts(groupId) {
    return apiGet(`/api/v1/groups/${groupId}/judge/scripts/`);
  },

  /** 由評分表快照產生受管收集腳本（後端會接著跑 policy 與 AI 審查） */
  createScript(groupId, { name, templateKey, rubricSnapshot, sourceFileId = null }) {
    return apiPost(`/api/v1/groups/${groupId}/judge/scripts/`, {
      name,
      template_key: templateKey,
      rubric_snapshot: rubricSnapshot,
      source_file_id: sourceFileId,
    });
  },

  /** 重新生成腳本（可帶新的 rubric 快照） */
  regenerateScript(groupId, scriptId, rubricSnapshot = null) {
    return apiPost(
      `/api/v1/groups/${groupId}/judge/scripts/${scriptId}/regenerate`,
      { rubric_snapshot: rubricSnapshot },
    );
  },

  /** 核准腳本（status: reviewed → approved） */
  approveScript(groupId, scriptId) {
    return apiPost(`/api/v1/groups/${groupId}/judge/scripts/${scriptId}/approve`, {});
  },

  /** 刪除腳本 */
  deleteScript(groupId, scriptId) {
    return apiDelete(`/api/v1/groups/${groupId}/judge/scripts/${scriptId}`);
  },

  /* ── 腳本執行 ── */

  /** 對指定 VMID 建立腳本執行任務 */
  createScriptRun(groupId, scriptId, targetVmids) {
    return apiPost(`/api/v1/groups/${groupId}/judge/scripts/${scriptId}/runs`, {
      target_scope: "manual",
      target_vmids: targetVmids,
    });
  },

  /** 查詢執行任務進度與結果（前端輪詢用） */
  getScriptRun(groupId, scriptId, runId) {
    return apiGet(
      `/api/v1/groups/${groupId}/judge/scripts/${scriptId}/runs/${runId}`,
    );
  },
};
