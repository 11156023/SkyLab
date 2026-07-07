import type { TaskRecordStatus, VmTemplateStatus } from "@/client"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

export const TEMPLATE_STATUS_LABEL: Record<VmTemplateStatus, string> = {
  creating: "建立中",
  ready: "就緒",
  updating: "更新循環中",
  failed: "失敗",
  deleted: "已刪除",
}

const TEMPLATE_STATUS_CLASS: Record<VmTemplateStatus, string> = {
  creating: "border-sky-500/30 bg-sky-500/10 text-sky-600 dark:text-sky-400",
  ready:
    "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  updating:
    "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400",
  failed: "border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400",
  deleted: "border-border bg-muted text-muted-foreground",
}

export function TemplateStatusBadge({ status }: { status: VmTemplateStatus }) {
  return (
    <Badge variant="outline" className={cn(TEMPLATE_STATUS_CLASS[status])}>
      {TEMPLATE_STATUS_LABEL[status] ?? status}
    </Badge>
  )
}

export const TASK_STATUS_LABEL: Record<TaskRecordStatus, string> = {
  queued: "排隊中",
  running: "執行中",
  succeeded: "成功",
  failed: "失敗",
}

const TASK_STATUS_CLASS: Record<TaskRecordStatus, string> = {
  queued: "border-border bg-muted text-muted-foreground",
  running: "border-sky-500/30 bg-sky-500/10 text-sky-600 dark:text-sky-400",
  succeeded:
    "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  failed: "border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400",
}

export function TaskStatusBadge({ status }: { status: TaskRecordStatus }) {
  return (
    <Badge variant="outline" className={cn(TASK_STATUS_CLASS[status])}>
      {TASK_STATUS_LABEL[status] ?? status}
    </Badge>
  )
}

export function TaskProgressBar({
  progress,
  status,
}: {
  progress: number
  status: TaskRecordStatus
}) {
  const value =
    status === "succeeded" ? 100 : Math.max(0, Math.min(100, progress))
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
      <div
        className={cn(
          "h-full rounded-full transition-all",
          status === "failed" ? "bg-red-500" : "bg-sky-500",
        )}
        style={{ width: `${value}%` }}
      />
    </div>
  )
}
