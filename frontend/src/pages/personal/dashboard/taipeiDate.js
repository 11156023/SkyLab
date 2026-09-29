/* 課程、課表與作業發布日都以台北時區的日曆日為準；儀表板各頁共用這一個換算。 */

const TAIPEI_DATE_FORMAT = new Intl.DateTimeFormat("en", {
  timeZone: "Asia/Taipei",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

/** 取台北時區的 YYYY-MM-DD（value 可為 Date 或 timestamp）。 */
export function taipeiDateKey(value) {
  const parts = TAIPEI_DATE_FORMAT.formatToParts(value);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}
