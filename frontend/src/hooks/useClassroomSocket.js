/**
 * useClassroomSocket.js
 * 教室信令 WebSocket：常駐連線、斷線後指數退避重連（5 秒起，上限 60 秒），
 * 把後端推播的 live/takeover 事件轉給 handler。
 * handler 以 ref 保存，避免每次 render 重建連線。
 *
 * token 被拒（close code 1008）也照一般退避重連：多半是 access token 過期，
 * 期間 api 層的 refresh 會換上新 token，每次重連都重新讀最新的 token
 * （與 services/jobs.js 的 connectJobsWebSocket 相同做法），
 * 不必等元件重掛載才恢復推播；退避上限 60 秒，不會一直敲後端。
 *
 * 事件型別：live_started | live_stopped | takeover_started |
 *          takeover_stopped | watch_force_closed
 */

import { useEffect, useRef, useState } from "react";
import { AuthStorage } from "../services/auth";
import { wsBaseUrl } from "../utils/wsUrl";

/* 相容舊的匯入路徑；新程式請直接從 utils/wsUrl 匯入 */
export { wsBaseUrl };

/** 重連退避：起始 5 秒、上限 60 秒 */
const RECONNECT_BASE_MS = 5_000;
const RECONNECT_MAX_MS = 60_000;

export function useClassroomSocket(onEvent, { enabled = true } = {}) {
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!enabled) return undefined;

    let ws = null;
    let stopped = false;
    let reconnectTimer = null;
    /* 退避：第一次 5 秒，之後每次加倍到 60 秒封頂；連上就歸零 */
    let retryDelay = RECONNECT_BASE_MS;

    const schedule = () => {
      if (stopped || reconnectTimer !== null) return;
      const delay = retryDelay;
      retryDelay = Math.min(retryDelay * 2, RECONNECT_MAX_MS);
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null;
        open();
      }, delay);
    };

    const open = () => {
      if (stopped) return;
      /* 每次連線都讀最新 token：1008 被拒後 refresh 可能已換上新 token */
      const token = AuthStorage.getAccessToken() || "";
      if (!token) {
        schedule();
        return;
      }
      const url = `${wsBaseUrl()}/ws/classroom?token=${encodeURIComponent(token)}`;
      try {
        ws = new WebSocket(url);
      } catch {
        schedule();
        return;
      }
      ws.onopen = () => {
        retryDelay = RECONNECT_BASE_MS;
        setConnected(true);
      };
      ws.onmessage = (evt) => {
        try {
          handlerRef.current(JSON.parse(evt.data));
        } catch {
          // 忽略非 JSON frame
        }
      };
      ws.onclose = () => {
        setConnected(false);
        ws = null;
        schedule();
      };
      ws.onerror = () => {
        ws?.close();
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
        try {
          ws.close();
        } catch {
          // noop
        }
        ws = null;
      }
    };
  }, [enabled]);

  return { connected };
}
