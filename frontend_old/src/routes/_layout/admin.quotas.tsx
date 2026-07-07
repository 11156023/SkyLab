import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { Trash2 } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"
import { GroupsService } from "@/client"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
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
import { requireAdminUser } from "@/features/auth/guards"
import { QuotaAPI } from "@/services/quotas"

export const Route = createFileRoute("/_layout/admin/quotas")({
  component: AdminQuotasPage,
  beforeLoad: () => requireAdminUser(),
  head: () => ({ meta: [{ title: "配額管理 - SkyLab" }] }),
})

const EMPTY_FORM = {
  scope: "group" as "group" | "user",
  target: "",
  max_cpu_cores: 8,
  max_memory_mb: 16384,
  max_disk_gb: 100,
  max_instances: 5,
}

function AdminQuotasPage() {
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [form, setForm] = useState(EMPTY_FORM)

  const { data: quotas } = useQuery({
    queryKey: ["quotas"],
    queryFn: () => QuotaAPI.list(),
  })

  const { data: groups } = useQuery({
    queryKey: ["quota-groups"],
    queryFn: async () => (await GroupsService.listGroups()).data ?? [],
  })

  const createMutation = useMutation({
    mutationFn: () =>
      QuotaAPI.create({
        scope: form.scope,
        group_id: form.scope === "group" ? form.target : null,
        user_id: form.scope === "user" ? form.target : null,
        max_cpu_cores: form.max_cpu_cores,
        max_memory_mb: form.max_memory_mb,
        max_disk_gb: form.max_disk_gb,
        max_instances: form.max_instances,
      }),
    onSuccess: () => {
      toast.success("配額已建立")
      setDialogOpen(false)
      setForm(EMPTY_FORM)
      queryClient.invalidateQueries({ queryKey: ["quotas"] })
    },
    onError: (err: unknown) => {
      const detail = (err as { body?: { detail?: string } })?.body?.detail
      toast.error(detail || "建立失敗")
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => QuotaAPI.remove(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["quotas"] }),
    onError: (err: unknown) => {
      const detail = (err as { body?: { detail?: string } })?.body?.detail
      toast.error(detail || "刪除失敗")
    },
  })

  const numberInput = (key: keyof typeof EMPTY_FORM, label: string) => (
    <div className="space-y-1">
      <Label>{label}</Label>
      <Input
        type="number"
        value={form[key] as number}
        onChange={(e) => setForm({ ...form, [key]: Number(e.target.value) })}
      />
    </div>
  )

  return (
    <div className="container mx-auto p-6 space-y-6">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>資源配額管理</CardTitle>
          <Button onClick={() => setDialogOpen(true)}>新增配額</Button>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>範圍</TableHead>
                <TableHead>對象</TableHead>
                <TableHead>CPU</TableHead>
                <TableHead>記憶體 (MB)</TableHead>
                <TableHead>磁碟 (GB)</TableHead>
                <TableHead>台數</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(quotas ?? []).map((q) => (
                <TableRow key={q.id}>
                  <TableCell>
                    {q.scope === "group" ? "群組" : "個人覆寫"}
                  </TableCell>
                  <TableCell>{q.group_name ?? q.user_email ?? "-"}</TableCell>
                  <TableCell>{q.max_cpu_cores}</TableCell>
                  <TableCell>{q.max_memory_mb}</TableCell>
                  <TableCell>{q.max_disk_gb}</TableCell>
                  <TableCell>{q.max_instances}</TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => deleteMutation.mutate(q.id)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {(quotas ?? []).length === 0 && (
            <div className="text-center py-8 text-muted-foreground">
              尚未設定任何配額（未設定者套用內建預設：8 cores / 16 GB / 100 GB /
              5 台）
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>新增配額</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label>範圍</Label>
              <Select
                value={form.scope}
                onValueChange={(v) =>
                  setForm({ ...form, scope: v as "group" | "user", target: "" })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="group">群組預設</SelectItem>
                  <SelectItem value="user">個人覆寫</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {form.scope === "group" ? (
              <div className="space-y-1">
                <Label>群組</Label>
                <Select
                  value={form.target}
                  onValueChange={(v) => setForm({ ...form, target: v })}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="選擇群組" />
                  </SelectTrigger>
                  <SelectContent>
                    {(groups ?? []).map((g) => (
                      <SelectItem key={g.id} value={g.id}>
                        {g.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            ) : (
              <div className="space-y-1">
                <Label>使用者 ID</Label>
                <Input
                  value={form.target}
                  onChange={(e) => setForm({ ...form, target: e.target.value })}
                  placeholder="使用者 UUID"
                />
              </div>
            )}
            <div className="grid grid-cols-2 gap-3">
              {numberInput("max_cpu_cores", "CPU cores")}
              {numberInput("max_memory_mb", "記憶體 (MB)")}
              {numberInput("max_disk_gb", "磁碟 (GB)")}
              {numberInput("max_instances", "實例數")}
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              取消
            </Button>
            <Button
              onClick={() => createMutation.mutate()}
              disabled={!form.target || createMutation.isPending}
            >
              建立
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
