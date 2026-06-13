import type { ColumnDef } from "@tanstack/react-table"
import type { TFunction } from "i18next"
import {
  AlertTriangle,
  Container,
  InfinityIcon,
  Loader2,
  Monitor,
  XCircle,
} from "lucide-react"

import type { VMRequestStatus } from "@/client"
import { VMActions } from "@/components/Resources/VMActions"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import useCustomToast from "@/hooks/useCustomToast"
import { cn, decodeName } from "@/lib/utils"
import {
  type DeletingMeta,
  useCancelDeletionRequest,
} from "@/services/deletingResources"
import {
  type CreatingMeta,
  type ResourceRow,
  useCancelVmRequest,
} from "@/services/pendingResources"

function StatusBadge({
  status,
  t,
}: {
  status: string
  t: TFunction<string, string>
}) {
  const isRunning = status === "running"
  const isPaused = status === "paused"

  let label: string
  if (isRunning) {
    label = t("table.running")
  } else if (isPaused) {
    label = t("table.paused")
  } else {
    label = t("table.stopped")
  }

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium",
        isRunning
          ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
          : "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-400",
      )}
    >
      <span
        className={cn(
          "w-1.5 h-1.5 rounded-full",
          isRunning ? "bg-green-500" : "bg-gray-400",
        )}
      />
      {label}
    </span>
  )
}

const CREATING_LABEL: Record<VMRequestStatus, string> = {
  pending: "等待審核",
  approved: "排程中",
  rejected: "已拒絕",
  cancelled: "已取消",
}

function CreatingStatusBadge({ meta }: { meta: CreatingMeta }) {
  // 超時判斷：start_at 已過且仍在 pending / approved（還沒進入 provisioning）
  const isOverdue =
    (meta.status === "pending" || meta.status === "approved") &&
    !!meta.start_at &&
    new Date(meta.start_at).getTime() < Date.now()

  if (isOverdue) {
    const overdueMs = Date.now() - new Date(meta.start_at as string).getTime()
    const overdueMin = Math.floor(overdueMs / 60_000)
    const overdueLabel =
      overdueMin >= 60
        ? `${Math.floor(overdueMin / 60)} 小時`
        : `${overdueMin} 分鐘`
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium",
          "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300",
        )}
        title={`排程開機時間已過 ${overdueLabel}，但仍未開始建立`}
      >
        <AlertTriangle className="h-3 w-3" />
        超時 ({overdueLabel})
      </span>
    )
  }

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium",
        "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300",
      )}
    >
      <Loader2 className="h-3 w-3 animate-spin" />
      {CREATING_LABEL[meta.status] ?? "創建中"}
    </span>
  )
}

function CreatingActions({ meta }: { meta: CreatingMeta }) {
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const cancel = useCancelVmRequest()
  // provisioning 已在跑 Proxmox clone，無法取消；只有 pending/approved 階段允許取消
  const canCancel = meta.status === "pending" || meta.status === "approved"

  return (
    <div className="flex items-center gap-1">
      <Button
        variant="ghost"
        size="sm"
        disabled={!canCancel || cancel.isPending}
        onClick={(e) => {
          e.stopPropagation()
          cancel.mutate(meta.request_id, {
            onSuccess: () => showSuccessToast("已送出取消申請"),
            onError: (err: Error) => showErrorToast(err.message),
          })
        }}
        title={canCancel ? "取消申請" : "建立流程已開始，無法取消"}
      >
        <XCircle className="h-4 w-4 mr-1" />
        取消申請
      </Button>
    </div>
  )
}

const DELETING_LABEL: Record<DeletingMeta["status"], string> = {
  pending: "等待刪除",
  running: "刪除中",
  completed: "已刪除",
  failed: "刪除失敗",
  cancelled: "已取消",
}

function DeletingStatusBadge({ meta }: { meta: DeletingMeta }) {
  const isCompleted = meta.status === "completed"
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium",
        "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300",
      )}
    >
      {!isCompleted && <Loader2 className="h-3 w-3 animate-spin" />}
      {DELETING_LABEL[meta.status] ?? "刪除中"}
    </span>
  )
}

