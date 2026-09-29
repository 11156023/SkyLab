import { apiDelete, apiGet, apiPost } from "./api";

/** 虛擬教室：session 管理走 REST，信令與觀看資料面走 WS（見 ClassroomStudentLayer / WatchDialog）。 */
export const ClassroomService = {
  listClassStudents(classId) {
    return apiGet(`/api/v1/classroom/classes/${classId}/students`);
  },

  listClassBroadcastSources(classId) {
    return apiGet(`/api/v1/classroom/classes/${classId}/broadcast-sources`);
  },

  /** 開啟正式班級 session。 */
  createSession({ vmid, mode, class_id }) {
    return apiPost("/api/v1/classroom/sessions", { vmid, mode, class_id });
  },

  /** 結束 session */
  stopSession(sessionId) {
    return apiDelete(`/api/v1/classroom/sessions/${sessionId}`);
  },

  /** 控制權：action = "take" | "release" */
  setControl(sessionId, action) {
    return apiPost(`/api/v1/classroom/sessions/${sessionId}/control`, { action });
  },

  /** 進行中的 session 列表（教師/管理員） */
  listSessions() {
    return apiGet("/api/v1/classroom/sessions");
  },

  /**
   * 目前對自己生效的廣播（學生輪詢用）。
   * 回傳 { session, taken_over_vmids }：taken_over_vmids 是自己的機器中目前正被老師接管的 vmid，
   * 重連後用來重建「老師接管中」覆蓋。
   */
  getLive() {
    return apiGet("/api/v1/classroom/live");
  },
};
