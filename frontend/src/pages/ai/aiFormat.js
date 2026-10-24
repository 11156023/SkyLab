/* AI 頁面（AI 用量監控、AI API 我的用量）共用的區間與顯示格式 helper */

/* 區間換算搬到 utils/aiTimeRange（查詢層也要用，不能反向 import 頁面），這裡轉出給既有的 import */
export { presetToRange } from "../../utils/aiTimeRange";

export function presetToBucket(preset) {
  return preset === "7d" ? "hour" : "day";
}

export function formatTokens(n) {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

export function formatDuration(ms) {
  if (ms == null) return "—";
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${ms}ms`;
}

export function formatTokenRate(value) {
  if (value == null) return "—";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "—";
  return `${numeric.toLocaleString(undefined, { maximumFractionDigits: 2 })} tok/s`;
}

/* 模型名稱只顯示公開可辨識的部分：HF 快取目錄 models--org--name → org/name，
   絕對路徑只留最後一段，不把伺服器路徑露給使用者 */
export function formatModelDisplay(modelName) {
  if (!modelName) return "—";
  const trimmed = modelName.trim();
  if (!trimmed) return "—";

  const match = trimmed.match(/models--([^/]+)--([^/]+)/);
  if (match) return `${match[1]}/${match[2]}`;

  if (/^(?:[A-Za-z]:[\\/]|[\\/])/.test(trimmed)) {
    const separator = Math.max(trimmed.lastIndexOf("/"), trimmed.lastIndexOf("\\"));
    const basename = separator >= 0 ? trimmed.slice(separator + 1) : trimmed;
    return basename || trimmed;
  }

  return trimmed;
}

export function isOkStatus(status) {
  return (
    status === "success" ||
    status === 200 ||
    status === "200" ||
    status === "ok"
  );
}
