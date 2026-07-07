import {
  CheckCircle2,
  Loader2,
  type LucideIcon,
  Pause,
  RotateCw,
  Sparkles,
  XCircle,
} from "lucide-react"

import { cn } from "@/lib/utils"
import type { JobItem, JobKind, JobStatus } from "@/services/jobs"

const STATUS_META: Record<
  JobStatus,
  { label: string; icon: LucideIcon; cls: string }
> = {
  pending: { label: "等待中", icon: Pause, cls: "text-muted-foreground" },
  running: {
    label: "執行中",
    icon: Loader2,
    cls: "text-blue-600 dark:text-blue-400 animate-spin",
  },
  completed: {
    label: "已完成",
    icon: CheckCircle2,
    cls: "text-emerald-600 dark:text-emerald-400",
  },
  failed: {
    label: "失敗",
    icon: XCircle,
    cls: "text-rose-600 dark:text-rose-400",
  },
  blocked: {
    label: "受阻",
    icon: XCircle,
    cls: "text-amber-600 dark:text-amber-400",
  },
  cancelled: { label: "已取消", icon: XCircle, cls: "text-muted-foreground" },
}

const KIND_LABEL: Record<JobKind, string> = {
  migration: "遷移",
  script_deploy: "部署",
  vm_request: "開機申請",
  spec_change: "規格變更",
  deletion: "刪除",
}

function fmtTime(iso: string) {
  try {
    return new Intl.DateTimeFormat("zh-TW", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(iso))
  } catch {
    return iso
  }
}

export function JobRow({
  job,
  compact = false,
  onClick,
}: {
  job: JobItem
  compact?: boolean
  onClick?: (job: JobItem) => void
}) {
  const meta = STATUS_META[job.status]
  const Icon = meta.icon
  const showProgress =
    job.progress !== null &&
    job.status !== "completed" &&
    job.status !== "cancelled" &&
    job.status !== "failed"
  const clickable = typeof onClick === "function"

  return (
    <button
      type="button"
      onClick={clickable ? () => onClick?.(job) : undefined}
      disabled={!clickable}
      className={cn(
        "flex w-full items-start gap-3 rounded-md p-2 text-left transition-colors",
        clickable
          ? "cursor-pointer hover:bg-accent/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          : "cursor-default",
        compact ? "py-2" : "py-3",
      )}
    >
      <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", meta.cls)} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            {KIND_LABEL[job.kind]}
          </span>
          <span
            className={cn(
              "truncate text-sm font-medium text-foreground",
              compact && "text-[13px]",
            )}
            title={job.title}
          >
            {job.title}
          </span>
        </div>
        {job.message && (
          <div
            className="mt-0.5 truncate text-xs text-muted-foreground"
            title={job.message}
          >
            {job.message}
          </div>
        )}
        {showProgress && (
          <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-muted">
            <div
              className={cn(
                "h-full rounded-full transition-all",
                job.status === "blocked" ? "bg-amber-500" : "bg-blue-500",
              )}
              style={{ width: `${job.progress ?? 5}%` }}
            />
          </div>
        )}
        <div className="mt-1 flex items-center justify-between text-[11px] text-muted-foreground">
          <span>{meta.label}</span>
          <span>{fmtTime(job.updated_at)}</span>
        </div>
      </div>
    </button>
  )
}

export function JobEmpty({ message = "目前沒有任務" }: { message?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-10 text-center text-muted-foreground">
      <Sparkles className="h-6 w-6 opacity-50" />
      <span className="text-sm">{message}</span>
    </div>
  )
}

export function JobLoading() {
  return (
    <div className="flex items-center justify-center gap-2 px-6 py-8 text-muted-foreground">
      <RotateCw className="h-4 w-4 animate-spin" />
      <span className="text-sm">載入中…</span>
    </div>
  )
}

export const JOB_KIND_LABEL = KIND_LABEL
export const JOB_STATUS_META = STATUS_META
