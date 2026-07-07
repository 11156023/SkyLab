import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "@tanstack/react-router"
import { Bell, ChevronRight } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import useAuth from "@/hooks/useAuth"
import { cn } from "@/lib/utils"
import {
  connectJobsWebSocket,
  type JobItem,
  type JobStatus,
  JobsAPI,
  type JobsListResponse,
} from "@/services/jobs"
import { JobDetailDialog } from "./JobDetailDialog"
import { JOB_KIND_LABEL, JobEmpty, JobLoading, JobRow } from "./JobRow"

const NOTIFY_ONLY_MINE_KEY = "jobs:notifyOnlyMine"
const RUNNING_STATUSES: ReadonlySet<JobStatus> = new Set(["running"])

// 從 running/pending/blocked → 終態時觸發 toast
const TERMINAL_STATUSES: ReadonlySet<JobStatus> = new Set([
  "completed",
  "failed",
  "blocked",
  "cancelled",
])

function notifyJobTransition(
  job: JobItem,
  prevStatus: JobStatus | undefined,
  onView: (id: string) => void,
) {
  // 第一次看到（prev undefined）且本來就是終態 → 不通知（避免重整時轟炸）
  if (prevStatus === undefined) return
  if (prevStatus === job.status) return
  if (!TERMINAL_STATUSES.has(job.status)) return

  const kindLabel = JOB_KIND_LABEL[job.kind]
  const action = {
    label: "檢視",
    onClick: () => onView(job.id),
  }
  const description = job.title

  switch (job.status) {
    case "completed":
      toast.success(`${kindLabel}已完成`, { description, action })
      break
    case "failed":
      toast.error(`${kindLabel}失敗`, {
        description: job.message ?? description,
        action,
      })
      break
    case "blocked":
      toast.warning(`${kindLabel}受阻`, {
        description: job.message ?? description,
        action,
      })
      break
    case "cancelled":
      toast(`${kindLabel}已取消`, { description, action })
      break
  }
}

