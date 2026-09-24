import { apiDelete, apiGet, apiPatch, apiPost } from "./api";

const BASE = "/api/v1/users";

const PAGE_SIZE = 200;
const MAX_PAGES = 25; // 上限保護：最多載入 5000 位使用者

export const UsersService = {
  list({ skip = 0, limit = 100 } = {}) {
    return apiGet(`${BASE}/?skip=${skip}&limit=${limit}`);
  },

  /** 逐頁取回全部使用者（供前端搜尋／選單用，避免只拿到第一頁）。 */
  async listAll() {
    const all = [];
    for (let page = 0; page < MAX_PAGES; page += 1) {
      const res = await UsersService.list({
        skip: page * PAGE_SIZE,
        limit: PAGE_SIZE,
      });
      const batch = res?.data ?? [];
      all.push(...batch);
      if (batch.length < PAGE_SIZE) break;
      if (typeof res?.count === "number" && all.length >= res.count) break;
    }
    return all;
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

  /** 管理員解除某使用者的兩步驟驗證（手機遺失救援；對方需重新登入） */
  resetTotp(userId) {
    return apiDelete(`${BASE}/${userId}/totp`);
  },
};
