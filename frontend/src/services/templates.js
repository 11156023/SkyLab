import {
  apiDelete,
  apiGet,
  apiGetBlob,
  apiPatch,
  apiPost,
  apiPostMultipart,
} from "./api";

export const TemplatesService = {
  /** 列出可見範本（admin 全部；teacher 自有+可見；student 僅 ready 且可見） */
  list(options) {
    return apiGet("/api/v1/templates/", options);
  },

  /** 學生申請表單的應用範本目錄：全部可見且 ready 的範本（任何登入者） */
  catalog(options) {
    return apiGet("/api/v1/templates/catalog", options);
  },

  /** 把現有 VM/LXC 轉為範本（背景任務） */
  create(body) {
    return apiPost("/api/v1/templates/", body);
  },

  /** 更新範本 metadata / 可見範圍 */
  update(templateId, body) {
    return apiPatch(`/api/v1/templates/${templateId}`, body);
  },

  /** 重新送出失敗的母機轉換任務 */
  retry(templateId) {
    return apiPost(`/api/v1/templates/${templateId}/retry`, {});
  },

  /** 刪除範本（仍有 linked clone 子機時後端回 409） */
  remove(templateId) {
    return apiDelete(`/api/v1/templates/${templateId}`);
  },

  /** 從範本克隆開通（student 單台；teacher/admin 可批量） */
  clone(templateId, body) {
    return apiPost(`/api/v1/templates/${templateId}/clone`, body);
  },

  /** 列出範本附件（使用手冊等） */
  listAttachments(templateId) {
    return apiGet(`/api/v1/templates/${templateId}/attachments`);
  },

  /** 上傳範本附件（擁有者或 admin，50MB 內） */
  uploadAttachment(templateId, file) {
    const form = new FormData();
    form.append("file", file);
    return apiPostMultipart(`/api/v1/templates/${templateId}/attachments`, form);
  },

  /** 刪除範本附件 */
  removeAttachment(templateId, attachmentId) {
    return apiDelete(
      `/api/v1/templates/${templateId}/attachments/${attachmentId}`,
    );
  },

  /** 下載附件（回傳 Blob，配 downloadBlob 使用） */
  downloadAttachment(templateId, attachmentId) {
    return apiGetBlob(
      `/api/v1/templates/${templateId}/attachments/${attachmentId}/download`,
    );
  },

  /** 更新循環：克隆暫存母機 / 轉為新版 / 取消 */
  startUpdateCycle(templateId) {
    return apiPost(`/api/v1/templates/${templateId}/update-cycle/start`, {});
  },
  finishUpdateCycle(templateId) {
    return apiPost(`/api/v1/templates/${templateId}/update-cycle/finish`, {});
  },
  cancelUpdateCycle(templateId) {
    return apiPost(`/api/v1/templates/${templateId}/update-cycle/cancel`, {});
  },
};
