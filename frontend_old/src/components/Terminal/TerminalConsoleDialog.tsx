import { VisuallyHidden } from "@radix-ui/react-visually-hidden"
import {
  Loader2,
  Maximize,
  Minimize2,
  RotateCcw,
  Terminal,
  Trash2,
  X,
} from "lucide-react"
import { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog"
import { cn } from "@/lib/utils"

import useXTermDisplay from "./XTermDisplay"

interface TerminalConsoleDialogProps {
  vmid: number | null
  vmName?: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function TerminalConsoleDialog({
  vmid,
  vmName,
  open,
  onOpenChange,
}: TerminalConsoleDialogProps) {
  const [isFullscreen, setIsFullscreen] = useState(false)
  const { t } = useTranslation("resources")

  const {
    status,
    error: terminalError,
    terminalRef,
    handleDisconnect,
    handleClear,
    handleReset,
    fitTerminal,
  } = useXTermDisplay({
    vmid: vmid || 0,
    onDisconnect: () => {
      onOpenChange(false)
    },
  })

  const isConnected = status === "connected"
  const isLoading = status === "connecting"

  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(!!document.fullscreenElement)
      // Fit terminal when entering/exiting fullscreen
      setTimeout(() => {
        fitTerminal()
      }, 100)
    }
    document.addEventListener("fullscreenchange", handleFullscreenChange)
    return () =>
      document.removeEventListener("fullscreenchange", handleFullscreenChange)
  }, [fitTerminal])

  const handleClose = () => {
    handleDisconnect()
    onOpenChange(false)
  }

  const toggleFullscreen = () => {
    const container = document.querySelector(".terminal-dialog-container")
    if (!container) return

    if (!document.fullscreenElement) {
      container.requestFullscreen?.()
    } else {
      document.exitFullscreen?.()
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent
        className={cn(
          "terminal-dialog-container w-[98vw] h-[95vh] max-w-[98vw] sm:max-w-[98vw] flex flex-col p-0 gap-0",
          "bg-zinc-900 border-zinc-700 overflow-hidden",
          "[&>button]:hidden",
        )}
      >
        <VisuallyHidden>
          <DialogTitle>
            {t("console.terminal.title", { name: vmName || `LXC ${vmid}` })}
          </DialogTitle>
        </VisuallyHidden>
        <div className="flex items-center justify-between px-4 py-2 bg-gradient-to-r from-zinc-800 to-zinc-900 border-b border-zinc-700 shrink-0">
          <div className="flex items-center gap-3">
            <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-green-500/20">
              <Terminal className="h-4 w-4 text-green-400" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-white">
                {vmName || `LXC ${vmid}`}
              </h2>
              <div className="flex items-center gap-2">
                <span className="text-xs text-zinc-400">VMID: {vmid}</span>
                <span
                  className={cn(
                    "inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium",
                    isConnected
                      ? "bg-green-500/20 text-green-400"
                      : isLoading
                        ? "bg-amber-500/20 text-amber-400"
                        : "bg-zinc-500/20 text-zinc-400",
                  )}
                >
                  <span
                    className={cn(
                      "w-1.5 h-1.5 rounded-full",
                      isConnected
                        ? "bg-green-400"
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
              onClick={handleClear}
              disabled={!isConnected}
              className="h-8 px-3 text-xs text-zinc-300 hover:text-white hover:bg-zinc-700/50 disabled:opacity-40"
            >
              <Trash2 className="h-3.5 w-3.5 mr-1.5" />
              {t("console.buttons.clear")}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={handleReset}
              disabled={!isConnected}
              className="h-8 px-3 text-xs text-zinc-300 hover:text-white hover:bg-zinc-700/50 disabled:opacity-40"
            >
              <RotateCcw className="h-3.5 w-3.5 mr-1.5" />
              {t("console.buttons.reset")}
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

        <div className="flex-1 overflow-hidden bg-[#1e1e1e] relative">
          {isLoading && (
            <div className="absolute inset-0 flex flex-col items-center justify-center bg-zinc-900 z-20">
              <div className="flex flex-col items-center">
                <div className="relative mb-6">
                  <div className="w-20 h-20 rounded-full bg-zinc-800 flex items-center justify-center">
                    <Terminal className="h-10 w-10 text-zinc-600" />
                  </div>
                  <div className="absolute -bottom-1 -right-1 w-8 h-8 rounded-full bg-zinc-800 flex items-center justify-center border-2 border-zinc-900">
                    <Loader2 className="h-4 w-4 text-green-400 animate-spin" />
                  </div>
                </div>
                <h3 className="text-lg font-medium text-white mb-2">
                  {t("console.terminal.connecting")}
                </h3>
                <p className="text-sm text-zinc-400">
                  {t("console.terminal.connectingDescription", {
                    name: vmName || `LXC ${vmid}`,
                  })}
                </p>
              </div>
            </div>
          )}

          {terminalError && (
            <div className="absolute top-4 left-4 right-4 bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded z-10">
              {terminalError}
            </div>
          )}

          {vmid && open && <div ref={terminalRef} className="w-full h-full" />}
        </div>

        <div className="flex items-center justify-between px-4 py-1.5 bg-zinc-800/50 border-t border-zinc-700/50 shrink-0">
          <div className="flex items-center gap-4 text-[11px] text-zinc-500">
            <span>
              WebSocket:{" "}
              {isConnected
                ? t("console.websocket.connected")
                : t("console.websocket.disconnected")}
            </span>
            <span>•</span>
            <span>{t("console.protocol.terminal")}</span>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={handleClose}
              className="h-7 px-3 text-xs text-red-400 hover:text-red-300 hover:bg-red-500/10"
            >
              <X className="h-3 w-3 mr-1.5" />
              {t("console.buttons.disconnect")}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
