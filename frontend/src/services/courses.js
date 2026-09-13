import { apiDelete, apiGet, apiGetBlob, apiPost, apiPut } from "./api";

/**
 * 課程服務：
 * - CoursesService：學生端 — 正式課程總覽、每週任務、PDF 與完成狀態
 * - CourseAdminService：老師/管理員端 — 路徑/房間/任務/題目 CRUD、發布、進度監控
 * 欄位見後端 app/schemas/course.py。
 */
export const CoursesService = {
  /** 今天實際有排課、且學生已加入的課程。 */
  listSchedule() {
    return apiGet("/api/v1/courses/schedule");
  },

  /** 由資源期限、申請審核與近期課堂任務產生的提醒。 */
  listReminders() {
    return apiGet("/api/v1/courses/reminders");
  },

  /** 已發布路徑清單（含我的進度 %） */
  listPaths() {
    return apiGet("/api/v1/courses/paths");
  },

  /** 路徑詳情：房間清單 + 進度 */
  getPath(pathId) {
    return apiGet(`/api/v1/courses/paths/${pathId}`);
  },

  /** 取得老師已核准、可讓學生查看的 AI 評分任務。 */
  getAiAssignments(pathId) {
    return apiGet(`/api/v1/courses/paths/${pathId}/ai-assignments`);
  },

  /** 取得老師在班級每週內容中正式發布的任務與 PDF。 */
  getWeeklyTasks(pathId) {
    return apiGet(`/api/v1/courses/paths/${pathId}/weekly-tasks`);
  },

  /** 透過有課程身分驗證的端點預覽每週任務 PDF。 */
  getWeeklyTaskDocument(pathId, weekId, fileId) {
    return apiGetBlob(
      `/api/v1/courses/paths/${pathId}/weekly-tasks/${weekId}/files/${fileId}`,
    );
  },

  /** 取得老師上傳、且與已核准任務相連的 PDF。 */
  getAiAssignmentDocument(pathId, assignmentId) {
    return apiGetBlob(
      `/api/v1/courses/paths/${pathId}/ai-assignments/${assignmentId}/source-document`,
    );
  },

  /** 取得學生在此課程由班級流程分配的所有練習機器。 */
  getPracticeMachines(pathId) {
    return apiGet(`/api/v1/courses/paths/${pathId}/practice-machines`);
  },

  /** 學生以每週／整份任務為單位回報完成；AI 檢查由老師統一啟動。 */
  updateAssignmentCompletion(pathId, assignmentId, completed) {
    return apiPut(
      `/api/v1/courses/paths/${pathId}/ai-assignments/${assignmentId}/completion`,
      { completed },
    );
  },

  /** 房間詳情：任務 + 題目（不含答案）+ 我的部署狀態 */
  getRoom(roomId) {
    return apiGet(`/api/v1/courses/rooms/${roomId}`);
  },

};

export const CourseAdminService = {
  // ── 路徑 ──
  listPaths() {
    return apiGet("/api/v1/admin/courses/paths");
  },
  createPath(body) {
    return apiPost("/api/v1/admin/courses/paths", body);
  },
  updatePath(pathId, body) {
    return apiPut(`/api/v1/admin/courses/paths/${pathId}`, body);
  },
  publishPath(pathId, published) {
    return apiPut(`/api/v1/admin/courses/paths/${pathId}/publish`, { published });
  },
  deletePath(pathId) {
    return apiDelete(`/api/v1/admin/courses/paths/${pathId}`);
  },
  getPathProgress(pathId) {
    return apiGet(`/api/v1/admin/courses/paths/${pathId}/progress`);
  },

  // ── 房間 ──
  listRooms(pathId) {
    return apiGet(`/api/v1/admin/courses/paths/${pathId}/rooms`);
  },
  createRoom(body) {
    return apiPost("/api/v1/admin/courses/rooms", body);
  },
  updateRoom(roomId, body) {
    return apiPut(`/api/v1/admin/courses/rooms/${roomId}`, body);
  },
  deleteRoom(roomId) {
    return apiDelete(`/api/v1/admin/courses/rooms/${roomId}`);
  },

  // ── 任務 ──
  listTasks(roomId) {
    return apiGet(`/api/v1/admin/courses/rooms/${roomId}/tasks`);
  },
  createTask(body) {
    return apiPost("/api/v1/admin/courses/tasks", body);
  },
  updateTask(taskId, body) {
    return apiPut(`/api/v1/admin/courses/tasks/${taskId}`, body);
  },
  deleteTask(taskId) {
    return apiDelete(`/api/v1/admin/courses/tasks/${taskId}`);
  },

  // ── 題目（flag 明文只出現在請求 body，回應不含） ──
  listQuestions(taskId) {
    return apiGet(`/api/v1/admin/courses/tasks/${taskId}/questions`);
  },
  createQuestion(body) {
    return apiPost("/api/v1/admin/courses/questions", body);
  },
  updateQuestion(questionId, body) {
    return apiPut(`/api/v1/admin/courses/questions/${questionId}`, body);
  },
  deleteQuestion(questionId) {
    return apiDelete(`/api/v1/admin/courses/questions/${questionId}`);
  },
};

/** 老師端進度即時推播 WebSocket URL（token 由呼叫端帶入） */
export function courseProgressWsUrl(pathId, token) {
  const apiUrl = new URL(
    import.meta.env.VITE_API_URL ||
      `${window.location.protocol}//${window.location.host}`
  );
  const proto = apiUrl.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${apiUrl.host}/ws/courses/paths/${pathId}/progress?token=${encodeURIComponent(token)}`;
}
