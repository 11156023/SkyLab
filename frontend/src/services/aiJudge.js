import {
  apiDelete,
  apiGet,
  apiGetBlob,
  apiPatch,
  apiPost,
  apiPostBlob,
  apiPostMultipart,
} from "./api";
import i18n from "../i18n";

// 腳本產生會依序執行 generation、policy/quality 修正與 AI reviewer，
// 不能沿用一般 API 的 15 秒 request budget。後端每次 vLLM 呼叫仍有自己的 timeout。
const SCRIPT_GENERATION_TIMEOUT_MS = 7 * 60 * 1000;
// Teacher Judge 的 AI 分析／對話以 backend/config/system-ai.json 的 60 秒為準。
export const TEACHER_JUDGE_REQUEST_TIMEOUT_MS = 60 * 1000;

/** 評分環境模板選項 */
export const TEMPLATE_OPTIONS = [
  { key: "n8n", label: "n8n" },
  { key: "python", label: "Python" },
  { key: "postgresql", label: "PostgreSQL" },
  { key: "linux", label: i18n.t("aiJudge.linuxTemplateLabel", { ns: "services" }) },
];

/** 正式工作區與獨立編輯頁共用的整表潤飾動作。 */
export const RUBRIC_POLISH_PROMPT =
  "請在不改變原始評分目標的前提下潤飾目前檢查表：檢查每個項目的描述與成功條件，將自動檢測支援狀態判定為 auto、partial 或 manual，並補充檢測方式、missing_information、fallback、check_steps 與必要 parameters，讓下一層檢查 AI 能理解。只有客觀判準、平台能力與完整執行資訊都具備時才能標為 auto；若缺少服務名稱、工作目錄、執行命令、Port 或成功條件，請標為 partial 並明確列出缺口，不要猜測。不要改成較容易但不同的檢查目標。即使內容不需修改，也請回傳完整評分項目列表。將目前評分環境視為主要情境而非硬性範圍，個別項目仍可使用平台其他已啟用的受控能力。";

/** 評分項目異動後，重新判斷目前環境能自動檢查到什麼程度。 */
export const RUBRIC_REASSESS_PROMPT =
  "請在不改變原始評分目標的前提下重新評估各項目的自動檢測支援狀態，更新檢測分類、檢測方式、缺少資訊、替代建議與評分計劃書。只有具備客觀判準、平台能力與完整執行資訊時才能標為能自動檢測；若缺少服務名稱、工作目錄、執行命令、Port 或成功條件，請明確向我詢問，不要猜測，也不要改成不同的檢查目標。將目前評分環境視為主要情境，個別項目仍可使用平台其他已啟用的受控能力。";

export function getTemplateLabel(templateKey) {
  return (
    TEMPLATE_OPTIONS.find((option) => option.key === templateKey)?.label ??
    i18n.t("aiJudge.linuxTemplateLabel", { ns: "services" })
  );
}

/** refine action 的內部指令仍保留在 session 歷史，但不在教師聊天室呈現。 */
export function shouldDisplayChatMessage(message) {
  const isKnownInternalPrompt = [RUBRIC_POLISH_PROMPT, RUBRIC_REASSESS_PROMPT].includes(
    message?.content,
  );
  return !message?.hidden && !message?.metadata_json?.ui_hidden && !isKnownInternalPrompt;
}

