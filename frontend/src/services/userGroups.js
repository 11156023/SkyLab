import { apiDelete, apiGet, apiPatch, apiPost, apiPostMultipart } from "./api";

const BASE = "/api/v1/user-groups";

/* 管理員維護的使用者群組（例如一個班）；老師只會用 list 挑群組匯入班級 */
export const UserGroupsService = {
  list() { return apiGet(`${BASE}/`); },
  create(name) { return apiPost(`${BASE}/`, { name }); },
  rename(groupId, name) { return apiPatch(`${BASE}/${groupId}`, { name }); },
  delete(groupId) { return apiDelete(`${BASE}/${groupId}`); },
  members(groupId) { return apiGet(`${BASE}/${groupId}/members`); },
  addMembers(groupId, { userIds = [], emails = [] } = {}) {
    return apiPost(`${BASE}/${groupId}/members`, { user_ids: userIds, emails });
  },
  removeMembers(groupId, userIds) {
    return apiPost(`${BASE}/${groupId}/members/remove`, { user_ids: userIds });
  },
  importMembers(groupId, file) {
    const body = new FormData();
    body.append("file", file);
    return apiPostMultipart(`${BASE}/${groupId}/members/import-csv`, body);
  },
};
