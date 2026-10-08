import {
  apiDelete,
  apiGet,
  apiGetBlob,
  apiPatch,
  apiPost,
  apiPostMultipart,
} from "./api";
import i18n from "../i18n";

// 腳本編譯使用獨立的等待上限；前置 AI 重新核對走 sendSessionMessage 的整輪期限。
const SCRIPT_GENERATION_TIMEOUT_MS = 7 * 60 * 1000;
// 整輪 backend 570 秒、瀏覽器 600 秒、Teacher Judge nginx 610 秒。
// 單次模型呼叫仍由 system-ai.json 的 vllm.timeout（120 秒）限制。
export const TEACHER_JUDGE_REQUEST_TIMEOUT_MS = 600 * 1000;

/** 評分環境模板選項 */
export const TEMPLATE_OPTIONS = [
  { key: "n8n", label: "n8n" },
  { key: "python", label: "Python" },
  { key: "postgresql", label: "PostgreSQL" },
  { key: "linux", label: i18n.t("aiJudge.linuxTemplateLabel", { ns: "services" }) },
];

const EXECUTION_LOCATION_INSTRUCTION =
  "工作目錄不是通用必填：版本、服務狀態、CPU 查詢可省略；讀取或執行檔案時，只要完整檔案路徑，或明確的 collector.cwd 與相對路徑即可。已知目錄須寫入對應步驟的 collector，不能只放在描述。不需要目錄時省略 cwd 或填 null；下述缺少資訊只適用於該項目真正必要的資訊。";

/** 正式工作區與獨立編輯頁共用的整表潤飾動作。 */
export const RUBRIC_POLISH_PROMPT =
  EXECUTION_LOCATION_INSTRUCTION +
  "請在不改變原始評分目標的前提下，重新核對目前完整檢查表。這次是儲存並製作腳本前的 Finalizer，與 Chat、附件提案共用同一 typed 契約：只透過工具送出需要變更的項目，完整 candidate 由後端套回目前檢查表；不要在 reply 重複整份項目列表。若既有可執行項目仍是 legacy flat/template check_steps，必須一併送出該項目的完整 typed 轉換；只有已是有效 typed steps 且內容未變動的項目可以省略。每個送出的 check_steps 都必須是完整 typed collector/assertion 陣列（每步含唯一穩定 id、title、collector；ai 項目要有 assertion，teacher 項目省略 assertion）。collector 與 assertion 的類型欄位都是 type，預期值欄位是 expected；不要使用 collector_type、assertion_type、expected_value、flat argv、command_key 或輸出 Python。工具失敗時依 issues 與 repair_hint 修正，只在 retryable=true 時重試；不得省略失敗步驟或承諾背景重試。將自動檢測支援狀態判定為 auto、partial 或 manual，只有平台能安全取得證據且執行資訊完整時才標為 auto；缺少服務名稱、工作目錄、執行命令、Port 或資料範圍時標為 partial 並列出缺口；manual 項目要填寫 fallback，不要猜測或改變檢查目標。將目前評分環境視為主要情境，個別項目仍可使用其他已啟用的受控能力。";

/** 評分項目異動後，重新判斷目前環境能自動檢查到什麼程度。 */
export const RUBRIC_REASSESS_PROMPT =
  EXECUTION_LOCATION_INSTRUCTION +
  "請在不改變原始評分目標的前提下重新評估各項目的自動檢測支援狀態，更新檢測分類、檢測方式、缺少資訊、替代建議與評分計劃書。只有平台能安全取得證據且執行資訊完整時才能標為能自動檢測；若缺少服務名稱、工作目錄、執行命令、Port 或資料範圍，請只詢問真正缺少的內容，不要猜測或改變檢查目標。將目前評分環境視為主要情境，個別項目仍可使用其他已啟用的受控能力。";

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
    teachingClassWeekId = null,
  } = {}) {
    return this.createSession(classId, {
      title,
      teachingClassWeekId,
      selectedFileId: null,
      creationMode: "blank",
      rubricName,
      environmentKeys,
    });
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

  dismissSessionProposal(classId, sessionId, messageId) {
    return apiDelete(
      `/api/v1/teaching-classes/${classId}/judge/sessions/${sessionId}/messages/${messageId}/proposal`,
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
    ).catch((error) => {
      if (error?.timeout) {
        throw { ...error, message: i18n.t("aiJudge.waitTimeout", { ns: "services" }) };
      }
      throw error;
    });
  },

  uploadSessionAttachment(classId, sessionId, file) {
    const formData = new FormData();
    formData.append("file", file);
    return apiPostMultipart(
      `/api/v1/teaching-classes/${classId}/judge/sessions/${sessionId}/attachments`,
      formData,
      { timeoutMs: 120 * 1000 },
    );
  },

  deleteSessionAttachment(classId, sessionId, attachmentId) {
    return apiDelete(
      `/api/v1/teaching-classes/${classId}/judge/sessions/${sessionId}/attachments/${attachmentId}`,
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

  updateTargetReview(classId, sessionId, runId, vmid, { feedback = "", decisions = {} }) {
    return apiPatch(
      `/api/v1/teaching-classes/${classId}/judge/sessions/${sessionId}/runs/${runId}/targets/${vmid}/review`,
      { feedback, decisions },
    );
  },

  updateStudentReview(classId, sessionId, runId, studentId, { feedback = "", decisions = {} }) {
    return apiPatch(
      `/api/v1/teaching-classes/${classId}/judge/sessions/${sessionId}/runs/${runId}/students/${encodeURIComponent(studentId)}/review`,
      { feedback, decisions },
    );
  },

  /* ── 檢查點腳本集（多機器整批執行） ── */

  /** 以目前已確認的 rubric 建立一組 deterministic child artifacts。 */
  createSessionScriptSet(classId, sessionId, analysisRevision = null) {
    const payload = {};
    if (analysisRevision !== null && analysisRevision !== undefined) {
      payload.analysis_revision = analysisRevision;
    }
    return apiPost(
      `/api/v1/teaching-classes/${classId}/judge/sessions/${sessionId}/script-sets`,
      payload,
      { timeoutMs: SCRIPT_GENERATION_TIMEOUT_MS },
    );
  },

  /** 列出 session 的檢查點腳本集（每個邏輯機器一份 child artifact） */
  listSessionScriptSets(classId, sessionId) {
    return apiGet(
      `/api/v1/teaching-classes/${classId}/judge/sessions/${sessionId}/script-sets`,
    );
  },

  /** 對腳本集建立整批執行：每份 child artifact 分散到該節點的每台學生機器 */
  createSessionScriptSetRun(classId, sessionId, artifactSetId) {
    return apiPost(
      `/api/v1/teaching-classes/${classId}/judge/sessions/${sessionId}/script-sets/${artifactSetId}/runs`,
      { target_scope: "all_students_in_set" },
    );
  },

  /** 查詢整批執行進度與逐機器檢查點投影（前端輪詢用） */
  getSessionRunBatch(classId, sessionId, runBatchId) {
    return apiGet(
      `/api/v1/teaching-classes/${classId}/judge/sessions/${sessionId}/run-batches/${runBatchId}`,
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

  /* ── 收集腳本 ── */

  /** 列出班級收集腳本 */
  listScripts(classId, sessionId = null) {
    const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
    return apiGet(`/api/v1/teaching-classes/${classId}/judge/scripts/${query}`);
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
};
