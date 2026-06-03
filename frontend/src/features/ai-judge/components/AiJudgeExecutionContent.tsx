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
import {
  AiJudgeService,
  type TeacherJudgeScriptRun,
  type TeacherJudgeScriptRunTargetProgress,
  type TeacherJudgeScriptRunTargetResult,
} from "@/features/ai-judge/api"
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

function RunStatusBadge({
  status,
}: {
  status: TeacherJudgeScriptRun["status"]
}) {
  if (status === "completed") return <Badge>已完成</Badge>
  if (status === "running") return <Badge>執行中</Badge>
  if (status === "failed") return <Badge variant="destructive">失敗</Badge>
  if (status === "cancelled") return <Badge variant="secondary">已取消</Badge>
  return <Badge variant="outline">等待中</Badge>
}

function TargetStatusBadge({
  status,
}: {
  status: TeacherJudgeScriptRunTargetProgress["status"] | string
}) {
  if (status === "completed") return <Badge>完成</Badge>
  if (status === "running") return <Badge>執行中</Badge>
  if (status === "failed") return <Badge variant="destructive">失敗</Badge>
  return <Badge variant="outline">排隊中</Badge>
}

function AiJudgementBadge({
  result,
}: {
  result?: TeacherJudgeScriptRunTargetResult
}) {
  if (!result) return <Badge variant="outline">等待回收</Badge>
  if (result.validation?.valid === false) {
    return <Badge variant="destructive">JSON 格式錯誤</Badge>
  }
  const judgement = result.ai_judgement
  if (!judgement) return <Badge variant="outline">分析中</Badge>
  if (judgement.status === "completed") {
    const score = typeof judgement.score === "number" ? judgement.score : null
    const maxScore =
      typeof judgement.max_score === "number" ? judgement.max_score : 5
    return <Badge>{score === null ? "已分析" : `${score}/${maxScore}`}</Badge>
  }
  if (judgement.status === "failed") {
    return <Badge variant="destructive">AI 分析失敗</Badge>
  }
  if (judgement.status === "skipped") {
    return <Badge variant="secondary">略過</Badge>
  }
  return <Badge variant="outline">分析中</Badge>
}

function reasonLabel(reasonCode?: string | null) {
  switch (reasonCode) {
    case "success":
      return "成功"
    case "not_running":
      return "未運行"
    case "missing_ip":
      return "缺少 IP"
    case "missing_ssh_key":
      return "缺少 SSH 金鑰"
    case "owner_mismatch":
      return "資源擁有者不一致"
    case "missing_db_resource":
      return "資料庫無資源"
    case "invalid_resource_type":
      return "類型不可執行"
    case "python_missing":
      return "缺少 python3"
    case "execution_nonzero":
      return "腳本執行失敗"
    case "result_too_large":
      return "結果過大"
    case "invalid_json":
      return "JSON 格式錯誤"
    case "executor_error":
      return "執行器錯誤"
    default:
      return reasonCode ?? null
  }
}

function formatUsage(value?: number | null) {
  if (typeof value !== "number" || Number.isNaN(value)) return "--"
  return `${Math.round(value)}%`
}

function runIsTerminal(status?: TeacherJudgeScriptRun["status"]) {
  return status === "completed" || status === "failed" || status === "cancelled"
}

