import { useMutation, useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"
import type { BatchSpecItemPublic } from "@/client"
import { ResourcesService } from "@/client"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { TeachingAPI } from "@/services/teaching"

const STATUS_LABEL: Record<string, string> = {
  pending: "等待中",
  running: "調整中",
  ok: "已生效",
  needs_restart: "需重啟生效",
  quota_exceeded: "超出配額",
  error: "失敗",
}

function statusClass(status: string): string {
  if (status === "ok") return "text-green-600"
  if (status === "needs_restart") return "text-orange-500"
  if (status === "quota_exceeded" || status === "error") return "text-red-600"
  return ""
}

export default function BatchSpecPanel({ groupId }: { groupId: string }) {
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [cores, setCores] = useState("")
  const [memoryMb, setMemoryMb] = useState("")
  const [taskId, setTaskId] = useState<string | null>(null)

  const { data: vms } = useQuery({
    queryKey: ["teaching-heatmap", groupId],
    queryFn: () => TeachingAPI.getHeatmap(groupId),
  })

  const { data: status } = useQuery({
    queryKey: ["batch-spec", taskId],
    enabled: taskId !== null,
    refetchInterval: (q) =>
      q.state.data?.items.some(
        (i: BatchSpecItemPublic) =>
          i.status === "pending" || i.status === "running",
      )
        ? 2000
        : false,
    queryFn: () => TeachingAPI.getBatchSpecStatus(taskId as string),
  })

  const specMutation = useMutation({
    mutationFn: () =>
      TeachingAPI.startBatchSpec({
        vmids: [...selected],
        cores: cores ? Number(cores) : null,
        memory_mb: memoryMb ? Number(memoryMb) : null,
      }),
    onSuccess: (data) => {
      toast.success("批次調整任務已開始")
      setTaskId(data.task_id)
    },
    onError: (err: unknown) => {
      const detail = (err as { body?: { detail?: string } })?.body?.detail
      toast.error(detail || "批次調整啟動失敗")
    },
  })

  const rebootMutation = useMutation({
    mutationFn: (vmid: number) => ResourcesService.rebootResource({ vmid }),
    onSuccess: (_data, vmid) => {
      toast.success(`VM ${vmid} 重啟指令已送出`)
    },
    onError: (err: unknown) => {
      const detail = (err as { body?: { detail?: string } })?.body?.detail
      toast.error(detail || "重啟失敗")
    },
  })

  const toggle = (vmid: number) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(vmid)) next.delete(vmid)
      else next.add(vmid)
      return next
    })
  }

  const hasChange = cores !== "" || memoryMb !== ""

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>批次調整規格</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label>CPU 核心數（留空不變）</Label>
              <Input
                type="number"
                min={1}
                value={cores}
                onChange={(e) => setCores(e.target.value)}
                placeholder="4"
              />
            </div>
            <div className="space-y-2">
              <Label>記憶體 MB（留空不變）</Label>
              <Input
                type="number"
                min={256}
                value={memoryMb}
                onChange={(e) => setMemoryMb(e.target.value)}
                placeholder="4096"
              />
            </div>
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10" />
                <TableHead>VMID</TableHead>
                <TableHead>名稱</TableHead>
                <TableHead>擁有者</TableHead>
                <TableHead>狀態</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(vms ?? []).map((vm) => (
                <TableRow key={vm.vmid}>
                  <TableCell>
                    <Checkbox
                      checked={selected.has(vm.vmid)}
                      onCheckedChange={() => toggle(vm.vmid)}
                    />
                  </TableCell>
                  <TableCell>{vm.vmid}</TableCell>
                  <TableCell>{vm.name ?? "-"}</TableCell>
                  <TableCell>{vm.owner_name ?? "-"}</TableCell>
                  <TableCell>{vm.status}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <Button
            onClick={() => specMutation.mutate()}
            disabled={
              selected.size === 0 || !hasChange || specMutation.isPending
            }
          >
            調整 {selected.size} 台 VM
          </Button>
        </CardContent>
      </Card>

      {status && (
        <Card>
          <CardHeader>
            <CardTitle>調整結果</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>VMID</TableHead>
                  <TableHead>結果</TableHead>
                  <TableHead>原因</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {status.items.map((item) => (
                  <TableRow key={item.vmid}>
                    <TableCell>{item.vmid}</TableCell>
                    <TableCell className={statusClass(item.status)}>
                      {STATUS_LABEL[item.status] ?? item.status}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {item.error ?? "-"}
                    </TableCell>
                    <TableCell className="text-right">
                      {item.status === "needs_restart" && (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => rebootMutation.mutate(item.vmid)}
                          disabled={rebootMutation.isPending}
                        >
                          重啟
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
