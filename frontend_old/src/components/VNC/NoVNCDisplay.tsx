import { useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"

interface NoVNCDisplayProps {
  vmid: number
  onDisconnect?: () => void
  controls?: {
    viewOnly?: boolean
    scaleViewport?: boolean
    resizeSession?: boolean
    showDotCursor?: boolean
    clipViewport?: boolean
    dragViewport?: boolean
    qualityLevel?: number
    compressionLevel?: number
  }
}

type ConnectionStatus = "connecting" | "connected" | "disconnected" | "error"

interface RFBInstance {
  viewOnly: boolean
  scaleViewport: boolean
  resizeSession: boolean
  showDotCursor: boolean
  clipViewport: boolean
  dragViewport: boolean
  qualityLevel: number
  compressionLevel: number
  disconnect: () => void
  sendCtrlAltDel: () => void
  clipboardPasteFrom: (text: string) => void
  addEventListener: (event: string, callback: (e: CustomEvent) => void) => void
}

export default function useNoVNCDisplay({
  vmid,
  onDisconnect,
  controls,
}: NoVNCDisplayProps) {
  const { t } = useTranslation("console")
  const canvasRef = useRef<HTMLDivElement>(null)
  const rfbRef = useRef<RFBInstance | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const [status, setStatus] = useState<ConnectionStatus>("connecting")
  const [error, setError] = useState("")

  const viewOnly = controls?.viewOnly ?? false
  const scaleViewport = controls?.scaleViewport ?? true
  const resizeSession = controls?.resizeSession ?? true
  const showDotCursor = controls?.showDotCursor ?? false
  const clipViewport = controls?.clipViewport ?? false
  const dragViewport = controls?.dragViewport ?? false
  const qualityLevel = controls?.qualityLevel ?? 6
  const compressionLevel = controls?.compressionLevel ?? 6

  useEffect(() => {
    if (!vmid || !canvasRef.current) return

    setStatus("connecting")
    setError("")

    let rfb: RFBInstance | null = null
    let ws: WebSocket | null = null
    let isSubscribed = true
    let ticketReceived = false

    const initVNC = async () => {
      try {
        const RFBModule = await import("@novnc/novnc/lib/rfb")
        const RFB = RFBModule.default

        const apiUrl = new URL(
          import.meta.env.VITE_API_URL ||
            `${window.location.protocol}//${window.location.host}`,
        )
        const protocol = apiUrl.protocol === "https:" ? "wss:" : "ws:"
        const accessToken = localStorage.getItem("access_token") || ""
        const wsUrl = `${protocol}//${apiUrl.host}/ws/vnc/${vmid}?token=${encodeURIComponent(accessToken)}`
        console.log("Connecting to backend WebSocket:", wsUrl)

        ws = new WebSocket(wsUrl)
        wsRef.current = ws

        ws.onopen = () => {
          console.log("✅ Backend WebSocket connected")
        }

        ws.onmessage = async (event) => {
          try {
            const data = JSON.parse(event.data)

            if (data.type === "vnc_ticket") {
              console.log("✅ Received VNC ticket from backend")
              ticketReceived = true

              const container = canvasRef.current
              if (!container || !ws) return

              rfb = new RFB(container, ws, {
                shared: true,
                credentials: {
                  username: "",
                  password: data.ticket,
                  target: "",
                },
              })

              rfb.scaleViewport = scaleViewport
              rfb.resizeSession = resizeSession
              rfb.viewOnly = viewOnly
              rfb.qualityLevel = qualityLevel
              rfb.compressionLevel = compressionLevel
              rfb.showDotCursor = showDotCursor
              rfb.clipViewport = clipViewport
              rfb.dragViewport = dragViewport

              rfb.addEventListener("connect", () => {
                if (!isSubscribed) return
                setStatus("connected")
                setError("")
                console.log("✅ noVNC connected successfully")
              })

              rfb.addEventListener("disconnect", (e: CustomEvent) => {
                if (!isSubscribed) return
                setStatus("disconnected")
                if (e.detail.clean) {
                  console.log("✅ noVNC disconnected cleanly")
                  setError(t("vnc.normalClosure"))
                } else {
                  const errorMsg =
                    e.detail?.reason || t("vnc.wsUnexpectedClose")
                  setError(t("vnc.disconnected", { reason: errorMsg }))
                  console.error("❌ noVNC disconnected with error:", e.detail)
                }
              })

              rfb.addEventListener("securityfailure", (e: CustomEvent) => {
                if (!isSubscribed) return
                setStatus("error")
                setError(
                  t("vnc.securityFailure", {
                    reason: e.detail.reason || t("shared.unknownReason"),
                  }),
                )
                console.error("❌ noVNC security failure:", e.detail)
              })

              rfb.addEventListener("credentialsrequired", () => {
                if (!isSubscribed) return
                console.error("❌ Credentials required")
                setStatus("error")
                setError(t("vnc.credentialsRequired"))
              })

              rfbRef.current = rfb
              console.log("noVNC RFB instance created with existing WebSocket")
            }
          } catch {
            console.debug("Received non-JSON message (possibly binary data)")
          }
        }

        ws.onerror = (wsError) => {
          console.error("❌ Backend WebSocket error:", wsError)
          if (!ticketReceived) {
            setStatus("error")
            setError(t("vnc.backendConnectFailed"))
          }
        }

        ws.onclose = (event) => {
          console.log("Backend WebSocket closed:", event.code, event.reason)
          if (!ticketReceived && isSubscribed) {
            setStatus("error")
            setError(t("vnc.wsClosed"))
          }
        }
      } catch (err) {
        if (!isSubscribed) return
        setStatus("error")
        setError(t("vnc.initFailed", { message: (err as Error).message }))
        console.error("Connection initialization error:", err)
      }
    }

    initVNC()

    return () => {
      console.log("Cleaning up connections")
      isSubscribed = false
      if (rfbRef.current) {
        rfbRef.current.disconnect()
        rfbRef.current = null
      }
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }
    }
  }, [
    vmid,
    scaleViewport,
    resizeSession,
    viewOnly,
    qualityLevel,
    compressionLevel,
    showDotCursor,
    clipViewport,
    dragViewport,
    t,
  ])

  const handleDisconnect = () => {
    if (rfbRef.current) {
      rfbRef.current.disconnect()
    }
    onDisconnect?.()
  }

  const sendCtrlAltDel = () => {
    rfbRef.current?.sendCtrlAltDel()
  }

  const toggleFullscreen = () => {
    const container = canvasRef.current
    if (!container) return

    if (!document.fullscreenElement) {
      container.requestFullscreen().catch((err) => {
        console.error(t("vnc.fullscreenFailed"), err)
      })
    } else {
      document.exitFullscreen()
    }
  }

  const handleClipboard = () => {
    if (rfbRef.current) {
      navigator.clipboard
        .readText()
        .then((text) => {
          rfbRef.current?.clipboardPasteFrom(text)
        })
        .catch((err) => {
          console.error(t("vnc.clipboardFailed"), err)
        })
    }
  }

  return {
    status,
    error,
    canvasRef,
    handleDisconnect,
    sendCtrlAltDel,
    toggleFullscreen,
    handleClipboard,
  }
}

export function NoVNCDisplayComponent({
  vmid,
  onDisconnect,
  controls,
}: NoVNCDisplayProps) {
  const { status, error, canvasRef } = useNoVNCDisplay({
    vmid,
    onDisconnect,
    controls,
  })

  return (
    <div className="relative w-full h-full bg-black">
      {status === "connecting" && !error && (
        <div className="absolute inset-0 flex flex-col items-center justify-center bg-gray-900 z-20">
          <div className="relative">
            <div className="w-16 h-16 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="w-8 h-8 bg-blue-500 rounded-full animate-pulse" />
            </div>
          </div>
          <div className="mt-6 text-white text-lg font-medium">
            正在連接 VNC...
          </div>
          <div className="mt-2 text-gray-400 text-sm">請稍候</div>
        </div>
      )}

      {error && (
        <div className="absolute top-4 left-4 right-4 bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded z-10">
          {error}
        </div>
      )}

      <div
        ref={canvasRef}
        className="w-full h-full bg-black"
        style={{
          minHeight: "600px",
          position: "relative",
        }}
      />
    </div>
  )
}
