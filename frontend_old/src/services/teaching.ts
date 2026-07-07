import type {
  BatchSpecAccepted,
  BatchSpecRequest,
  BatchSpecStatusPublic,
  CancelablePromise,
  ConfigPushAccepted,
  ConfigPushStatusPublic,
  HeatmapEntry,
  PairSessionPublic,
} from "@/client"
import { OpenAPI } from "@/client"
import { request as __request } from "@/client/core/request"

const BASE = "/api/v1/teaching"
const PAIR_BASE = "/api/v1/pair-sessions"

export const TeachingAPI = {
  getHeatmap(groupId: string): CancelablePromise<HeatmapEntry[]> {
    return __request(OpenAPI, {
      method: "GET",
      url: `${BASE}/heatmap`,
      query: { group_id: groupId },
    })
  },

  startConfigPush(opts: {
    file: File
    targetPath: string
    vmids: number[]
  }): CancelablePromise<ConfigPushAccepted> {
    return __request(OpenAPI, {
      method: "POST",
      url: `${BASE}/config-push`,
      formData: {
        file: opts.file,
        target_path: opts.targetPath,
        vmids: opts.vmids,
      },
    })
  },

  getConfigPushStatus(
    taskId: string,
  ): CancelablePromise<ConfigPushStatusPublic> {
    return __request(OpenAPI, {
      method: "GET",
      url: `${BASE}/config-push/{task_id}`,
      path: { task_id: taskId },
    })
  },

  startBatchSpec(body: BatchSpecRequest): CancelablePromise<BatchSpecAccepted> {
    return __request(OpenAPI, {
      method: "POST",
      url: `${BASE}/batch-spec`,
      body,
      mediaType: "application/json",
    })
  },

  getBatchSpecStatus(taskId: string): CancelablePromise<BatchSpecStatusPublic> {
    return __request(OpenAPI, {
      method: "GET",
      url: `${BASE}/batch-spec/{task_id}`,
      path: { task_id: taskId },
    })
  },
}

export const PairAPI = {
  create(body: {
    vmid: number
    invitee_user_id: string
  }): CancelablePromise<PairSessionPublic> {
    return __request(OpenAPI, {
      method: "POST",
      url: PAIR_BASE,
      body,
      mediaType: "application/json",
    })
  },

  mine(): CancelablePromise<PairSessionPublic[]> {
    return __request(OpenAPI, {
      method: "GET",
      url: `${PAIR_BASE}/mine`,
    })
  },

  end(sessionId: string): CancelablePromise<{ message: string }> {
    return __request(OpenAPI, {
      method: "DELETE",
      url: `${PAIR_BASE}/{session_id}`,
      path: { session_id: sessionId },
    })
  },
}
