import type {
  CancelablePromise,
  ClassroomControlRequest,
  ClassroomLivePublic,
  ClassroomSessionCreate,
  ClassroomSessionPublic,
  ClassroomStudent,
} from "@/client"
import { OpenAPI } from "@/client"
import { request as __request } from "@/client/core/request"

const BASE = "/api/v1/classroom"

export const ClassroomAPI = {
  listStudents(groupId: string): CancelablePromise<ClassroomStudent[]> {
    return __request(OpenAPI, {
      method: "GET",
      url: `${BASE}/groups/{group_id}/students`,
      path: { group_id: groupId },
    })
  },

  listSessions(): CancelablePromise<ClassroomSessionPublic[]> {
    return __request(OpenAPI, { method: "GET", url: `${BASE}/sessions` })
  },

  createSession(
    body: ClassroomSessionCreate,
  ): CancelablePromise<ClassroomSessionPublic> {
    return __request(OpenAPI, {
      method: "POST",
      url: `${BASE}/sessions`,
      body,
      mediaType: "application/json",
    })
  },

  stopSession(sessionId: string): CancelablePromise<{ message: string }> {
    return __request(OpenAPI, {
      method: "DELETE",
      url: `${BASE}/sessions/{session_id}`,
      path: { session_id: sessionId },
    })
  },

  setControl(
    sessionId: string,
    body: ClassroomControlRequest,
  ): CancelablePromise<ClassroomSessionPublic> {
    return __request(OpenAPI, {
      method: "POST",
      url: `${BASE}/sessions/{session_id}/control`,
      path: { session_id: sessionId },
      body,
      mediaType: "application/json",
    })
  },

  getLive(): CancelablePromise<ClassroomLivePublic> {
    return __request(OpenAPI, { method: "GET", url: `${BASE}/live` })
  },
}
