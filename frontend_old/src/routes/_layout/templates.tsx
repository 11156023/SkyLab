import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import {
  AlertTriangle,
  BookCopy,
  Copy,
  Loader2,
  MoreHorizontal,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react"
import { useMemo, useState } from "react"
import { toast } from "sonner"

import type { ApiError, VmTemplatePublic } from "@/client"
import { TemplateStatusBadge } from "@/components/Templates/TemplateBadges"
import { TemplateCloneDialog } from "@/components/Templates/TemplateCloneDialog"
import { TemplateFormDialog } from "@/components/Templates/TemplateFormDialog"
import { TemplateTasksCard } from "@/components/Templates/TemplateTasksCard"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import useAuth from "@/hooks/useAuth"
import { cn } from "@/lib/utils"
import { TemplatesAPI } from "@/services/templates"

export const Route = createFileRoute("/_layout/templates")({
  component: TemplatesPage,
  head: () => ({
    meta: [{ title: "範本 - SkyLab" }],
  }),
})

function errorMessage(error: unknown, fallback: string) {
  const apiError = error as ApiError & { body?: { detail?: string } }
  return apiError.body?.detail ?? apiError.message ?? fallback
}

function TemplatesPage() {
  const { user } = useAuth()
  const canManage =
    user?.role === "admin" ||
    user?.role === "teacher" ||
    user?.is_superuser === true

  const [createOpen, setCreateOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<VmTemplatePublic | null>(null)
  const [cloneTarget, setCloneTarget] = useState<VmTemplatePublic | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<VmTemplatePublic | null>(
    null,
  )

  const queryClient = useQueryClient()
  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ["templates"],
    queryFn: () => TemplatesAPI.list(),
    refetchInterval: (query) => {
      const templates = query.state.data?.data ?? []
      return templates.some(
        (t) => t.status === "creating" || t.status === "updating",
      )
        ? 4000
        : 30000
    },
  })
  const templates = data?.data ?? []

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["templates"] })
    queryClient.invalidateQueries({ queryKey: ["template-tasks"] })
  }

  const cycleMutation = useMutation({
    mutationFn: ({
      templateId,
      action,
    }: {
      templateId: string
      action: "start" | "finish" | "cancel"
    }) => {
      if (action === "start") return TemplatesAPI.startUpdateCycle(templateId)
      if (action === "finish") return TemplatesAPI.finishUpdateCycle(templateId)
      return TemplatesAPI.cancelUpdateCycle(templateId)
    },
    onSuccess: (_res, vars) => {
      toast.success(
        vars.action === "start"
          ? "已開始更新循環：系統正在複製一台暫存母機，完成後會出現在你的資源列表，修改完再回到此頁按「完成更新」"
          : vars.action === "finish"
            ? "正在把暫存母機轉為新版範本"
            : "已取消更新循環，暫存母機將被回收",
      )
      invalidate()
    },
    onError: (error: unknown) => {
      toast.error(errorMessage(error, "操作失敗"))
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (templateId: string) => TemplatesAPI.remove(templateId),
    onSuccess: () => {
      toast.success("刪除任務已送出")
      setDeleteTarget(null)
      invalidate()
    },
    onError: (error: unknown) => {
      toast.error(errorMessage(error, "刪除範本失敗"))
      setDeleteTarget(null)
    },
  })

  const readyTemplates = useMemo(
    () => templates.filter((t) => t.status === "ready"),
    [templates],
  )

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
            <BookCopy className="h-6 w-6 text-sky-500" />
            範本
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {canManage
              ? "把設定好的母機轉為範本，學生即可一鍵克隆出自己的環境。"
              : "從老師提供的範本一鍵克隆出自己的環境，開好即用。"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => refetch()}
            disabled={isFetching}
          >
            <RefreshCw
              className={cn("mr-1.5 h-3.5 w-3.5", isFetching && "animate-spin")}
            />
            重新整理
          </Button>
          {canManage && (
            <Button size="sm" onClick={() => setCreateOpen(true)}>
              <Plus className="mr-1.5 h-4 w-4" />從 VM 建立範本
            </Button>
          )}
        </div>
      </header>

      {isLoading ? (
        <Card>
          <CardContent className="flex items-center justify-center py-12 text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            載入範本中...
          </CardContent>
        </Card>
      ) : canManage ? (
        <ManagementTable
          templates={templates}
          onEdit={setEditTarget}
          onClone={setCloneTarget}
          onDelete={setDeleteTarget}
          onCycle={(templateId, action) =>
            cycleMutation.mutate({ templateId, action })
          }
          cyclePending={cycleMutation.isPending}
        />
      ) : (
        <StudentCatalog templates={readyTemplates} onClone={setCloneTarget} />
      )}

      <TemplateTasksCard />

      <TemplateFormDialog open={createOpen} onOpenChange={setCreateOpen} />
      <TemplateFormDialog
        open={editTarget !== null}
        onOpenChange={(open) => !open && setEditTarget(null)}
        template={editTarget}
      />
      <TemplateCloneDialog
        open={cloneTarget !== null}
        onOpenChange={(open) => !open && setCloneTarget(null)}
        template={cloneTarget}
        canBatch={canManage}
      />

      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              刪除範本「{deleteTarget?.name}」？
            </AlertDialogTitle>
            <AlertDialogDescription>
              PVE 端的範本磁碟會一併刪除，動作無法復原。
              如果還有從此範本克隆出的機器（linked clone），系統會拒絕刪除。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              className="bg-red-600 text-white hover:bg-red-700"
              onClick={() =>
                deleteTarget && deleteMutation.mutate(deleteTarget.id)
              }
            >
              {deleteMutation.isPending && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              確認刪除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function ManagementTable({
  templates,
  onEdit,
  onClone,
  onDelete,
  onCycle,
  cyclePending,
}: {
  templates: VmTemplatePublic[]
  onEdit: (t: VmTemplatePublic) => void
  onClone: (t: VmTemplatePublic) => void
  onDelete: (t: VmTemplatePublic) => void
  onCycle: (templateId: string, action: "start" | "finish" | "cancel") => void
  cyclePending: boolean
}) {
  if (templates.length === 0) {
    return (
      <Card>
        <CardContent className="py-12 text-center text-sm text-muted-foreground">
          還沒有任何範本。先準備好一台母機（裝好系統與課程環境），
          再點右上角「從 VM 建立範本」。
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardContent className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>名稱</TableHead>
              <TableHead className="w-24">VMID</TableHead>
              <TableHead className="w-20">類型</TableHead>
              <TableHead className="w-28">狀態</TableHead>
              <TableHead className="w-24">可見範圍</TableHead>
              <TableHead className="w-16">版本</TableHead>
              <TableHead className="w-12" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {templates.map((template) => (
              <TableRow key={template.id}>
                <TableCell>
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{template.name}</span>
                    {template.pve_exists === false && (
                      <span
                        className="flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400"
                        title="PVE 端找不到這個範本，可能已被手動刪除"
                      >
                        <AlertTriangle className="h-3.5 w-3.5" />
                        PVE 不存在
                      </span>
                    )}
                  </div>
                  {template.description && (
                    <p className="mt-0.5 max-w-md truncate text-xs text-muted-foreground">
                      {template.description}
                    </p>
                  )}
                  {template.error_message && (
                    <p className="mt-0.5 max-w-md truncate text-xs text-red-600 dark:text-red-400">
                      {template.error_message}
                    </p>
                  )}
                </TableCell>
                <TableCell className="font-mono text-sm">
                  {template.pve_vmid}
                </TableCell>
                <TableCell>
                  <Badge variant="outline">{template.resource_type}</Badge>
                </TableCell>
                <TableCell>
                  <TemplateStatusBadge status={template.status} />
                </TableCell>
                <TableCell className="text-sm">
                  {template.visibility === "global"
                    ? "全域"
                    : `${template.group_ids?.length ?? 0} 個群組`}
                </TableCell>
                <TableCell className="text-sm">v{template.version}</TableCell>
                <TableCell>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="icon" className="h-8 w-8">
                        <MoreHorizontal className="h-4 w-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem
                        disabled={template.status !== "ready"}
                        onClick={() => onClone(template)}
                      >
                        <Copy className="mr-2 h-4 w-4" />
                        克隆開通
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => onEdit(template)}>
                        <Pencil className="mr-2 h-4 w-4" />
                        編輯 / 可見範圍
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                      {template.status === "ready" && (
                        <DropdownMenuItem
                          disabled={cyclePending}
                          onClick={() => onCycle(template.id, "start")}
                        >
                          <RefreshCw className="mr-2 h-4 w-4" />
                          開始更新循環
                        </DropdownMenuItem>
                      )}
                      {template.status === "updating" && (
                        <>
                          <DropdownMenuItem
                            disabled={cyclePending}
                            onClick={() => onCycle(template.id, "finish")}
                          >
                            <RefreshCw className="mr-2 h-4 w-4" />
                            完成更新（轉為新版）
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            disabled={cyclePending}
                            onClick={() => onCycle(template.id, "cancel")}
                          >
                            取消更新循環
                          </DropdownMenuItem>
                        </>
                      )}
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        className="text-red-600 focus:text-red-600 dark:text-red-400"
                        onClick={() => onDelete(template)}
                      >
                        <Trash2 className="mr-2 h-4 w-4" />
                        刪除範本
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

function StudentCatalog({
  templates,
  onClone,
}: {
  templates: VmTemplatePublic[]
  onClone: (t: VmTemplatePublic) => void
}) {
  if (templates.length === 0) {
    return (
      <Card>
        <CardContent className="py-12 text-center text-sm text-muted-foreground">
          目前沒有可用的範本。老師發布範本後，就會出現在這裡。
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {templates.map((template) => (
        <Card key={template.id} className="flex flex-col">
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <BookCopy className="h-4 w-4 shrink-0 text-sky-500" />
              <span className="truncate">{template.name}</span>
            </CardTitle>
            {template.description && (
              <CardDescription className="line-clamp-2">
                {template.description}
              </CardDescription>
            )}
          </CardHeader>
          <CardContent className="mt-auto space-y-3 pt-2">
            <div className="flex flex-wrap gap-1.5 text-xs text-muted-foreground">
              <Badge variant="outline">{template.resource_type}</Badge>
              {template.default_cores && (
                <Badge variant="outline">{template.default_cores} 核</Badge>
              )}
              {template.default_memory && (
                <Badge variant="outline">
                  {Math.round(template.default_memory / 1024)} GB RAM
                </Badge>
              )}
              {template.default_disk && (
                <Badge variant="outline">{template.default_disk} GB 磁碟</Badge>
              )}
              <Badge variant="outline">v{template.version}</Badge>
            </div>
            <Button className="w-full" onClick={() => onClone(template)}>
              <Copy className="mr-2 h-4 w-4" />
              一鍵克隆
            </Button>
          </CardContent>
        </Card>
      ))}
    </div>
  )
}
