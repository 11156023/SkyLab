/**
 * Modal that pops when a student's auto-stop timer or expiry deadline is
 * approaching.
 *
 * Two flavors based on ``warn_reason``:
 * - ``auto_stop``: VM is about to be powered off (group practice quota or
 *   course-window grace). Practice quota lets the student extend; window-grace
 *   ones can't because the schedule dictates the cutoff.
 * - ``expiry``: VM's ``expiry_date`` is within the configured window. No
 *   self-service extend here — the student must request a new spec_change_request.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { CalendarX, Clock, RefreshCw } from "lucide-react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import useCustomToast from "@/hooks/useCustomToast"
import {
  type SessionStatus,
  SessionWarningService,
} from "@/services/sessionWarning"

export function SessionWarningDialog({
  status,
  open,
  onClose,
  onDismissPermanent,
}: {
  status: SessionStatus | null
  open: boolean
  onClose: () => void
  onDismissPermanent: () => void
}) {
  const qc = useQueryClient()
  const toast = useCustomToast()
  const [doNotShow, setDoNotShow] = useState(false)

  const mutation = useMutation({
    mutationFn: (vmid: number) => SessionWarningService.extend(vmid),
    onSuccess: (result) => {
      toast.showSuccessToast(`已延長 ${result.extended_minutes / 60} 小時`)
      qc.invalidateQueries({ queryKey: ["sessionStatus"] })
      onClose()
    },
    onError: (e: any) => {
      toast.showErrorToast(e?.body?.detail ?? "延長失敗")
    },
  })

  if (!status) return null

  const isExpiry = status.warn_reason === "expiry"

  const handleClose = () => {
    if (doNotShow) {
      onDismissPermanent()
    } else {
      onClose()
    }
    setDoNotShow(false)
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && handleClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {isExpiry ? (
              <>
                <CalendarX className="h-5 w-5 text-rose-500" />
                資源即將到期
              </>
            ) : (
              <>
                <Clock className="h-5 w-5 text-amber-500" />
                VM 即將自動關機
              </>
            )}
          </DialogTitle>
          <DialogDescription>
            {isExpiry ? (
              <>
                VM #{status.vmid} 將在約{" "}
                <strong>{status.hours_until_expiry ?? "?"} 小時</strong>{" "}
                後到期並停用。請及早備份資料；如需延長使用期限，請向管理員申請。
              </>
            ) : (
              <>
                VM #{status.vmid} 將在約{" "}
                <strong>{status.minutes_until_stop ?? "?"} 分鐘</strong>{" "}
                後自動關機。需要繼續使用嗎？
              </>
            )}
          </DialogDescription>
        </DialogHeader>
        <div className="flex items-center gap-2 py-1">
          <Checkbox
            id="do-not-show"
            checked={doNotShow}
            onCheckedChange={(v) => setDoNotShow(v === true)}
          />
          <Label
            htmlFor="do-not-show"
            className="text-sm text-muted-foreground cursor-pointer"
          >
            不再顯示此提醒
          </Label>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={handleClose}>
            {isExpiry ? "知道了" : "稍後再說"}
          </Button>
          {!isExpiry && (
            <Button
              disabled={!status.can_extend || mutation.isPending}
              onClick={() => mutation.mutate(status.vmid)}
            >
              <RefreshCw
                className={`mr-2 h-4 w-4 ${
                  mutation.isPending ? "animate-spin" : ""
                }`}
              />
              延長使用時間
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
