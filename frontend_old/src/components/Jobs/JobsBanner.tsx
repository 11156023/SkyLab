import { useQuery } from "@tanstack/react-query"
import { Activity } from "lucide-react"

import { cn } from "@/lib/utils"
import { JobsAPI } from "@/services/jobs"
import { JobsButton } from "./JobsButton"

/**
 * 頁面最上方常駐 banner：左側顯示背景任務狀態摘要，右側放 Job 按鈕。
 * - 始終顯示（不分角色）
 * - 只計 status === "running" 的任務（與 popover 一致）
 */
export function JobsBanner() {
  const { data } = useQuery({
    queryKey: ["jobs", "running"],
    queryFn: () =>
      JobsAPI.list({ statuses: ["running"], limit: 200, history_days: 30 }),
    refetchInterval: 15000,
    staleTime: 5000,
  })

  const running = data?.items.length ?? 0
  const hasRunning = running > 0

  return (
    <div
      className={cn(
        "flex items-center justify-between border-b px-6 py-2",
        hasRunning
          ? "border-blue-300/60 bg-blue-50/70 dark:border-blue-900/50 dark:bg-blue-950/30"
          : "border-border/60 bg-muted/40",
      )}
    >
      <div className="flex items-center gap-2 text-xs">
        <Activity
          className={cn(
            "h-3.5 w-3.5",
            hasRunning
              ? "text-blue-600 dark:text-blue-400"
              : "text-muted-foreground",
          )}
        />
        <span
          className={cn(
            hasRunning
              ? "font-medium text-blue-900 dark:text-blue-200"
              : "text-muted-foreground",
          )}
        >
          {hasRunning ? `目前有 ${running} 個任務執行中` : "目前無任務執行中"}
        </span>
      </div>
      <JobsButton />
    </div>
  )
}