export function JobsButton() {
  const [open, setOpen] = useState(false)
  const [focusJobId, setFocusJobId] = useState<string | null>(null)
  const [notifyOnlyMine, setNotifyOnlyMine] = useState<boolean>(() => {
    if (typeof window === "undefined") return false
    return window.localStorage.getItem(NOTIFY_ONLY_MINE_KEY) === "1"
  })
  const ref = useRef<HTMLDivElement | null>(null)
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const isAdmin = Boolean(
    user?.is_superuser ||
      (user as { role?: string } | undefined)?.role === "admin",
  )
  const myUserId = user?.id ?? null
  // 使用 ref 送進 WS callback，避免 closure 抓舊設定導致 effect 重連
  const filterRef = useRef({ enabled: false, myUserId: null as string | null })
  filterRef.current = {
    enabled: notifyOnlyMine && isAdmin,
    myUserId,
  }
  // 上一次 WS snapshot 中各 job 的狀態，用於 diff 觸發 toast
  const prevStatusMapRef = useRef<Map<string, JobStatus> | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ["jobs", "running"],
    queryFn: () =>
      JobsAPI.list({ statuses: ["running"], limit: 200, history_days: 30 }),
    refetchInterval: 15000, // WS 為主，REST 作為 fallback
    staleTime: 5000,
  })

  // WebSocket 即時推送
  useEffect(() => {
    const token = localStorage.getItem("access_token")
    if (!token) return
    const dispose = connectJobsWebSocket(token, (snapshot) => {
      // popover 只要 running
      const runningOnly = snapshot.items.filter((j) =>
        RUNNING_STATUSES.has(j.status),
      )
      const runningPayload: JobsListResponse = {
        items: runningOnly,
        total: runningOnly.length,
        active_count: snapshot.active_count,
      }
      queryClient.setQueryData(["jobs", "running"], runningPayload)
      // banner 仍在讀舊 key，一併同步
      queryClient.setQueryData(["jobs", "recent", 5], snapshot)
      // 同步通知其他可能訂閱的 query
      queryClient.invalidateQueries({ queryKey: ["jobs", "list"] })

      // ── Diff: 比對上一次 snapshot 的狀態，發 toast ──
      const prev = prevStatusMapRef.current
      const next = new Map<string, JobStatus>()
      for (const j of snapshot.items) {
        next.set(j.id, j.status)
      }
      // 只在已建立 baseline 後才比對（首次連線當下視為基準，不要彈通知）
      if (prev !== null) {
        const { enabled, myUserId } = filterRef.current
        for (const j of snapshot.items) {
          // admin 開「只通知自己」：跳過非本人的 job
          if (enabled && j.user_id !== myUserId) continue
          notifyJobTransition(j, prev.get(j.id), setFocusJobId)
        }
      }
      prevStatusMapRef.current = next
    })
    return dispose
  }, [queryClient])

  // 點擊外部關閉
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener("mousedown", onDown)
    return () => document.removeEventListener("mousedown", onDown)
  }, [open])

  const items = data?.items ?? []
  const runningCount = items.length

  return (
    <div ref={ref} className="relative">
      <Button
        variant="outline"
        size="sm"
        className={cn(
          "h-8 gap-2 rounded-full border-blue-200 bg-white px-3 text-xs font-medium shadow-xs hover:bg-blue-50",
          "dark:border-blue-900/60 dark:bg-background dark:hover:bg-blue-950/30",
        )}
        onClick={() => setOpen((v) => !v)}
        aria-label="背景任務"
      >
        <Bell className="h-3.5 w-3.5" />
        <span>任務</span>
        {runningCount > 0 && (
          <span className="ml-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-blue-600 px-1 text-[10px] font-semibold text-white">
            {runningCount > 99 ? "99+" : runningCount}
          </span>
        )}
      </Button>

      {open && (
        <div
          className={cn(
            "absolute right-0 top-full z-50 mt-2 w-90 origin-top-right rounded-lg border border-border bg-popover p-2 shadow-lg",
          )}
        >
          <div className="flex items-center justify-between px-2 py-1.5">
            <div className="text-sm font-semibold">執行中任務</div>
            <div className="text-[11px] text-muted-foreground">
              {items.length > 0 ? `${items.length} 個執行中` : "無執行中任務"}
            </div>
          </div>
          {isAdmin && (
            <label className="mx-2 mb-1 flex cursor-pointer items-center gap-2 rounded-md bg-muted/40 px-2 py-1.5 text-[11px] text-muted-foreground hover:bg-muted/70">
              <input
                type="checkbox"
                className="h-3.5 w-3.5 cursor-pointer accent-blue-600"
                checked={notifyOnlyMine}
                onChange={(e) => {
                  const next = e.target.checked
                  setNotifyOnlyMine(next)
                  try {
                    window.localStorage.setItem(
                      NOTIFY_ONLY_MINE_KEY,
                      next ? "1" : "0",
                    )
                  } catch {
                    // localStorage 不可用時該設定仅本次購職生效
                  }
                }}
              />
              <span>只通知「我的」任務（避免被其他使用者的任務轟炸）</span>
            </label>
          )}
          <div className="max-h-[60vh] overflow-y-auto">
            {isLoading ? (
              <JobLoading />
            ) : items.length === 0 ? (
              <JobEmpty message="目前無執行中任務，其他狀態請至「背景任務」頁面查看" />
            ) : (
              items.map((job) => (
                <JobRow
                  key={job.id}
                  job={job}
                  compact
                  onClick={(j) => {
                    setFocusJobId(j.id)
                    setOpen(false)
                  }}
                />
              ))
            )}
          </div>
          <div className="mt-1 border-t border-border pt-1">
            <Link
              to="/jobs"
              onClick={() => setOpen(false)}
              className="flex items-center justify-between rounded-md px-3 py-2 text-sm font-medium text-blue-600 transition-colors hover:bg-blue-50 dark:text-blue-400 dark:hover:bg-blue-950/30"
            >
              <span>查看全部任務與歷史</span>
              <ChevronRight className="h-4 w-4" />
            </Link>
          </div>
        </div>
      )}
      <JobDetailDialog jobId={focusJobId} onClose={() => setFocusJobId(null)} />
    </div>
  )
}
