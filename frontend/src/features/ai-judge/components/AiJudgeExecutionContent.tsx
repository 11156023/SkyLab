import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { PlayCircle } from "lucide-react"
import { useMemo, useState } from "react"

import type { GroupMemberPublic } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { AiJudgeService } from "@/features/ai-judge/api"
import useCustomToast from "@/hooks/useCustomToast"
import { queryKeys } from "@/lib/queryKeys"

type RunnableMember = GroupMemberPublic & {
  vm_cpu_usage_pct?: number | null
  vm_ram_usage_pct?: number | null
  vm_disk_usage_pct?: number | null
}

function VmStatusBadge({ status }: { status?: string | null }) {
  if (status === "running") return <Badge>運行中</Badge>
  if (status === "stopped") return <Badge variant="outline">已關機</Badge>
  return <Badge variant="secondary">{status ?? "未建立"}</Badge>
}

function formatUsage(value?: number | null) {
  if (typeof value !== "number" || Number.isNaN(value)) return "--"
  return `${Math.round(value)}%`
}

export function AiJudgeExecutionContent({
  groupId,
  members,
}: {
  groupId: string
  members: RunnableMember[]
}) {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [selectedVmids, setSelectedVmids] = useState<number[]>([])
  const [dialogOpen, setDialogOpen] = useState(false)
  const [selectedScriptId, setSelectedScriptId] = useState<string | null>(null)

  const scriptsQuery = useQuery({
    queryKey: queryKeys.groups.teacherJudgeScripts(groupId),
    queryFn: () => AiJudgeService.listScripts({ groupId }),
  })

  const approvedScripts = useMemo(
    () =>
      (scriptsQuery.data ?? []).filter(
        (script) => script.status === "approved",
      ),
    [scriptsQuery.data],
  )
  const effectiveScriptId = selectedScriptId ?? approvedScripts[0]?.id ?? ""
  const effectiveScript = approvedScripts.find(
    (script) => script.id === effectiveScriptId,
  )
  const runningMembers = members.filter(
    (member) =>
      member.vmid &&
      member.vm_status === "running" &&
      (member.vm_type === "qemu" || member.vm_type === "lxc"),
  )
  const selectedSet = new Set(selectedVmids)

  const createRunMutation = useMutation({
    mutationFn: () =>
      AiJudgeService.createScriptRun({
        groupId,
        scriptId: effectiveScriptId,
        target_vmids: selectedVmids,
      }),
    onSuccess: (run) => {
      showSuccessToast(
        `已建立腳本執行任務（${run.progress_json.total ?? selectedVmids.length} 台）`,
      )
      setDialogOpen(false)
      setSelectedScriptId(null)
      setSelectedVmids([])
      queryClient.invalidateQueries({
        queryKey: queryKeys.groups.teacherJudgeScripts(groupId),
      })
    },
    onError: (err: any) =>
      showErrorToast(err?.body?.detail ?? err?.message ?? "建立執行任務失敗"),
  })

  const toggleVmid = (vmid: number, checked: boolean) => {
    setSelectedVmids((current) => {
      if (checked) return Array.from(new Set([...current, vmid]))
      return current.filter((item) => item !== vmid)
    })
  }

  const selectAllRunning = () => {
    setSelectedVmids(
      runningMembers.map((member) => member.vmid!).filter(Boolean),
    )
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold tracking-tight">腳本執行</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            選擇群組內運行中的 VM/LXC，套用已核准的 AI 收集腳本。
          </p>
        </div>
        <Button
          onClick={() => setDialogOpen(true)}
          disabled={selectedVmids.length === 0 || approvedScripts.length === 0}
        >
          <PlayCircle className="mr-1 h-4 w-4" />
          執行腳本
        </Button>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border bg-muted/20 px-4 py-3 text-sm">
        <div className="text-muted-foreground">
          可執行 {runningMembers.length} / 全部 {members.length} 台，已選{" "}
          <span className="font-medium text-foreground">
            {selectedVmids.length}
          </span>{" "}
          台
        </div>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={selectAllRunning}
            disabled={runningMembers.length === 0}
          >
            選取運行中
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setSelectedVmids([])}
            disabled={selectedVmids.length === 0}
          >
            清除
          </Button>
        </div>
      </div>

      <div className="overflow-hidden rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-12" />
              <TableHead>機器名稱</TableHead>
              <TableHead>成員</TableHead>
              <TableHead>類型</TableHead>
              <TableHead>狀態</TableHead>
              <TableHead>資源摘要</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {members.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={6}
                  className="py-8 text-center text-sm text-muted-foreground"
                >
                  尚無群組成員。
                </TableCell>
              </TableRow>
            ) : (
              members.map((member) => {
                const vmid = member.vmid ?? null
                const runnable =
                  vmid !== null &&
                  member.vm_status === "running" &&
                  (member.vm_type === "qemu" || member.vm_type === "lxc")
                return (
                  <TableRow
                    key={member.user_id}
                    className={!runnable ? "opacity-60" : undefined}
                  >
                    <TableCell>
                      <Checkbox
                        checked={vmid !== null && selectedSet.has(vmid)}
                        disabled={!runnable || vmid === null}
                        onCheckedChange={(checked) => {
                          if (vmid !== null) toggleVmid(vmid, checked === true)
                        }}
                      />
                    </TableCell>
                    <TableCell className="font-mono text-sm">
                      {vmid ?? "-"}
                    </TableCell>
                    <TableCell>
                      <div className="text-sm">{member.full_name ?? "-"}</div>
                      <div className="text-xs text-muted-foreground">
                        {member.email}
                      </div>
                    </TableCell>
                    <TableCell className="uppercase text-xs text-muted-foreground">
                      {member.vm_type ?? "-"}
                    </TableCell>
                    <TableCell>
                      <VmStatusBadge status={member.vm_status} />
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      CPU {formatUsage(member.vm_cpu_usage_pct)} · RAM{" "}
                      {formatUsage(member.vm_ram_usage_pct)} · 碟{" "}
                      {formatUsage(member.vm_disk_usage_pct)}
                    </TableCell>
                  </TableRow>
                )
              })
            )}
          </TableBody>
        </Table>
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>確認執行腳本</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <div className="text-sm font-medium">選擇腳本</div>
              <Select
                value={effectiveScriptId}
                onValueChange={(value) => setSelectedScriptId(value)}
              >
                <SelectTrigger>
                  <SelectValue placeholder="選擇已核准腳本" />
                </SelectTrigger>
                <SelectContent>
                  {approvedScripts.map((script) => (
                    <SelectItem key={script.id} value={script.id}>
                      {script.name} v{script.version}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {approvedScripts.length === 0 && (
                <p className="text-xs text-muted-foreground">
                  目前沒有已核准的收集腳本，請先到 AI 收集腳本頁核准。
                </p>
              )}
            </div>

            <div className="rounded-md border bg-muted/20 p-3">
              <div className="text-sm font-medium">
                執行機器（{selectedVmids.length} 台）
              </div>
              <div className="mt-2 flex flex-wrap gap-2">
                {selectedVmids.map((vmid) => (
                  <Badge key={vmid} variant="secondary">
                    {vmid}
                  </Badge>
                ))}
              </div>
              <p className="mt-3 text-xs text-muted-foreground">
                後端會在送出時再次確認這些 VM/LXC 仍屬於此群組且正在運行。
              </p>
            </div>

            {effectiveScript && (
              <div className="text-xs text-muted-foreground">
                即將使用：{effectiveScript.name} v{effectiveScript.version}（
                {effectiveScript.template_key}）
              </div>
            )}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDialogOpen(false)}
              disabled={createRunMutation.isPending}
            >
              取消
            </Button>
            <Button
              onClick={() => createRunMutation.mutate()}
              disabled={
                createRunMutation.isPending ||
                selectedVmids.length === 0 ||
                !effectiveScriptId
              }
            >
              {createRunMutation.isPending ? "建立中..." : "確認執行"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