function aiJudgementSummary(result?: TeacherJudgeScriptRunTargetResult) {
  if (!result) return null
  if (result.validation?.valid === false) {
    return result.validation.error ?? "JSON 驗證未通過，未進入 AI 分析。"
  }
  const judgement = result.ai_judgement
  if (!judgement) return "AI 分析尚未完成。"
  return judgement.error ?? judgement.summary ?? null
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
  const [activeRunRef, setActiveRunRef] = useState<{
    scriptId: string
    runId: string
  } | null>(null)

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

  const activeRunQuery = useQuery({
    queryKey: activeRunRef
      ? queryKeys.groups.teacherJudgeScriptRun(
          groupId,
          activeRunRef.scriptId,
          activeRunRef.runId,
        )
      : queryKeys.groups.teacherJudgeScriptRun(groupId, "none", "none"),
    queryFn: () =>
      AiJudgeService.getScriptRun({
        groupId,
        scriptId: activeRunRef!.scriptId,
        runId: activeRunRef!.runId,
      }),
    enabled: activeRunRef !== null,
    refetchInterval: (query) =>
      runIsTerminal(query.state.data?.status) ? false : 2000,
  })
  const activeRun = activeRunQuery.data
  const progressTargets = (activeRun?.progress_json.targets ??
    []) as TeacherJudgeScriptRunTargetProgress[]
  const resultTargets = (activeRun?.target_results_json.targets ??
    []) as TeacherJudgeScriptRunTargetResult[]
  const resultByVmid = new Map(
    resultTargets.map((result) => [result.vmid, result]),
  )

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
      setActiveRunRef({ scriptId: effectiveScriptId, runId: run.id })
      setDialogOpen(false)
      setSelectedScriptId(null)
      setSelectedVmids([])
      queryClient.invalidateQueries({
        queryKey: queryKeys.groups.teacherJudgeScripts(groupId),
      })
      queryClient.setQueryData(
        queryKeys.groups.teacherJudgeScriptRun(
          groupId,
          effectiveScriptId,
          run.id,
        ),
        run,
      )
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
            {runningMembers.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={6}
                  className="py-8 text-center text-sm text-muted-foreground"
                >
                  目前沒有可執行的運行中 VM/LXC。
                </TableCell>
              </TableRow>
            ) : (
              runningMembers.map((member) => {
                const vmid = member.vmid ?? null
                return (
                  <TableRow key={member.user_id}>
                    <TableCell>
                      <Checkbox
                        checked={vmid !== null && selectedSet.has(vmid)}
                        disabled={vmid === null}
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

      {activeRun && (
        <div className="rounded-lg border">
          <div className="flex flex-wrap items-start justify-between gap-3 border-b px-4 py-3">
            <div>
              <div className="flex items-center gap-2">
                <h3 className="font-semibold">最近一次執行結果</h3>
                <RunStatusBadge status={activeRun.status} />
              </div>
              <p className="mt-1 text-sm text-muted-foreground">
                進度 {activeRun.progress_json.done ?? 0} /{" "}
                {activeRun.progress_json.total ?? progressTargets.length} 台
              </p>
            </div>
            {activeRunQuery.isFetching && !runIsTerminal(activeRun.status) && (
              <div className="text-sm text-muted-foreground">更新中...</div>
            )}
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>VMID</TableHead>
                <TableHead>成員</TableHead>
                <TableHead>來源節點</TableHead>
                <TableHead>執行狀態</TableHead>
                <TableHead>AI 分析</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {progressTargets.map((target) => {
                const result = resultByVmid.get(target.vmid)
                const user = result?.user ?? target.user
                const proxmoxNode = result?.proxmox_node ?? target.proxmox_node
                const resourceType =
                  result?.resource_type ?? target.resource_type
                const targetReason = reasonLabel(
                  result?.reason_code ?? target.reason_code,
                )
                return (
                  <TableRow key={target.vmid}>
                    <TableCell className="font-mono text-sm">
                      {target.name ?? target.vmid}
                    </TableCell>
                    <TableCell>
                      <div className="text-sm">{user?.full_name ?? "-"}</div>
                      {user?.email && (
                        <div className="text-xs text-muted-foreground">
                          {user.email}
                        </div>
                      )}
                    </TableCell>
                    <TableCell>
                      <div className="font-mono text-sm">
                        {proxmoxNode ?? "-"}
                      </div>
                      <div className="text-xs uppercase text-muted-foreground">
                        {resourceType ?? "-"}
                      </div>
                    </TableCell>
                    <TableCell>
                      <TargetStatusBadge status={target.status} />
                      {targetReason && targetReason !== "成功" && (
                        <div className="mt-1 text-xs text-muted-foreground">
                          {targetReason}
                        </div>
                      )}
                    </TableCell>
                    <TableCell>
                      <AiJudgementBadge result={result} />
                      {result ? (
                        <details className="mt-2 text-sm">
                          <summary className="cursor-pointer text-muted-foreground">
                            查看心得
                          </summary>
                          {aiJudgementSummary(result) && (
                            <p
                              className={
                                result.validation?.valid === false ||
                                result.ai_judgement?.status === "failed"
                                  ? "mt-2 text-destructive text-xs"
                                  : "mt-2 text-muted-foreground text-xs"
                              }
                            >
                              {aiJudgementSummary(result)}
                            </p>
                          )}
                          {result.ai_judgement?.item_judgements?.length ? (
                            <div className="mt-2 space-y-2">
                              {result.ai_judgement.item_judgements.map(
                                (item, index) => (
                                  <div
                                    key={`${item.item_id ?? "item"}-${index}`}
                                    className="rounded-md bg-muted p-3 text-xs"
                                  >
                                    <div className="flex flex-wrap items-center justify-between gap-2">
                                      <span className="font-medium text-foreground">
                                        {item.title ??
                                          item.item_id ??
                                          "評分項目"}
                                      </span>
                                      {typeof item.score === "number" && (
                                        <Badge variant="outline">
                                          {item.score}/{item.max_score ?? 1}
                                        </Badge>
                                      )}
                                    </div>
                                    {item.comment && (
                                      <p className="mt-1 text-muted-foreground">
                                        {item.comment}
                                      </p>
                                    )}
                                  </div>
                                ),
                              )}
                            </div>
                          ) : null}
                        </details>
                      ) : (
                        <div className="mt-1 text-sm text-muted-foreground">
                          等待回收
                        </div>
                      )}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
      )}

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
