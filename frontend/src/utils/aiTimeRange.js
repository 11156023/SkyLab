/**
 * AI 用量／監控的區間代號（7d／30d／90d）換成起訖時間（ISO 字串）。
 * 查詢層（queries/aiMonitoring）在每次抓取時才換算，所以放 utils，不放頁面的 aiFormat。
 */
export function presetToRange(preset) {
  const end = new Date();
  const start = new Date();
  const days = preset === "7d" ? 7 : preset === "30d" ? 30 : 90;
  start.setDate(start.getDate() - days);
  return { startDate: start.toISOString(), endDate: end.toISOString() };
}
