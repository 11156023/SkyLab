import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import {
  AlertTriangle,
  CheckCircle2,
  Cpu,
  Monitor,
  RefreshCw,
  Server,
  Trash2,
  XCircle,
} from "lucide-react"
import { useState } from "react"
import { useTranslation } from "react-i18next"

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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import useAuth from "@/hooks/useAuth"
import { queryKeys } from "@/lib/queryKeys"
import {
  type GPUMappingDetail,
  type GPUUsageInfo,
  GpuService,
} from "@/services/gpu"

export const Route = createFileRoute("/_layout/gpu-management")({
  component: GPUManagementPage,
})

function StatusBadge({ mapping }: { mapping: GPUMappingDetail }) {
  if (mapping.available_count > 0 && mapping.used_count === 0) {
    return (
      <Badge
        variant="outline"
        className="border-green-500/30 bg-green-500/10 text-green-600 dark:text-green-400"
      >
        <CheckCircle2 className="mr-1 h-3 w-3" />
        可用
      </Badge>
    )
  }

  return (
    <Badge
      variant="outline"
      className="border-orange-500/30 bg-orange-500/10 text-orange-600 dark:text-orange-400"
    >
      <Monitor className="mr-1 h-3 w-3" />
      {mapping.used_count}/{mapping.device_count} 使用中
    </Badge>
  )
}

function VMUsageList({ usedBy }: { usedBy: GPUUsageInfo[] }) {
  if (usedBy.length === 0) {
    return <span className="text-sm text-muted-foreground">—</span>
  }

  return (
    <div className="flex flex-wrap gap-1.5">
      {usedBy.map((vm) => (
        <TooltipProvider key={vm.vmid}>
          <Tooltip>
            <TooltipTrigger asChild>
              <Badge variant="secondary" className="text-xs">
                <Monitor className="mr-1 h-3 w-3" />
                {vm.vm_name || `VM ${vm.vmid}`}
                <span
                  className={`ml-1 inline-block h-1.5 w-1.5 rounded-full ${vm.status === "running" ? "bg-green-500" : "bg-gray-400"}`}
                />
              </Badge>
            </TooltipTrigger>
            <TooltipContent>
              <div className="text-xs">
                <p>VMID: {vm.vmid}</p>
                <p>節點: {vm.node}</p>
                <p>狀態: {vm.status}</p>
              </div>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      ))}
    </div>
  )
}

