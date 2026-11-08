import { apiDelete, apiGet, apiPatch, apiPost } from "./api";

/** 與後端 UserFeedbackCreate 的 min_length / max_length 對齊 */
export const FEEDBACK_CONTENT_MIN = 5;
export const FEEDBACK_CONTENT_MAX = 2000;

/** 回饋類型；文案 key 在 common 命名空間（帳號設定與管理員頁共用） */
export const FEEDBACK_CATEGORIES = Object.freeze([
  { value: "bug", icon: "bug_report", labelKey: "Feedback.categoryBug", color: "danger" },
  { value: "suggestion", icon: "lightbulb", labelKey: "Feedback.categorySuggestion", color: "info" },
  { value: "other", icon: "chat_bubble", labelKey: "Feedback.categoryOther", color: "muted" },
]);

export function feedbackCategoryMeta(value) {
  return FEEDBACK_CATEGORIES.find((c) => c.value === value) ?? FEEDBACK_CATEGORIES[2];
}

/**
 * 使用者回饋：任何登入者從「帳號設定 › 關於」送出；管理員在「使用者回饋」頁
 * 看清單、標記已處理（可改回）或刪除。
 */
export const FeedbackService = {
  /** 送出（body: { category, content, app_version? }）；後端回 201 */
  submit(body) {
    return apiPost("/api/v1/feedback/", body);
  },

  /** 管理員清單（新到舊）；params: { skip, limit, status?, category? } */
  listAll(params = {}) {
    const query = new URLSearchParams();
    query.set("skip", String(params.skip ?? 0));
    query.set("limit", String(params.limit ?? 50));
    if (params.status) query.set("status", params.status);
    if (params.category) query.set("category", params.category);
    return apiGet(`/api/v1/feedback/?${query.toString()}`);
  },

  /** 管理員改處理狀態："open" | "resolved" */
  setStatus(feedbackId, status) {
    return apiPatch(`/api/v1/feedback/${feedbackId}`, { status });
  },

  /** 管理員刪除（204） */
  remove(feedbackId) {
    return apiDelete(`/api/v1/feedback/${feedbackId}`);
  },
};
