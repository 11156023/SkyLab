import { apiDelete, apiGet, apiPatch, apiPost } from "./api";

const BASE = "/api/v1/users";

export const UsersService = {
  list({ skip = 0, limit = 100 } = {}) {
    return apiGet(`${BASE}/?skip=${skip}&limit=${limit}`);
  },

  create(payload) {
    return apiPost(`${BASE}/`, payload);
  },

  update(userId, payload) {
    return apiPatch(`${BASE}/${userId}`, payload);
  },

  delete(userId) {
    return apiDelete(`${BASE}/${userId}`);
  },
};