function GPUManagementPage() {
  const { t } = useTranslation(["common"])
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const isAdmin = user?.role === "admin" || user?.is_superuser

  const [deleteTarget, setDeleteTarget] = useState<GPUMappingDetail | null>(
    null,
  )

  const {
    data: mappingsData,
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: queryKeys.gpu.mappings,
    queryFn: () => GpuService.listMappings(),
  })

  const deleteMutation = useMutation({
    mutationFn: (mappingId: string) => GpuService.deleteMapping(mappingId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.gpu.mappings })
      setDeleteTarget(null)
    },
  })

  const mappings = mappingsData?.data ?? []
  const totalSlots = mappings.reduce((s, m) => s + m.device_count, 0)
  const usedSlots = mappings.reduce((s, m) => s + m.used_count, 0)
  const availableSlots = mappings.reduce((s, m) => s + m.available_count, 0)
  const totalVramMb = mappings.reduce((s, m) => s + m.total_vram_mb, 0)
  const usedVramMb = mappings.reduce((s, m) => s + m.used_vram_mb, 0)

  const formatVram = (mb: number) => {
    if (mb <= 0) return "—"
    if (mb >= 1024) return `${(mb / 1024).toFixed(mb % 1024 === 0 ? 0 : 1)} GB`
    return `${mb} MB`
  }

  return (
    <div className="mx-auto max-w-300 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">GPU 管理</h1>
          <p className="text-muted-foreground">
            管理 PCI 資源映射（GPU），查看使用狀態與分配情形
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => refetch()}
          disabled={isLoading}
        >
          <RefreshCw
            className={`mr-2 h-4 w-4 ${isLoading ? "animate-spin" : ""}`}
          />
          {t("common:buttons.refresh", "重新整理")}
        </Button>
      </div>

      {/* Stats Cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">可分配插槽</CardTitle>
            <Cpu className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{totalSlots}</div>
            <p className="text-xs text-muted-foreground">
              {mappings.length} 個映射，
              {mappings.reduce((s, m) => s + m.physical_gpu_count, 0)} 張實體
              GPU
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">使用中</CardTitle>
            <Monitor className="h-4 w-4 text-orange-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-orange-600">
              {usedSlots}
            </div>
            <p className="text-xs text-muted-foreground">
              {availableSlots} 個插槽可用
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">總 VRAM</CardTitle>
            <Cpu className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{formatVram(totalVramMb)}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">已分配 VRAM</CardTitle>
            <Monitor className="h-4 w-4 text-orange-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-orange-600">
              {formatVram(usedVramMb)}
            </div>
            {totalVramMb > 0 && (
              <p className="text-xs text-muted-foreground">
                {((usedVramMb / totalVramMb) * 100).toFixed(0)}% 使用率
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* GPU Table */}
      <Card>
        <CardHeader>
          <CardTitle>GPU 資源映射列表</CardTitle>
          <CardDescription>
            顯示所有已設定的 PCI 資源映射，以及各 GPU 的使用情形
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex items-center justify-center py-12 text-muted-foreground">
              <RefreshCw className="mr-2 h-5 w-5 animate-spin" />
              載入中...
            </div>
          ) : isError ? (
            <div className="flex flex-col items-center justify-center gap-2 py-12 text-destructive">
              <XCircle className="h-6 w-6" />
              <p>載入 GPU 資料失敗</p>
              <Button variant="outline" size="sm" onClick={() => refetch()}>
                重試
              </Button>
            </div>
          ) : mappings.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-2 py-12 text-muted-foreground">
              <AlertTriangle className="h-6 w-6" />
              <p>尚未設定任何 PCI 資源映射</p>
              <p className="text-xs">
                請先在 Proxmox 叢集設定中建立 PCI Hardware Mapping
              </p>
            </div>
          ) : (
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-45">名稱</TableHead>
                    <TableHead className="w-60">說明 / 型號</TableHead>
                    <TableHead className="w-20">類型</TableHead>
                    <TableHead className="w-30">VRAM</TableHead>
                    <TableHead className="w-30">節點</TableHead>
                    <TableHead className="w-25">狀態</TableHead>
                    <TableHead>使用的 VM</TableHead>
                    {isAdmin && (
                      <TableHead className="w-20 text-right">操作</TableHead>
                    )}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {mappings.map((mapping) => {
                    const nodes = [
                      ...new Set(
                        mapping.maps.map((m) => m.node).filter(Boolean),
                      ),
                    ]
                    return (
                      <TableRow key={mapping.id}>
                        <TableCell className="font-medium">
                          <div className="flex items-center gap-2">
                            <Cpu className="h-4 w-4 text-primary" />
                            {mapping.id}
                          </div>
                        </TableCell>
                        <TableCell>
                          <span className="text-sm">
                            {mapping.description || "—"}
                          </span>
                        </TableCell>
                        <TableCell>
                          <div className="flex flex-wrap gap-1">
                            {mapping.has_mdev ? (
                              <Badge variant="secondary" className="text-xs">
                                vGPU
                              </Badge>
                            ) : (
                              <Badge variant="outline" className="text-xs">
                                Passthrough
                              </Badge>
                            )}
                            {mapping.is_sriov && (
                              <Badge
                                variant="outline"
                                className="text-xs border-blue-500/30 text-blue-600 dark:text-blue-400"
                              >
                                SR-IOV
                              </Badge>
                            )}
                          </div>
                        </TableCell>
                        <TableCell>
                          {mapping.total_vram_mb > 0 ? (
                            <div className="text-sm">
                              <span className="font-medium">
                                {formatVram(mapping.used_vram_mb)}
                              </span>
                              <span className="text-muted-foreground">
                                {" "}
                                / {formatVram(mapping.total_vram_mb)}
                              </span>
                            </div>
                          ) : (
                            <span className="text-sm text-muted-foreground">
                              —
                            </span>
                          )}
                        </TableCell>
                        <TableCell>
                          <div className="flex flex-wrap gap-1">
                            {nodes.map((node) => (
                              <Badge
                                key={node}
                                variant="outline"
                                className="text-xs"
                              >
                                <Server className="mr-1 h-3 w-3" />
                                {node}
                              </Badge>
                            ))}
                          </div>
                        </TableCell>
                        <TableCell>
                          <StatusBadge mapping={mapping} />
                        </TableCell>
                        <TableCell>
                          <VMUsageList usedBy={mapping.used_by} />
                        </TableCell>
                        {isAdmin && (
                          <TableCell className="text-right">
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8 text-destructive hover:text-destructive"
                              onClick={() => setDeleteTarget(mapping)}
                              disabled={mapping.used_count > 0}
                              title={
                                mapping.used_count > 0
                                  ? "使用中的映射無法刪除"
                                  : "刪除映射"
                              }
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </TableCell>
                        )}
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Delete Confirmation Dialog */}
      <Dialog
        open={!!deleteTarget}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>確認刪除 GPU 映射</DialogTitle>
            <DialogDescription>
              確定要刪除 GPU 映射「{deleteTarget?.id}」嗎？此操作無法復原。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>
              取消
            </Button>
            <Button
              variant="destructive"
              onClick={() =>
                deleteTarget && deleteMutation.mutate(deleteTarget.id)
              }
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? "刪除中..." : "確認刪除"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
