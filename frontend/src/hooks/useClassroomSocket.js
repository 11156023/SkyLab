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
import { wsBaseUrl } from "../utils/wsUrl";
import { connectReconnectingWebSocket } from "../utils/reconnectingWebSocket";

/* 相容舊的匯入路徑；新程式請直接從 utils/wsUrl 匯入 */
export { wsBaseUrl };

/** 重連退避：起始 5 秒、上限 60 秒 */
export function useClassroomSocket(onEvent, { enabled = true } = {}) {
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!enabled) return undefined;

    setConnected(false);
    return connectReconnectingWebSocket(() => {
      const token = AuthStorage.getAccessToken();
      return token ? `${wsBaseUrl()}/ws/classroom?token=${encodeURIComponent(token)}` : null;
    }, {
      onOpen: () => setConnected(true),
      onClose: () => setConnected(false),
      onMessage: (event) => {
        try { handlerRef.current(JSON.parse(event.data)); } catch { /* Ignore invalid JSON. */ }
      },
    });
  }, [enabled]);

  return { connected };
}