export const AiJudgeService = {
  /* ── 持久化檢查 Session ── */

  listSessions(classId) {
    return apiGet(`/api/v1/teaching-classes/${classId}/judge/sessions/`);
  },

  createSession(classId, {
    title,
    teachingClassWeekId = null,
    selectedFileId = null,
    creationMode,
    rubricName,
    environmentKeys,
  }) {
    const payload = {
      title,
      selected_file_id: selectedFileId,
    };
    if (teachingClassWeekId) payload.teaching_class_week_id = teachingClassWeekId;
    if (creationMode) payload.creation_mode = creationMode;
    if (rubricName !== undefined) payload.rubric_name = rubricName;
    if (environmentKeys !== undefined) payload.environment_keys = environmentKeys;
    return apiPost(`/api/v1/teaching-classes/${classId}/judge/sessions/`, payload);
  },

  createBlankSession(classId, {
    title = i18n.t("aiJudge.defaultBlankSessionTitle", { ns: "services" }),
    rubricName = i18n.t("aiJudge.defaultBlankRubricName", { ns: "services" }),
    environmentKeys = ["n8n"],
  } = {}) {
    return this.createSession(classId, {
      title,
      selectedFileId: null,
      creationMode: "blank",
      rubricName,
      environmentKeys,
    });
  },

  getSession(classId, sessionId) {
    return apiGet(`/api/v1/teaching-classes/${classId}/judge/sessions/${sessionId}`);
  },

  updateSession(classId, sessionId, changes) {
    return apiPatch(
      `/api/v1/teaching-classes/${classId}/judge/sessions/${sessionId}`,
      changes,
    );
  },

  forkSession(classId, sessionId, title = null) {
    return apiPost(
      `/api/v1/teaching-classes/${classId}/judge/sessions/${sessionId}/fork`,
      title ? { title } : {},
    );
  },

  deleteSession(classId, sessionId) {
    return apiDelete(
      `/api/v1/teaching-classes/${classId}/judge/sessions/${sessionId}`,
    );
  },

  listSessionMessages(classId, sessionId, before = null) {
    const query = before ? `?before=${encodeURIComponent(before)}` : "";
    return apiGet(
      `/api/v1/teaching-classes/${classId}/judge/sessions/${sessionId}/messages${query}`,
    );
  },

  clearSessionMessages(classId, sessionId) {
    return apiDelete(
      `/api/v1/teaching-classes/${classId}/judge/sessions/${sessionId}/messages`,
    );
  },

  sendSessionMessage(
    classId,
    sessionId,
    content,
    analysisRevision = null,
    { isRefine = false, attachmentIds = [] } = {},
  ) {
    const payload = { content };
    if (analysisRevision !== null && analysisRevision !== undefined) {
      payload.analysis_revision = analysisRevision;
    }
    if (isRefine) payload.is_refine = true;
    if (attachmentIds.length) payload.attachment_ids = attachmentIds;
    return apiPost(
      `/api/v1/teaching-classes/${classId}/judge/sessions/${sessionId}/messages`,
      payload,
      { timeoutMs: TEACHER_JUDGE_REQUEST_TIMEOUT_MS },
    );
  },

  uploadSessionAttachment(classId, sessionId, file) {
    const formData = new FormData();
    formData.append("file", file);
    return apiPostMultipart(
      `/api/v1/teaching-classes/${classId}/judge/sessions/${sessionId}/attachments`,
      formData,
      { timeoutMs: TEACHER_JUDGE_REQUEST_TIMEOUT_MS },
    );
  },

  deleteSessionAttachment(classId, sessionId, attachmentId) {
    return apiDelete(
      `/api/v1/teaching-classes/${classId}/judge/sessions/${sessionId}/attachments/${attachmentId}`,
    );
  },

  createSessionScript(classId, sessionId, analysisRevision = null) {
    const payload = {};
    if (analysisRevision !== null && analysisRevision !== undefined) {
      payload.analysis_revision = analysisRevision;
    }
    return apiPost(
      `/api/v1/teaching-classes/${classId}/judge/sessions/${sessionId}/scripts`,
      payload,
      { timeoutMs: SCRIPT_GENERATION_TIMEOUT_MS },
    );
  },

  listSessionRuns(classId, sessionId) {
    return apiGet(
      `/api/v1/teaching-classes/${classId}/judge/sessions/${sessionId}/runs`,
    );
  },

  getSessionRun(classId, sessionId, runId) {
    return apiGet(
      `/api/v1/teaching-classes/${classId}/judge/sessions/${sessionId}/runs/${runId}`,
    );
  },

  createSessionRun(classId, sessionId, scriptId, targetVmids) {
    return apiPost(
      `/api/v1/teaching-classes/${classId}/judge/sessions/${sessionId}/scripts/${scriptId}/runs`,
      { target_scope: "manual", target_vmids: targetVmids },
    );
  },

  /* ── 檢查表文件 ── */

  /** 列出班級已保存的檢查表 */
  listFiles(classId) {
    return apiGet(`/api/v1/teaching-classes/${classId}/judge/files/`);
  },

  /** 更新已保存檢查表的分析結果（項目編輯後持久化） */
  updateFileAnalysis(classId, fileId, analysis, expectedRevision = null) {
    const payload = { analysis };
    if (expectedRevision !== null && expectedRevision !== undefined) {
      payload.expected_revision = expectedRevision;
    }
    return apiPatch(
      `/api/v1/teaching-classes/${classId}/judge/files/${fileId}/analysis`,
      payload,
    );
  },

  /** 下載檢查表原始檔 */
  downloadFile(classId, fileId) {
    return apiGetBlob(`/api/v1/teaching-classes/${classId}/judge/files/${fileId}/download`);
  },

  /* ── 匯出 ── */

  /** 將評分項目匯出成 Excel（回傳 Blob） */
  downloadExcel(items, summary) {
    return apiPostBlob("/api/v1/rubric/download-excel", { items, summary });
  },

  /* ── 收集腳本 ── */

  /** 列出班級收集腳本 */
  listScripts(classId, sessionId = null) {
    const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
    return apiGet(`/api/v1/teaching-classes/${classId}/judge/scripts/${query}`);
  },

  /** 由檢查表快照產生受管收集腳本（後端會接著跑 policy 與 AI 審查） */
  createScript(classId, { name, templateKey, rubricSnapshot, sourceFileId = null }) {
    return apiPost(
      `/api/v1/teaching-classes/${classId}/judge/scripts/`,
      {
        name,
        template_key: templateKey,
        rubric_snapshot: rubricSnapshot,
        source_file_id: sourceFileId,
      },
      { timeoutMs: SCRIPT_GENERATION_TIMEOUT_MS },
    );
  },

  /** 重新生成腳本（可帶新的 rubric 快照） */
  regenerateScript(classId, scriptId, rubricSnapshot = null) {
    return apiPost(
      `/api/v1/teaching-classes/${classId}/judge/scripts/${scriptId}/regenerate`,
      { rubric_snapshot: rubricSnapshot },
      { timeoutMs: SCRIPT_GENERATION_TIMEOUT_MS },
    );
  },

  /** 相容舊版待老師核准腳本；新流程通過靜態與 AI 檢查後會直接 approved。 */
  approveScript(classId, scriptId) {
    return apiPost(`/api/v1/teaching-classes/${classId}/judge/scripts/${scriptId}/approve`, {});
  },

  /** 刪除腳本 */
  deleteScript(classId, scriptId) {
    return apiDelete(`/api/v1/teaching-classes/${classId}/judge/scripts/${scriptId}`);
  },

  /** 重新命名腳本 */
  renameScript(classId, scriptId, name) {
    return apiPatch(`/api/v1/teaching-classes/${classId}/judge/scripts/${scriptId}`, {
      name,
    });
  },

  /* ── 腳本執行 ── */

  /** 對指定 VMID 建立腳本執行任務 */
  createScriptRun(classId, scriptId, targetVmids) {
    return apiPost(`/api/v1/teaching-classes/${classId}/judge/scripts/${scriptId}/runs`, {
      target_scope: "manual",
      target_vmids: targetVmids,
    });
  },

  /** 查詢執行任務進度與結果（前端輪詢用） */
  getScriptRun(classId, scriptId, runId) {
    return apiGet(
      `/api/v1/teaching-classes/${classId}/judge/scripts/${scriptId}/runs/${runId}`,
    );
  },
};