function DeletingActions({ meta }: { meta: DeletingMeta }) {
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const cancel = useCancelDeletionRequest()
  const canCancel = meta.status === "pending" || meta.status === "running"
  const cancelTitle =
    meta.status === "pending"
      ? "取消刪除"
      : meta.status === "running"
        ? "嘗試取消（盡力而為，若 Proxmox 操作已開始可能仍會完成）"
        : "刪除已結束，無法取消"

  return (
    <div className="flex items-center gap-1">
      <Button
        variant="ghost"
        size="sm"
        disabled={!canCancel || cancel.isPending}
        onClick={(e) => {
          e.stopPropagation()
          cancel.mutate(meta.request_id, {
            onSuccess: () => showSuccessToast("已取消刪除"),
            onError: (err: Error) => showErrorToast(err.message),
          })
        }}
        title={cancelTitle}
      >
        <XCircle className="h-4 w-4 mr-1" />
        取消刪除
      </Button>
    </div>
  )
}

function TypeIcon({ type }: { type: string }) {
  if (type === "lxc") {
    return <Container className="h-4 w-4 text-blue-500" />
  }
  return <Monitor className="h-4 w-4 text-purple-500" />
}

function TypeLabel({
  type,
  t,
}: {
  type: string
  t: TFunction<string, string>
}) {
  if (type === "lxc") {
    return (
      <span className="text-xs text-muted-foreground">{t("table.lxc")}</span>
    )
  }
  return <span className="text-xs text-muted-foreground">{t("table.kvm")}</span>
}

