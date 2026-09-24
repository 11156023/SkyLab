/**
 * weeklyEdits.js
 * 每週內容「還沒儲存的修改」不被重抓的資料蓋掉。班級在審核／建機中時，
 * 頁面每 3 秒重抓一次班級資料，每次都是新的 weeks 陣列；直接拿來換掉畫面，
 * 老師打到一半的主題就會消失。
 */

/** 畫面上可以改、要按「儲存」才送出的欄位；其餘（id、日期、檔案）以伺服器為準 */
const UNSAVED_FIELDS = ["title", "target", "status"];

/**
 * 以伺服器的週次為準，把畫面上還沒儲存的欄位依上課日期疊回去。
 * 改過上課日期、週次重新排過時，對不到的週直接用伺服器的值。
 *
 * @param {object[]} serverWeeks 剛從伺服器拿到的週次（已 normalize）
 * @param {object[]} localWeeks  畫面上目前的週次
 * @returns {object[]}
 */
export function mergeUnsavedWeekEdits(serverWeeks, localWeeks) {
  return serverWeeks.map((serverWeek) => {
    const local = localWeeks.find((week) => week.date === serverWeek.date);
    if (!local) return serverWeek;
    const merged = { ...serverWeek };
    for (const field of UNSAVED_FIELDS) merged[field] = local[field];
    return merged;
  });
}
