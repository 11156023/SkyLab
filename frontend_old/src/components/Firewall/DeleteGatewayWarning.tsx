import { AlertTriangle } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

type Props = {
  open: boolean
  vmName: string
  onConfirm: () => void
  onClose: () => void
}

export function DeleteGatewayWarning({
  open,
  vmName,
  onConfirm,
  onClose,
}: Props) {
  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-card border-border text-foreground sm:max-w-sm">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-amber-400">
            <AlertTriangle className="w-5 h-5" />
            確認刪除上網連線
          </DialogTitle>
        </DialogHeader>

        <div className="py-2 text-sm text-muted-foreground space-y-2">
          <p>
            你正在刪除{" "}
            <span className="text-foreground font-medium">{vmName}</span>{" "}
            的上網連線（往 Internet 的預設出站規則）。
          </p>
          <p className="text-amber-400/80">
            刪除後此 VM 將無法連到外部網路，除非重新建立連線。
          </p>
        </div>

        <DialogFooter className="gap-2">
          <Button
            variant="ghost"
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground"
          >
            取消
          </Button>
          <Button
            onClick={() => {
              onConfirm()
              onClose()
            }}
            className="bg-red-900 hover:bg-red-800 text-white"
          >
            確認刪除
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
