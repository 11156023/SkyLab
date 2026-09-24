/**
 * useClassroomSocket.js
 * 教室信令 WebSocket：常駐連線、斷線後指數退避重連（5 秒起，上限 60 秒），
 * 把後端推播的 live/takeover 事件轉給 handler。
 * handler 以 ref 保存，避免每次 render 重建連線。
 *
 * token 被拒（close code 1008）不重連：重試再多次也一樣會被拒，
 * 只會一直對後端敲門；等使用者重新登入後元件重掛載自然會再連。
 *
 * 事件型別：live_started | live_stopped | takeover_started |
 *          takeover_stopped | watch_force_closed
 */

import { useEffect, useRef, useState } from "react";
import { AuthStorage } from "../services/auth";

/** 重連退避：起始 5 秒、上限 60 秒 */
const RECONNECT_BASE_MS = 5_000;
const RECONNECT_MAX_MS = 60_000;
/** WebSocket close code 1008：後端以政策理由拒絕（token 無效／過期） */
const WS_POLICY_VIOLATION = 1008;

/** 依 VITE_API_URL（或當前位置）組出 WS base，與 VncDialog 同邏輯 */
export function wsBaseUrl() {
  const apiUrl = new URL(
    import.meta.env.VITE_API_URL || `${window.location.protocol}//${window.location.host}`,
  );
  const proto = apiUrl.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${apiUrl.host}`;
}

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
      ws.onclose = (evt) => {
        setConnected(false);
        ws = null;
        /* 1008 = policy violation：後端拒絕這張 token，再連也沒用 */
        if (evt?.code === WS_POLICY_VIOLATION) {
          stopped = true;
          return;
        }
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
