import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Ban, Camera, Loader2, Pickaxe, ShieldCheck, Undo2 } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { type MiningIncidentPublic, MiningIncidentsService } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Textarea } from "@/components/ui/textarea"

const STATUS_LABELS: Record<string, string> = {
  detected: "已偵測",
  suspended: "已暫停",
  banned: "已停權",
  dismissed: "已解除",
}

function getApiErrorMessage(error: unknown): string {
  if (error && typeof error === "object") {
    const maybe = error as { detail?: string; message?: string }
    if (typeof maybe.detail === "string") return maybe.detail
    if (typeof maybe.message === "string") return maybe.message
  }
  return "未知錯誤"
}

function StatusBadge({ status }: { status: string }) {
  const variant =
    status === "suspended" || status === "detected"
      ? ("destructive" as const)
      : ("outline" as const)
  return <Badge variant={variant}>{STATUS_LABELS[status] ?? status}</Badge>
}

export default function MiningIncidentsPanel() {
  const queryClient = useQueryClient()
  const [banTarget, setBanTarget] = useState<MiningIncidentPublic | null>(null)
  const [dismissTarget, setDismissTarget] =
    useState<MiningIncidentPublic | null>(null)
  const [dismissExempt, setDismissExempt] = useState(false)
  const [dismissNote, setDismissNote] = useState("")

  const { data: incidents, isLoading } = useQuery({
    queryKey: ["miningIncidents"],
    queryFn: () => MiningIncidentsService.listIncidents(),
    refetchInterval: 30000,
  })

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["miningIncidents"] })

  const banMutation = useMutation({
    mutationFn: (incidentId: string) =>
      MiningIncidentsService.banIncident({ incidentId }),
    onSuccess: () => {
      toast.success("帳號已停權，VM 維持暫停狀態")
      setBanTarget(null)
      invalidate()
    },
    onError: (error) => toast.error(`停權失敗：${getApiErrorMessage(error)}`),
  })

  const dismissMutation = useMutation({
    mutationFn: (args: { incidentId: string; exempt: boolean; note: string }) =>
      MiningIncidentsService.dismissIncident({
        incidentId: args.incidentId,
        requestBody: { exempt: args.exempt, note: args.note || null },
      }),
    onSuccess: (result) => {
      toast.success(
        result.status === "dismissed"
          ? "已解除事件並嘗試恢復 VM"
          : "已解除事件",
      )
      setDismissTarget(null)
      setDismissExempt(false)
      setDismissNote("")
      invalidate()
    },
    onError: (error) => toast.error(`解除失敗：${getApiErrorMessage(error)}`),
  })

  const open = (incidents ?? []).filter(
    (i) => i.status === "detected" || i.status === "suspended",
  )
  const closed = (incidents ?? []).filter(
    (i) => i.status === "banned" || i.status === "dismissed",
  )

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-base flex items-center gap-2">
              <Pickaxe className="h-4 w-4" />
              挖礦事件
            </CardTitle>
            <CardDescription>
              CPU 長期滿載的疑似挖礦資源（已自動存證與暫停，待管理員審核）
            </CardDescription>
          </div>
          {open.length > 0 && (
            <Badge variant="destructive">{open.length} 待處理</Badge>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="flex justify-center py-6">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : !incidents || incidents.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-6 text-muted-foreground">
            <ShieldCheck className="h-6 w-6" />
            <p className="text-sm">目前沒有挖礦事件</p>
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>VMID</TableHead>
                <TableHead>平均 CPU</TableHead>
                <TableHead>觀察視窗</TableHead>
                <TableHead>存證快照</TableHead>
                <TableHead>狀態</TableHead>
                <TableHead>偵測時間</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {[...open, ...closed].map((incident) => (
                <TableRow key={incident.id}>
                  <TableCell className="font-mono text-sm">
                    {incident.vmid}
                  </TableCell>
                  <TableCell className="font-mono text-sm">
                    {incident.avg_cpu.toFixed(1)}%
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    {incident.window_hours} 小時
                  </TableCell>
                  <TableCell>
                    {incident.snapshot_name ? (
                      <span className="inline-flex items-center gap-1 font-mono text-xs">
                        <Camera className="h-3 w-3" />
                        {incident.snapshot_name}
                      </span>
                    ) : (
                      <span className="text-xs text-muted-foreground">
                        存證失敗
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={incident.status} />
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {new Date(incident.detected_at).toLocaleString("zh-TW")}
                  </TableCell>
                  <TableCell className="text-right">
                    {(incident.status === "detected" ||
                      incident.status === "suspended") && (
                      <div className="flex justify-end gap-1.5">
                        <Button
                          size="sm"
                          variant="destructive"
                          onClick={() => setBanTarget(incident)}
                        >
                          <Ban className="mr-1 h-3.5 w-3.5" />
                          停權
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => setDismissTarget(incident)}
                        >
                          <Undo2 className="mr-1 h-3.5 w-3.5" />
                          誤判解除
                        </Button>
                      </div>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>

      {/* 停權確認 */}
      <Dialog
        open={banTarget !== null}
        onOpenChange={(o) => !o && setBanTarget(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>確認停權帳號？</DialogTitle>
            <DialogDescription>
              VMID {banTarget?.vmid} 的擁有者帳號將被停用（無法登入），VM
              維持暫停狀態以保留證據。此操作可由管理員在使用者管理中還原。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setBanTarget(null)}>
              取消
            </Button>
            <Button
              variant="destructive"
              disabled={banMutation.isPending}
              onClick={() => banTarget && banMutation.mutate(banTarget.id)}
            >
              {banMutation.isPending && (
                <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
              )}
              確認停權
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 誤判解除 */}
      <Dialog
        open={dismissTarget !== null}
        onOpenChange={(o) => {
          if (!o) {
            setDismissTarget(null)
            setDismissExempt(false)
            setDismissNote("")
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>解除挖礦事件</DialogTitle>
            <DialogDescription>
              VMID {dismissTarget?.vmid} 將標記為誤判並嘗試恢復運行。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <Checkbox
                id="mining-exempt"
                checked={dismissExempt}
                onCheckedChange={(v) => setDismissExempt(v === true)}
              />
              <Label htmlFor="mining-exempt" className="text-sm">
                同時將此資源加入豁免（之後不再偵測）
              </Label>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="mining-note" className="text-sm">
                備註（選填）
              </Label>
              <Textarea
                id="mining-note"
                placeholder="例如：教授的模型訓練工作負載"
                value={dismissNote}
                onChange={(e) => setDismissNote(e.target.value)}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDismissTarget(null)}>
              取消
            </Button>
            <Button
              disabled={dismissMutation.isPending}
              onClick={() =>
                dismissTarget &&
                dismissMutation.mutate({
                  incidentId: dismissTarget.id,
                  exempt: dismissExempt,
                  note: dismissNote,
                })
              }
            >
              {dismissMutation.isPending && (
                <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
              )}
              確認解除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  )
}
