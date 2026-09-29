import { apiGet } from "./api";
import { wsBaseUrl } from "../utils/wsUrl";

export const JobsService = {
  /** 列出統一背景任務,支援篩選 */
  list(params) {
    const q = new URLSearchParams();
    if (params?.kinds?.length)    q.set("kinds",        params.kinds.join(","));
    if (params?.statuses?.length) q.set("statuses",     params.statuses.join(","));
    if (params?.activeOnly)       q.set("active_only",  "true");
    if (params?.limit != null)    q.set("limit",        String(params.limit));
    if (params?.offset != null)   q.set("offset",       String(params.offset));
    if (params?.historyDays)      q.set("history_days", String(params.historyDays));
    const qs = q.toString();
    return apiGet(`/api/v1/jobs/${qs ? `?${qs}` : ""}`);
  },

  /** 取得單一 Job 詳情 (id 格式: <kind>:<source_id>) */
  detail(jobId) {
    return apiGet(`/api/v1/jobs/${encodeURIComponent(jobId)}`);
  },
};

export const JOBS_WS_RECONNECT_BASE_MS = 5_000;
export const JOBS_WS_RECONNECT_MAX_MS = 60_000;

/**
 * 建立 /ws/jobs 即時推送連線，每次收到後端 snapshot 時呼叫 onSnapshot。
 * 斷線後以指數退避自動重連（5 秒起、最長 60 秒，連上後重置），後端重啟時
 * 不會讓所有分頁每 5 秒一起撞過來。回傳中止函式（供 useEffect cleanup 用）。
 *
 * @param {string | (() => string | null)} token access token，或每次連線時取得
 *   最新 token 的函式（token 會被 refresh 換掉，重連時要用新的才過得了認證）。
 * @param {(snapshot: object) => void} onSnapshot
 * @param {{ onStatusChange?: (connected: boolean) => void }} [options]
 *   連線狀態變化時通知（呼叫端可在 WS 正常時停掉 REST 輪詢）。
 */
export function connectJobsWebSocket(token, onSnapshot, { onStatusChange } = {}) {
  const resolveUrl = () => {
    const value = typeof token === "function" ? token() : token;
    if (!value) return null;
    return `${wsBaseUrl()}/ws/jobs?token=${encodeURIComponent(value)}`;
  };

  let ws = null;
  let stopped = false;
  let reconnectTimer = null;
  let retryDelay = JOBS_WS_RECONNECT_BASE_MS;

  const schedule = () => {
    if (stopped || reconnectTimer !== null) return;
    const delay = retryDelay;
    retryDelay = Math.min(retryDelay * 2, JOBS_WS_RECONNECT_MAX_MS);
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      open();
    }, delay);
  };

  const open = () => {
    if (stopped) return;
    const url = resolveUrl();
    // 尚未登入／token 剛被清掉：稍後再試，不要拿空 token 去撞後端
    if (!url) {
      schedule();
      return;
    }
    try {
      ws = new WebSocket(url);
    } catch {
      schedule();
      return;
    }
    ws.onopen = () => {
      retryDelay = JOBS_WS_RECONNECT_BASE_MS;
      onStatusChange?.(true);
    };
    ws.onmessage = (evt) => {
      try {
        onSnapshot(JSON.parse(evt.data));
      } catch {
        // 非 JSON 訊息直接忽略
      }
    };
    ws.onclose = () => {
      ws = null;
      if (!stopped) onStatusChange?.(false);
      schedule();
    };
  };

  open();

  return () => {
    stopped = true;
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (ws) {
      try { ws.close(); } catch { /* noop */ }
      ws = null;
    }
  };
}
