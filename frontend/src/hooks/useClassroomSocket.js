/**
 * useClassroomSocket.js
 * 教室信令 WebSocket：常駐連線、斷線 5 秒自動重連，
 * 把後端推播的 live/takeover 事件轉給 handler。
 * handler 以 ref 保存，避免每次 render 重建連線。
 *
 * 事件型別：live_started | live_stopped | takeover_started |
 *          takeover_stopped | watch_force_closed
 */

import { useEffect, useRef, useState } from "react";
import { AuthStorage } from "../services/auth";

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

    const schedule = () => {
      if (stopped || reconnectTimer !== null) return;
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null;
        open();
      }, 5_000);
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
      ws.onopen = () => setConnected(true);
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
