import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Copy, Loader2 } from "lucide-react"
import { useEffect, useState } from "react"
import { toast } from "sonner"

import type { ApiError, VmTemplatePublic } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { TemplatesAPI } from "@/services/templates"

function errorMessage(error: unknown, fallback: string) {
  const apiError = error as ApiError & { body?: { detail?: string } }
  return apiError.body?.detail ?? apiError.message ?? fallback
}

type Props = {
  open: boolean
  onOpenChange: (open: boolean) => void
  template: VmTemplatePublic | null
  /** teacher/admin 可批量；student 固定單台 */
  canBatch: boolean
}

export function TemplateCloneDialog({
  open,
  onOpenChange,
  template,
  canBatch,
}: Props) {
  const queryClient = useQueryClient()
  const [hostname, setHostname] = useState("")
  const [count, setCount] = useState("1")
  const [cores, setCores] = useState("")
  const [memory, setMemory] = useState("")
  const [disk, setDisk] = useState("")
  const [start, setStart] = useState(true)

  useEffect(() => {
    if (!open) return
    setHostname("")
    setCount("1")
    setCores(template?.default_cores ? String(template.default_cores) : "")
    setMemory(template?.default_memory ? String(template.default_memory) : "")
    setDisk(template?.default_disk ? String(template.default_disk) : "")
    setStart(true)
  }, [open, template])

  const mutation = useMutation({
    mutationFn: () => {
      if (!template) throw new Error("no template")
      const numOrNull = (v: string) => (v.trim() ? Number(v) : null)
      return TemplatesAPI.clone(template.id, {
        hostname: hostname.trim() || null,
        count: canBatch ? Math.max(1, Number(count) || 1) : 1,
        cores: numOrNull(cores),
        memory: numOrNull(memory),
        disk: numOrNull(disk),
        start,
      })
    },
    onSuccess: (res) => {
      toast.success(
        res.tasks.length > 1
          ? `已送出 ${res.tasks.length} 台克隆任務，可在下方任務清單追蹤進度`
          : "克隆任務已送出，完成後會出現在你的資源列表",
      )
      queryClient.invalidateQueries({ queryKey: ["template-tasks"] })
      onOpenChange(false)
    },
    onError: (error: unknown) => {
      toast.error(errorMessage(error, "克隆失敗"))
    },
  })

  if (!template) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Copy className="h-5 w-5 text-sky-500" />
            克隆「{template.name}」
          </DialogTitle>
          <DialogDescription>
            系統會以 linked clone 快速複製（必要時自動改用完整複製），
            並自動配置 IP 與防火牆。完成後可在資源頁操作。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_120px]">
            <div className="space-y-2">
              <Label>主機名稱（選填）</Label>
              <Input
                value={hostname}
                onChange={(e) => setHostname(e.target.value)}
                placeholder={`預設使用範本名稱`}
                maxLength={63}
              />
            </div>
            {canBatch && (
              <div className="space-y-2">
                <Label>數量</Label>
                <Input
                  type="number"
                  min={1}
                  max={50}
                  value={count}
                  onChange={(e) => setCount(e.target.value)}
                />
              </div>
            )}
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-2">
              <Label>CPU 核數</Label>
              <Input
                type="number"
                min={1}
                max={64}
                value={cores}
                onChange={(e) => setCores(e.target.value)}
                placeholder="沿用範本"
              />
            </div>
            <div className="space-y-2">
              <Label>記憶體 (MB)</Label>
              <Input
                type="number"
                min={128}
                value={memory}
                onChange={(e) => setMemory(e.target.value)}
                placeholder="沿用範本"
              />
            </div>
            <div className="space-y-2">
              <Label>磁碟 (GB)</Label>
              <Input
                type="number"
                min={1}
                value={disk}
                onChange={(e) => setDisk(e.target.value)}
                placeholder="沿用範本"
                disabled={template.resource_type === "lxc"}
              />
            </div>
          </div>

          <div className="flex items-center justify-between rounded-lg border border-border/60 px-3 py-2.5">
            <div className="text-sm">克隆完成後自動開機</div>
            <Switch checked={start} onCheckedChange={setStart} />
          </div>
        </div>

        <DialogFooter className="gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
          >
            {mutation.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Copy className="mr-2 h-4 w-4" />
            )}
            開始克隆
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
