import { useEffect, useRef, useState } from "react"

import { AuthSessionService } from "@/services/authSession"

export type ClassroomEventType =
  | "live_started"
  | "live_stopped"
  | "takeover_started"
  | "takeover_stopped"
  | "watch_force_closed"

export interface ClassroomEvent {
  type: ClassroomEventType
  session_id?: string
  vmid?: number
  group_id?: string | null
}

type ClassroomEventHandler = (event: ClassroomEvent) => void

/**
 * 教室信令 WebSocket：常駐連線、自動重連，把後端推播的 live/takeover
 * 事件轉給 handler。handler 以 ref 保存，避免每次 render 重建連線。
 */
export function useClassroomSocket(
  onEvent: ClassroomEventHandler,
  options?: { enabled?: boolean },
): { connected: boolean } {
  const enabled = options?.enabled ?? true
  const handlerRef = useRef(onEvent)
  handlerRef.current = onEvent
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    if (!enabled) return

    let ws: WebSocket | null = null
    let stopped = false
    let reconnectTimer: number | null = null

    const schedule = () => {
      if (stopped || reconnectTimer !== null) return
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null
        open()
      }, 5000)
    }

    const open = () => {
      if (stopped) return
      const token = AuthSessionService.getAccessToken() || ""
      if (!token) {
        schedule()
        return
      }
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:"
      const url = `${proto}//${window.location.host}/ws/classroom?token=${encodeURIComponent(token)}`
      try {
        ws = new WebSocket(url)
      } catch {
        schedule()
        return
      }
      ws.onopen = () => setConnected(true)
      ws.onmessage = (evt) => {
        try {
          const event = JSON.parse(evt.data) as ClassroomEvent
          handlerRef.current(event)
        } catch {
          // ignore non-JSON frames
        }
      }
      ws.onclose = () => {
        setConnected(false)
        ws = null
        schedule()
      }
      ws.onerror = () => {
        ws?.close()
      }
    }

    open()

    return () => {
      stopped = true
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
      if (ws) {
        try {
          ws.close()
        } catch {
          // noop
        }
        ws = null
      }
    }
  }, [enabled])

  return { connected }
}
