import { useQuery } from "@tanstack/react-query"
import {
  Clipboard,
  Keyboard,
  Loader2,
  Maximize,
  Minimize2,
  Monitor,
  Power,
  X,
} from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import { VncScreen } from "react-vnc"

import { useClassroomTakeover } from "@/components/Classroom/ClassroomStudentLayer"
import { TakeoverOverlay } from "@/components/Classroom/TakeoverOverlay"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent } from "@/components/ui/dialog"
import { queryKeys } from "@/lib/queryKeys"
import { cn } from "@/lib/utils"
import { AuthSessionService } from "@/services/authSession"
import { VncConsoleService } from "@/services/vncConsole"

interface VNCConsoleDialogProps {
  vmid: number | null
  vmName?: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function VNCConsoleDialog({
  vmid,
  vmName,
  open,
  onOpenChange,
}: VNCConsoleDialogProps) {
  const vncRef = useRef<React.ElementRef<typeof VncScreen>>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [isConnected, setIsConnected] = useState(false)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const { t } = useTranslation("resources")
  const underTakeover = useClassroomTakeover(vmid)

  const consoleQuery = useQuery({
    queryKey: queryKeys.resources.console(vmid ?? 0),
    queryFn: () => VncConsoleService.getVmConsole({ vmid: vmid! }),
    enabled: open && vmid !== null,
    retry: false,
    staleTime: 0,
    gcTime: 0,
    refetchOnWindowFocus: false,
  })

  useEffect(() => {
    if (!open) {
      setIsConnected(false)
    }
  }, [open])

  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(!!document.fullscreenElement)
    }
    document.addEventListener("fullscreenchange", handleFullscreenChange)
    return () =>
      document.removeEventListener("fullscreenchange", handleFullscreenChange)
  }, [])

  const handleConnect = useCallback(() => {
    setIsConnected(true)
  }, [])

  const handleDisconnect = useCallback(() => {
    setIsConnected(false)
  }, [])

  const handleClose = () => {
    vncRef.current?.disconnect?.()
    setIsConnected(false)
    onOpenChange(false)
  }

  const sendCtrlAltDel = () => {
    vncRef.current?.sendCtrlAltDel?.()
  }

  const handleClipboard = async () => {
    try {
      const text = await navigator.clipboard.readText()
      vncRef.current?.clipboardPaste?.(text)
    } catch (err) {
      console.error("Clipboard error:", err)
    }
  }

  const toggleFullscreen = () => {
    if (!containerRef.current) return

    if (!document.fullscreenElement) {
      containerRef.current.requestFullscreen?.()
    } else {
      document.exitFullscreen?.()
    }
  }

  const apiUrl =
    typeof window !== "undefined"
      ? new URL(
          import.meta.env.VITE_API_URL ||
            `${window.location.protocol}//${window.location.host}`,
        )
      : new URL("http://localhost:8000")
  const protocol = apiUrl.protocol === "https:" ? "wss:" : "ws:"
  const accessToken = AuthSessionService.getAccessToken() || ""
  const vncTicket = consoleQuery.data?.ticket ?? null
  const vncPort = consoleQuery.data?.port ?? null
  const isLoading = consoleQuery.isLoading || consoleQuery.isFetching
  const error = consoleQuery.isError
    ? t("console.vnc.fetchError", { error: consoleQuery.error.message })
    : !isLoading && open && vmid && !vncTicket
      ? t("console.vnc.ticketError")
      : null

  const wsUrl =
    vmid && vncTicket
      ? `${protocol}//${apiUrl.host}/ws/vnc/${vmid}?token=${encodeURIComponent(accessToken)}&vnc_ticket=${encodeURIComponent(vncTicket)}${vncPort ? `&vnc_port=${encodeURIComponent(vncPort)}` : ""}`
      : ""

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent
        className={cn(
          "w-[98vw] h-[95vh] max-w-[98vw] sm:max-w-[98vw] flex flex-col p-0 gap-0",
          "bg-zinc-900 border-zinc-700 overflow-hidden",
          "[&>button]:hidden",
        )}
      >
        <div
          ref={containerRef}
          className="flex flex-col h-full w-full bg-zinc-900"
        >
          <div className="flex items-center justify-between px-4 py-2 bg-gradient-to-r from-zinc-800 to-zinc-900 border-b border-zinc-700 shrink-0">
            <div className="flex items-center gap-3">
              <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-emerald-500/20">
                <Monitor className="h-4 w-4 text-emerald-400" />
              </div>
              <div>
                <h2 className="text-sm font-semibold text-white">
                  {vmName || `VM ${vmid}`}
                </h2>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-zinc-400">VMID: {vmid}</span>
                  <span
                    className={cn(
                      "inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium",
                      isConnected
                        ? "bg-emerald-500/20 text-emerald-400"
                        : isLoading
                          ? "bg-amber-500/20 text-amber-400"
                          : "bg-zinc-500/20 text-zinc-400",
                    )}
                  >
                    <span
                      className={cn(
                        "w-1.5 h-1.5 rounded-full",
                        isConnected
                          ? "bg-emerald-400"
                          : isLoading
                            ? "bg-amber-400 animate-pulse"
                            : "bg-zinc-400",
                      )}
                    />
                    {isConnected
                      ? t("console.status.connected")
                      : isLoading
                        ? t("console.status.connecting")
                        : t("console.status.disconnected")}
                  </span>
                </div>
              </div>
            </div>

            <div className="flex items-center gap-1">
              <Button
                variant="ghost"
                size="sm"
                onClick={sendCtrlAltDel}
                disabled={!isConnected}
                className="h-8 px-3 text-xs text-zinc-300 hover:text-white hover:bg-zinc-700/50 disabled:opacity-40"
              >
                <Keyboard className="h-3.5 w-3.5 mr-1.5" />
                Ctrl+Alt+Del
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={handleClipboard}
                disabled={!isConnected}
                className="h-8 px-3 text-xs text-zinc-300 hover:text-white hover:bg-zinc-700/50 disabled:opacity-40"
              >
                <Clipboard className="h-3.5 w-3.5 mr-1.5" />
                {t("console.buttons.paste")}
              </Button>
              <div className="w-px h-6 bg-zinc-700 mx-1" />
              <Button
                variant="ghost"
                size="icon"
                onClick={toggleFullscreen}
                disabled={!isConnected}
                className="h-8 w-8 text-zinc-300 hover:text-white hover:bg-zinc-700/50 disabled:opacity-40"
                title={
                  isFullscreen
                    ? t("console.buttons.exitFullscreen")
                    : t("console.buttons.fullscreen")
                }
              >
                {isFullscreen ? (
                  <Minimize2 className="h-4 w-4" />
                ) : (
                  <Maximize className="h-4 w-4" />
                )}
              </Button>
              <Button
                variant="ghost"
                size="icon"
                onClick={handleClose}
                className="h-8 w-8 text-zinc-400 hover:text-red-400 hover:bg-red-500/10"
                title={t("console.buttons.close")}
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
          </div>

          <div className="flex-1 overflow-hidden bg-black relative">
            {error && (
              <div className="absolute inset-0 flex flex-col items-center justify-center bg-zinc-900 z-20">
                <div className="flex flex-col items-center text-center p-8 max-w-md">
                  <div className="w-16 h-16 rounded-full bg-red-500/10 flex items-center justify-center mb-4">
                    <Power className="h-8 w-8 text-red-400" />
                  </div>
                  <h3 className="text-lg font-medium text-white mb-2">
                    {t("console.vnc.connectionFailed")}
                  </h3>
                  <p className="text-sm text-zinc-400 mb-6">{error}</p>
                  <Button
                    onClick={handleClose}
                    variant="outline"
                    className="border-zinc-600 text-zinc-300 hover:bg-zinc-800"
                  >
                    {t("console.buttons.close")}
                  </Button>
                </div>
              </div>
            )}

            {!error && (isLoading || (!isConnected && vncTicket)) && (
              <div className="absolute inset-0 flex flex-col items-center justify-center bg-zinc-900 z-20">
                <div className="flex flex-col items-center">
                  <div className="relative mb-6">
                    <div className="w-20 h-20 rounded-full bg-zinc-800 flex items-center justify-center">
                      <Monitor className="h-10 w-10 text-zinc-600" />
                    </div>
                    <div className="absolute -bottom-1 -right-1 w-8 h-8 rounded-full bg-zinc-800 flex items-center justify-center border-2 border-zinc-900">
                      <Loader2 className="h-4 w-4 text-blue-400 animate-spin" />
                    </div>
                  </div>
                  <h3 className="text-lg font-medium text-white mb-2">
                    {t("console.vnc.connecting")}
                  </h3>
                  <p className="text-sm text-zinc-400">
                    {t("console.vnc.connectingDescription", {
                      name: vmName || `VM ${vmid}`,
                    })}
                  </p>
                </div>
              </div>
            )}

            {vmid && open && wsUrl && vncTicket && (
              <VncScreen
                url={wsUrl}
                scaleViewport
                ref={vncRef}
                rfbOptions={{
                  credentials: {
                    username: "",
                    password: vncTicket,
                    target: "",
                  },
                }}
                onConnect={handleConnect}
                onDisconnect={handleDisconnect}
                style={{
                  width: "100%",
                  height: "100%",
                  background: "#000",
                }}
              />
            )}

            {underTakeover && <TakeoverOverlay />}
          </div>

          <div className="flex items-center justify-between px-4 py-1.5 bg-zinc-800/50 border-t border-zinc-700/50 shrink-0">
            <div className="flex items-center gap-4 text-[11px] text-zinc-500">
              <span>
                WebSocket:{" "}
                {wsUrl
                  ? t("console.websocket.connected")
                  : t("console.websocket.disconnected")}
              </span>
              <span>|</span>
              <span>{t("console.protocol.vnc")}</span>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={handleClose}
                className="h-7 px-3 text-xs text-red-400 hover:text-red-300 hover:bg-red-500/10"
              >
                <Power className="h-3 w-3 mr-1.5" />
                {t("console.buttons.disconnect")}
              </Button>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
