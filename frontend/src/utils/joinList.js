import i18n from "../i18n";

/**
 * 依目前語系串接清單項目（檔名、帳號等）：中文、日文用「、」，英文用「, 」。
 * 分隔符號放在 common 語系檔的 ListFormat.separator，新增語系時一併補上。
 * 不用 Intl.ListFormat：它的中文會在最後一項前加「和」，不是介面慣用的純頓號。
 */
export function joinList(items) {
  return items.join(i18n.t("ListFormat.separator", { ns: "common" }));
}

/**
 * 依目前語系串接幾個分句（「已加入 3 位；找不到：…」）：中文、日文「；」，英文「; 」。
 * 寫死全形「；」會讓英文提示出現全形標點。
 */
export function joinClauses(items) {
  return items.join(i18n.t("ListFormat.clauseSeparator", { ns: "common" }));
}

/** 串接分句並補上句尾標點（中文、日文「。」，英文「.」），給一整句的提示用 */
export function joinSentence(items) {
  return `${joinClauses(items)}${i18n.t("ListFormat.sentenceEnd", { ns: "common" })}`;
}
