import { apiGet, apiPost, apiDelete, apiPostMultipart } from "./api";

export const GroupsService = {
  /** 列出目前使用者可管理的群組 */
  list() {
    return apiGet("/api/v1/groups/");
  },

  /** 建立群組 */
  create(body) {
    return apiPost("/api/v1/groups/", body);
  },

  /** 群組詳情（含成員列表與 VM 狀態） */
  detail(groupId) {
    return apiGet(`/api/v1/groups/${groupId}`);
  },

  /** 刪除群組 */
  remove(groupId) {
    return apiDelete(`/api/v1/groups/${groupId}`);
  },

  /** 以 email 列表加入成員 */
  addMembers(groupId, emails) {
    return apiPost(`/api/v1/groups/${groupId}/members`, { emails });
  },

  /** 移除單一成員 */
  removeMember(groupId, userId) {
    return apiDelete(`/api/v1/groups/${groupId}/members/${userId}`);
  },

  /** 從 CSV 大量匯入學生（不存在的帳號會自動建立） */
  importCsv(groupId, file) {
    const formData = new FormData();
    formData.append("file", file);
    return apiPostMultipart(`/api/v1/groups/${groupId}/import-csv`, formData);
  },
};
