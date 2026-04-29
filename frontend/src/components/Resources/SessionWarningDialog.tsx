/**
 * Modal that pops when a student's auto-stop timer is approaching.
 *
 * The hook ``useSessionWarning`` polls every owned VM and flips the
 * ``shouldWarn`` flag when the backend reports ``should_warn=true``. The
 * student can then click "延長" to push back the auto-stop deadline by another
 * practice quota window.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Clock, RefreshCw } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import useCustomToast from "@/hooks/useCustomToast"
import {
  type SessionStatus,
  SessionWarningService,
} from "@/services/sessionWarning"

export function SessionWarningDialog({
  status,
  open,
  onClose,
}: {
  status: SessionStatus | null
  open: boolean
  onClose: () => void
}) {
  const qc = useQueryClient()
  const toast = useCustomToast()

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

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Clock className="h-5 w-5 text-amber-500" />
            VM 即將自動關機
          </DialogTitle>
          <DialogDescription>
            VM #{status.vmid} 將在約{" "}
            <strong>{status.minutes_until_stop ?? "?"} 分鐘</strong>{" "}
            後自動關機。需要繼續使用嗎？
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            稍後再說
          </Button>
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
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
