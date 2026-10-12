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
 *
 * 純日期字串（YYYY-MM-DD，如 expiry_date）沒有時區，一律照字面的年月日顯示；
 * 交給 new Date() 會被當成 UTC 午夜，負時區的使用者會看到前一天。
 */
import i18n from "../i18n";

const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;

const isDateOnly = (value) => typeof value === "string" && DATE_ONLY.test(value);

function toValidDate(value) {
  if (value == null || value === "") return null;
  if (isDateOnly(value)) {
    // 純日期在本地時區建構當天 00:00；02-31 這類不存在的日期會進位到下個月，當成無效
    const [year, month, day] = DATE_ONLY.exec(value).slice(1).map(Number);
    const date = new Date(year, month - 1, day);
    return date.getMonth() === month - 1 && date.getDate() === day ? date : null;
  }
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

/**
 * 機器到期日（expiry_date，純日期）：日期照字面顯示，另附本地時間的實際截止時刻。
 * 後端以「到期日 00:00 UTC」為界（lifecycle_policy.expiry_datetime），台灣看到的是 08:00；
 * 負時區的截止時刻落在前一天，這時連月日一起標，免得以為當天還能用。
 * 回傳 { date, cutoff, title, text }：cutoff 是精簡標示（「08:00 截止」），title 是完整說明，
 * text 是單行合併版；不是純日期字串時只回 date（其餘為 null），空值或無效日期回 null。
 */
export function formatExpiryDate(value) {
  const day = toValidDate(value);
  if (!day) return null;
  const date = formatDate(day);
  if (!isDateOnly(value)) return { date, cutoff: null, title: null, text: date };
  const cutoffAt = new Date(Date.UTC(day.getFullYear(), day.getMonth(), day.getDate()));
  const sameDay = cutoffAt.getFullYear() === day.getFullYear()
    && cutoffAt.getMonth() === day.getMonth()
    && cutoffAt.getDate() === day.getDate();
  const time = sameDay ? formatTime(cutoffAt) : formatShortDateTime(cutoffAt);
  const cutoff = i18n.t("ExpiryDate.cutoff", { ns: "common", time });
  return {
    date,
    cutoff,
    title: i18n.t("ExpiryDate.cutoffTitle", {
      ns: "common",
      datetime: format(cutoffAt, "", {
        year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
      }),
    }),
    text: i18n.t("ExpiryDate.inline", { ns: "common", date, cutoff }),
  };
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
