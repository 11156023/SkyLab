import { useMutation, useQuery } from "@tanstack/react-query"
import { useRef, useState } from "react"
import { toast } from "sonner"
import type { ConfigPushItemPublic } from "@/client"
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
  running: "分發中",
  ok: "成功",
  error: "失敗",
}

function statusClass(status: string): string {
  if (status === "ok") return "text-green-600"
  if (status === "error") return "text-red-600"
  return ""
}

export default function ConfigPushPanel({ groupId }: { groupId: string }) {
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [targetPath, setTargetPath] = useState("")
  const [taskId, setTaskId] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const { data: vms } = useQuery({
    queryKey: ["teaching-heatmap", groupId],
    queryFn: () => TeachingAPI.getHeatmap(groupId),
  })

  const { data: status } = useQuery({
    queryKey: ["config-push", taskId],
    enabled: taskId !== null,
    refetchInterval: (q) =>
      q.state.data?.items.some(
        (i: ConfigPushItemPublic) =>
          i.status === "pending" || i.status === "running",
      )
        ? 2000
        : false,
    queryFn: () => TeachingAPI.getConfigPushStatus(taskId as string),
  })

  const pushMutation = useMutation({
    mutationFn: () => {
      const file = fileRef.current?.files?.[0]
      if (!file) return Promise.reject(new Error("請先選擇檔案"))
      return TeachingAPI.startConfigPush({
        file,
        targetPath,
        vmids: [...selected],
      })
    },
    onSuccess: (data) => {
      toast.success("分發任務已開始")
      setTaskId(data.task_id)
    },
    onError: (err: unknown) => {
      const detail =
        (err as { body?: { detail?: string } })?.body?.detail ||
        (err as Error)?.message
      toast.error(detail || "分發啟動失敗（檔案上限 1 MB，路徑必須為絕對路徑）")
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

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>配置文件分發</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label>配置檔案（上限 1 MB）</Label>
              <Input type="file" ref={fileRef} />
            </div>
            <div className="space-y-2">
              <Label>目標絕對路徑</Label>
              <Input
                value={targetPath}
                onChange={(e) => setTargetPath(e.target.value)}
                placeholder="/etc/nginx/nginx.conf"
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
            onClick={() => pushMutation.mutate()}
            disabled={
              selected.size === 0 ||
              !targetPath.startsWith("/") ||
              pushMutation.isPending
            }
          >
            分發到 {selected.size} 台 VM
          </Button>
        </CardContent>
      </Card>

      {status && (
        <Card>
          <CardHeader>
            <CardTitle>分發結果</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>VMID</TableHead>
                  <TableHead>結果</TableHead>
                  <TableHead>原因</TableHead>
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
