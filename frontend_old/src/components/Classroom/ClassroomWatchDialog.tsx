import { useMutation } from "@tanstack/react-query"
import { Hand, Loader2, Monitor, Power, X } from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"
import { VncScreen } from "react-vnc"

import { Button } from "@/components/ui/button"
import { Dialog, DialogContent } from "@/components/ui/dialog"
import { cn } from "@/lib/utils"
import { AuthSessionService } from "@/services/authSession"
import { ClassroomAPI } from "@/services/classroom"

interface ClassroomWatchDialogProps {
  sessionId: string | null
  title?: string
  /** 僅 monitor 模式的發起者可見「接管/釋放」按鈕 */
  canControl?: boolean
  /** 初始控制權狀態（接管中） */
  initialControlling?: boolean
  /** Pair Mode：雙方皆可輸入，不走 controller 接管流程 */
  pair?: boolean
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function ClassroomWatchDialog({
  sessionId,
  title,
  canControl = false,
  initialControlling = false,
  pair = false,
  open,
  onOpenChange,
}: ClassroomWatchDialogProps) {
  const vncRef = useRef<React.ElementRef<typeof VncScreen>>(null)
  const [isConnected, setIsConnected] = useState(false)
  const [controlling, setControlling] = useState(initialControlling)

  useEffect(() => {
    if (open) {
      setControlling(initialControlling)
    } else {
      setIsConnected(false)
    }
  }, [open, initialControlling])

  const controlMutation = useMutation({
    mutationFn: (action: "take" | "release") =>
      ClassroomAPI.setControl(sessionId!, { action }),
    onSuccess: (_data, action) => setControlling(action === "take"),
  })

  const handleConnect = useCallback(() => setIsConnected(true), [])
  const handleDisconnect = useCallback(() => setIsConnected(false), [])

  const handleClose = () => {
    // 關閉前先釋放控制權，避免學生端持續被鎖定
    if (canControl && controlling && sessionId) {
      ClassroomAPI.setControl(sessionId, { action: "release" }).catch(() => {})
    }
    vncRef.current?.disconnect?.()
    setIsConnected(false)
    onOpenChange(false)
  }

  const proto = window.location.protocol === "https:" ? "wss:" : "ws:"
  const accessToken = AuthSessionService.getAccessToken() || ""
  const wsUrl =
    sessionId && open
      ? `${proto}//${window.location.host}/ws/classroom/${sessionId}/watch?token=${encodeURIComponent(accessToken)}`
      : ""
  // pair：雙方輸入都由後端放行；否則老師接管中才允許輸入
  const viewOnly = pair ? false : !(canControl && controlling)

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent
        className={cn(
          "w-[98vw] h-[95vh] max-w-[98vw] sm:max-w-[98vw] flex flex-col p-0 gap-0",
          "bg-zinc-900 border-zinc-700 overflow-hidden",
          "[&>button]:hidden",
        )}
      >
        <div className="flex flex-col h-full w-full bg-zinc-900">
          <div className="flex items-center justify-between px-4 py-2 bg-gradient-to-r from-zinc-800 to-zinc-900 border-b border-zinc-700 shrink-0">
            <div className="flex items-center gap-3">
              <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-emerald-500/20">
                <Monitor className="h-4 w-4 text-emerald-400" />
              </div>
              <div>
                <h2 className="text-sm font-semibold text-white">
                  {title || "教室觀看"}
                </h2>
                <span
                  className={cn(
                    "inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium",
                    isConnected
                      ? "bg-emerald-500/20 text-emerald-400"
                      : "bg-amber-500/20 text-amber-400",
                  )}
                >
                  <span
                    className={cn(
                      "w-1.5 h-1.5 rounded-full",
                      isConnected
                        ? "bg-emerald-400"
                        : "bg-amber-400 animate-pulse",
                    )}
                  />
                  {isConnected ? "已連線" : "連線中"}
                  {!viewOnly && "・接管中"}
                </span>
              </div>
            </div>

            <div className="flex items-center gap-1">
              {canControl && (
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={!isConnected || controlMutation.isPending}
                  onClick={() =>
                    controlMutation.mutate(controlling ? "release" : "take")
                  }
                  className={cn(
                    "h-8 px-3 text-xs disabled:opacity-40",
                    controlling
                      ? "text-amber-300 hover:text-amber-200 hover:bg-amber-500/10"
                      : "text-zinc-300 hover:text-white hover:bg-zinc-700/50",
                  )}
                >
                  <Hand className="h-3.5 w-3.5 mr-1.5" />
                  {controlling ? "釋放控制" : "接管"}
                </Button>
              )}
              <Button
                variant="ghost"
                size="icon"
                onClick={handleClose}
                className="h-8 w-8 text-zinc-400 hover:text-red-400 hover:bg-red-500/10"
                title="關閉"
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
          </div>

          <div className="flex-1 overflow-hidden bg-black relative">
            {!isConnected && wsUrl && (
              <div className="absolute inset-0 flex flex-col items-center justify-center bg-zinc-900 z-20">
                <Loader2 className="h-8 w-8 text-blue-400 animate-spin mb-3" />
                <p className="text-sm text-zinc-400">正在連接畫面…</p>
              </div>
            )}

            {wsUrl && (
              <VncScreen
                url={wsUrl}
                ref={vncRef}
                scaleViewport
                viewOnly={viewOnly}
                onConnect={handleConnect}
                onDisconnect={handleDisconnect}
                style={{ width: "100%", height: "100%", background: "#000" }}
              />
            )}
          </div>

          <div className="flex items-center justify-end px-4 py-1.5 bg-zinc-800/50 border-t border-zinc-700/50 shrink-0">
            <Button
              variant="ghost"
              size="sm"
              onClick={handleClose}
              className="h-7 px-3 text-xs text-red-400 hover:text-red-300 hover:bg-red-500/10"
            >
              <Power className="h-3 w-3 mr-1.5" />
              關閉觀看
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
