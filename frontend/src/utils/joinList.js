import i18n from "../i18n";

/**
 * 依目前語系串接清單項目（檔名、帳號等）：中文、日文用「、」，英文用「, 」。
 * 分隔符號放在 common 語系檔的 ListFormat.separator，新增語系時一併補上。
 * 不用 Intl.ListFormat：它的中文會在最後一項前加「和」，不是介面慣用的純頓號。
 */
export function joinList(items) {
  return items.join(i18n.t("ListFormat.separator", { ns: "common" }));
}
