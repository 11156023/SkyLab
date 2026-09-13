/**
 * utils/formatDate.js
 * i18n 感知的日期時間格式化，取代散落各頁的
 * `new Date(x).toLocaleString("zh-TW")`——寫死語系在英／日介面下仍會顯示台灣格式。
 *
 * 語系代碼（zh-TW / en / ja）本身就是合法的 BCP 47 locale，直接餵給 Intl。
 * 於呼叫當下讀 i18n.language；語系切換會觸發 re-render，重新呼叫即得新格式。
 *
 * 全站統一 24 小時制與 2-digit 補零；value 可為 ISO 字串、timestamp 或 Date，
 * 空值或無效日期回傳 fallback（預設 "—"）。
 */
import i18n from "../i18n";

function toValidDate(value) {
  if (value == null || value === "") return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function format(value, fallback, options) {
  const date = toValidDate(value);
  if (!date) return fallback;
  return new Intl.DateTimeFormat(i18n.language, options).format(date);
}

/** 完整日期時間（如 2026/09/10 14:30:15），稽核紀錄、任務詳情等需要精確時間處用 */
export function formatDateTime(value, fallback = "—") {
  return format(value, fallback, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

/** 精簡日期時間（如 09/10 14:30），狹窄的列表欄位與 meta 行用 */
export function formatShortDateTime(value, fallback = "—") {
  return format(value, fallback, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

/** 只有日期（如 2026/09/10） */
export function formatDate(value, fallback = "—") {
  return format(value, fallback, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

/** 只有月日（如 9/10），儀表板的緊湊日期欄用 */
export function formatMonthDay(value, fallback = "—") {
  return format(value, fallback, { month: "numeric", day: "numeric" });
}

/** 只有時間（如 14:30；seconds: true 時 14:30:15），圖表刻度與「最後更新」用 */
export function formatTime(value, fallback = "—", { seconds = false } = {}) {
  return format(value, fallback, {
    hour: "2-digit",
    minute: "2-digit",
    ...(seconds ? { second: "2-digit" } : {}),
    hour12: false,
  });
}
