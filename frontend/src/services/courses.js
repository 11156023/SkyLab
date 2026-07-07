import { apiDelete, apiGet, apiPost, apiPut } from "./api";

/**
 * 課程實驗室（Course Lab）：
 * - CoursesService：學生端 — 路徑瀏覽、房間內容、秒開部署、Flag 提交
 * - CourseAdminService：老師/管理員端 — 路徑/房間/任務/題目 CRUD、發布、進度監控
 * 欄位見後端 app/schemas/course.py。
 */
export const CoursesService = {
  /** 已發布路徑清單（含我的進度 %） */
  listPaths() {
    return apiGet("/api/v1/courses/paths");
  },

  /** 路徑詳情：房間清單 + 進度 */
  getPath(pathId) {
    return apiGet(`/api/v1/courses/paths/${pathId}`);
  },

  /** 房間詳情：任務 + 題目（不含答案）+ 我的部署狀態 */
  getRoom(roomId) {
    return apiGet(`/api/v1/courses/rooms/${roomId}`);
  },

  /** 秒開部署（202；之後以 getDeployment 輪詢） */
  deployRoom(roomId) {
    return apiPost(`/api/v1/courses/rooms/${roomId}/deploy`, {});
  },

  /** 部署狀態輪詢（provisioning / running / failed / expired） */
  getDeployment(deploymentId) {
    return apiGet(`/api/v1/courses/deployments/${deploymentId}`);
  },

  /** 提前歸還銷毀 */
  terminateDeployment(deploymentId) {
    return apiDelete(`/api/v1/courses/deployments/${deploymentId}`);
  },

  /** 提交答案（no_answer 題型 answer 可為 null） */
  submitAnswer(questionId, answer) {
    return apiPost(`/api/v1/courses/questions/${questionId}/submit`, { answer });
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