export const createColumns = (
  t: TFunction<string, string>,
  onOpenConsole: (vmid: number, name: string, type: string) => void,
  options?: { enableSelection?: boolean },
): ColumnDef<ResourceRow>[] => {
  const cols: ColumnDef<ResourceRow>[] = []

  if (options?.enableSelection) {
    cols.push({
      id: "select",
      header: ({ table }) => (
        <Checkbox
          checked={
            table.getIsAllPageRowsSelected() ||
            (table.getIsSomePageRowsSelected() && "indeterminate")
          }
          onCheckedChange={(value) => table.toggleAllPageRowsSelected(!!value)}
          aria-label="Select all"
          onClick={(e) => e.stopPropagation()}
        />
      ),
      cell: ({ row }) => {
        // creating / deleting row 不允許被勾選
        if (row.original._creating || row.original._deleting) {
          return <span className="block w-4" aria-hidden />
        }
        return (
          <Checkbox
            checked={row.getIsSelected()}
            onCheckedChange={(value) => row.toggleSelected(!!value)}
            aria-label="Select row"
            onClick={(e) => e.stopPropagation()}
          />
        )
      },
      enableSorting: false,
      enableHiding: false,
    })
  }

  cols.push(
    {
      accessorKey: "name",
      header: t("table.nameId"),
      cell: ({ row }) => {
        const c = row.original._creating
        const d = row.original._deleting
        const isFresh =
          c?.created_at &&
          Date.now() - new Date(c.created_at).getTime() < 30_000
        const requester = c
          ? (c.user_full_name ?? c.user_email ?? c.user_id)
          : d
            ? (d.user_full_name ?? d.user_email ?? d.user_id)
            : null
        const inner = (
          <div
            className={cn(
              "flex items-center gap-3 rounded-md transition-shadow",
              c &&
                "ring-1 ring-blue-300/60 dark:ring-blue-500/40 bg-blue-50/30 dark:bg-blue-900/10",
              isFresh && "ring-2 ring-blue-400/80 animate-pulse",
              d &&
                "ring-1 ring-red-300/60 dark:ring-red-500/40 bg-red-50/30 dark:bg-red-900/10",
            )}
          >
            <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-muted">
              <TypeIcon type={row.original.type} />
            </div>
            <div className="flex flex-col">
              <span className="font-medium">
                {decodeName(row.original.name)}
              </span>
              <TypeLabel type={row.original.type} t={t} />
              {c && (c.user_full_name || c.user_email) && (
                <span className="text-xs text-muted-foreground">
                  {c.user_full_name ?? c.user_email}
                </span>
              )}
              {d && (d.user_full_name || d.user_email) && (
                <span className="text-xs text-muted-foreground">
                  {d.user_full_name ?? d.user_email}
                </span>
              )}
            </div>
          </div>
        )
        if (!c && !d) return inner
        return (
          <TooltipProvider delayDuration={200}>
            <Tooltip>
              <TooltipTrigger asChild>{inner}</TooltipTrigger>
              <TooltipContent side="right" className="text-xs">
                {c && (
                  <>
                    <div className="font-medium">申請者：{requester}</div>
                    {c.user_email && c.user_full_name && (
                      <div className="text-muted-foreground">
                        {c.user_email}
                      </div>
                    )}
                    <div className="text-muted-foreground mt-1">
                      {c.resource_type.toUpperCase()} · {c.cores} vCPU ·{" "}
                      {c.memory} MB
                    </div>
                  </>
                )}
                {d && (
                  <>
                    <div className="font-medium">刪除者：{requester}</div>
                    {d.user_email && d.user_full_name && (
                      <div className="text-muted-foreground">
                        {d.user_email}
                      </div>
                    )}
                    <div className="text-muted-foreground mt-1">
                      VMID {row.original.vmid}
                      {d.purge && " · purge"}
                      {d.force && " · force"}
                    </div>
                  </>
                )}
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )
      },
    },
    {
      accessorKey: "environment_type",
      header: t("table.type"),
      cell: ({ row }) => (
        <div className="flex flex-col">
          <span className="font-medium">
            {row.original.environment_type || t("table.notSet")}
          </span>
          {row.original.os_info && (
            <span className="text-xs text-muted-foreground">
              {row.original.os_info}
            </span>
          )}
        </div>
      ),
    },
    {
      accessorKey: "status",
      header: t("table.status"),
      cell: ({ row }) => {
        const c = row.original._creating
        if (c) return <CreatingStatusBadge meta={c} />
        const d = row.original._deleting
        if (d) return <DeletingStatusBadge meta={d} />
        return <StatusBadge status={row.original.status} t={t} />
      },
    },
    {
      accessorKey: "expiry_date",
      header: t("table.expiryDate"),
      cell: ({ row }) => {
        if (row.original._creating || row.original._deleting) {
          return <span className="text-xs text-muted-foreground">—</span>
        }
        if (!row.original.expiry_date) {
          return (
            <div className="flex items-center gap-1.5 text-blue-600 dark:text-blue-400">
              <InfinityIcon className="h-4 w-4" />
              <span className="font-medium">{t("table.noExpiry")}</span>
            </div>
          )
        }
        return (
          <span className="text-sm">
            {new Date(row.original.expiry_date).toLocaleDateString("zh-TW")}
          </span>
        )
      },
    },
    {
      accessorKey: "ip_address",
      header: t("table.ipAddress"),
      cell: ({ row }) => (
        <span className="font-mono text-sm">
          {row.original._creating || row.original._deleting
            ? "—"
            : row.original.ip_address || "N/A"}
        </span>
      ),
    },
    {
      id: "actions",
      header: t("table.actions"),
      cell: ({ row }) => {
        const c = row.original._creating
        if (c) return <CreatingActions meta={c} />
        const d = row.original._deleting
        if (d) return <DeletingActions meta={d} />
        if (row.original.vmid == null) {
          return <span className="text-muted-foreground">-</span>
        }
        return (
          <VMActions
            vmid={row.original.vmid}
            name={row.original.name}
            type={row.original.type}
            status={row.original.status}
            onOpenConsole={onOpenConsole}
          />
        )
      },
    },
  )

  return cols
}

const noopT = ((key: string) => key) as TFunction

export const columns: ColumnDef<ResourceRow>[] = createColumns(noopT, () => {})
