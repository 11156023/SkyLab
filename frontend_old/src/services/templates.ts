import type {
  CancelablePromise,
  TaskRecordPublic,
  TaskRecordsPublic,
  TemplateCloneRequest,
  TemplateCloneResponse,
  VmTemplateCreate,
  VmTemplatePublic,
  VmTemplatesPublic,
  VmTemplateTaskResponse,
  VmTemplateUpdate,
} from "@/client"
import { OpenAPI } from "@/client"
import { request as __request } from "@/client/core/request"

const BASE = "/api/v1/templates"

export const TemplatesAPI = {
  list(): CancelablePromise<VmTemplatesPublic> {
    return __request(OpenAPI, { method: "GET", url: `${BASE}/` })
  },

  get(templateId: string): CancelablePromise<VmTemplatePublic> {
    return __request(OpenAPI, {
      method: "GET",
      url: `${BASE}/{template_id}`,
      path: { template_id: templateId },
    })
  },

  create(body: VmTemplateCreate): CancelablePromise<VmTemplateTaskResponse> {
    return __request(OpenAPI, {
      method: "POST",
      url: `${BASE}/`,
      body,
      mediaType: "application/json",
    })
  },

  update(
    templateId: string,
    body: VmTemplateUpdate,
  ): CancelablePromise<VmTemplatePublic> {
    return __request(OpenAPI, {
      method: "PATCH",
      url: `${BASE}/{template_id}`,
      path: { template_id: templateId },
      body,
      mediaType: "application/json",
    })
  },

  remove(templateId: string): CancelablePromise<TaskRecordPublic> {
    return __request(OpenAPI, {
      method: "DELETE",
      url: `${BASE}/{template_id}`,
      path: { template_id: templateId },
    })
  },

  clone(
    templateId: string,
    body: TemplateCloneRequest,
  ): CancelablePromise<TemplateCloneResponse> {
    return __request(OpenAPI, {
      method: "POST",
      url: `${BASE}/{template_id}/clone`,
      path: { template_id: templateId },
      body,
      mediaType: "application/json",
    })
  },

  startUpdateCycle(templateId: string): CancelablePromise<TaskRecordPublic> {
    return __request(OpenAPI, {
      method: "POST",
      url: `${BASE}/{template_id}/update-cycle/start`,
      path: { template_id: templateId },
    })
  },

  finishUpdateCycle(templateId: string): CancelablePromise<TaskRecordPublic> {
    return __request(OpenAPI, {
      method: "POST",
      url: `${BASE}/{template_id}/update-cycle/finish`,
      path: { template_id: templateId },
    })
  },

  cancelUpdateCycle(templateId: string): CancelablePromise<TaskRecordPublic> {
    return __request(OpenAPI, {
      method: "POST",
      url: `${BASE}/{template_id}/update-cycle/cancel`,
      path: { template_id: templateId },
    })
  },

  listTasks(limit = 50): CancelablePromise<TaskRecordsPublic> {
    return __request(OpenAPI, {
      method: "GET",
      url: `${BASE}/tasks`,
      query: { limit },
    })
  },

  getTask(taskId: string): CancelablePromise<TaskRecordPublic> {
    return __request(OpenAPI, {
      method: "GET",
      url: `${BASE}/tasks/{task_id}`,
      path: { task_id: taskId },
    })
  },
}

export const TEMPLATE_TASK_LABEL: Record<string, string> = {
  "template.convert": "轉換範本",
  "template.delete": "刪除範本",
  "template.update_clone": "更新循環：建立暫存母機",
  "template.update_convert": "更新循環：轉換新版",
  "template.update_cancel": "更新循環：取消",
  "template.clone": "克隆開通",
}
