import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { RefreshCw } from "lucide-react"
import { useMemo, useState } from "react"

import { JobDetailDialog } from "@/components/Jobs/JobDetailDialog"
import {
  JOB_KIND_LABEL,
  JobEmpty,
  JobLoading,
  JobRow,
} from "@/components/Jobs/JobRow"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"
import {
  type JobItem,
  type JobKind,
  type JobStatus,
  JobsAPI,
} from "@/services/jobs"

export const Route = createFileRoute("/_layout/jobs")({
  component: JobsPage,
  head: () => ({
    meta: [{ title: "背景任務 - SkyLab" }],
  }),
})

const KIND_OPTIONS: { value: "all" | JobKind; label: string }[] = [
  { value: "all", label: "全部類型" },
  { value: "migration", label: JOB_KIND_LABEL.migration },
  { value: "script_deploy", label: JOB_KIND_LABEL.script_deploy },
  { value: "vm_request", label: JOB_KIND_LABEL.vm_request },
  { value: "spec_change", label: JOB_KIND_LABEL.spec_change },
]

const STATUS_OPTIONS: { value: "all" | "active" | JobStatus; label: string }[] =
  [
    { value: "all", label: "全部狀態" },
    { value: "active", label: "進行中（pending/running/blocked）" },
    { value: "pending", label: "等待中" },
    { value: "running", label: "執行中" },
    { value: "completed", label: "已完成" },
    { value: "failed", label: "失敗" },
    { value: "blocked", label: "受阻" },
    { value: "cancelled", label: "已取消" },
  ]

const PAGE_SIZE = 30

function JobsPage() {
  const [kind, setKind] = useState<"all" | JobKind>("all")
  const [status, setStatus] = useState<"all" | "active" | JobStatus>("all")
  const [page, setPage] = useState(0)
  const [focusJobId, setFocusJobId] = useState<string | null>(null)

  const queryParams = useMemo(() => {
    return {
      kinds: kind === "all" ? undefined : ([kind] as JobKind[]),
      statuses:
        status === "all" || status === "active"
          ? undefined
          : ([status] as JobStatus[]),
      active_only: status === "active",
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
      history_days: 30,
    }
  }, [kind, status, page])

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ["jobs", "list", queryParams],
    queryFn: () => JobsAPI.list(queryParams),
    refetchInterval: 5000,
  })

  const items = data?.items ?? []
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">背景任務</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            集中檢視所有需要等待的後端任務：VM
            遷移、服務模板部署、開機申請、規格變更。
          </p>
        </div>
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
      </header>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">篩選</CardTitle>
          <CardDescription>顯示最近 30 天的任務記錄。</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">類型</span>
              <Select
                value={kind}
                onValueChange={(v) => {
                  setKind(v as typeof kind)
                  setPage(0)
                }}
              >
                <SelectTrigger className="h-8 w-37.5">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {KIND_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">狀態</span>
              <Select
                value={status}
                onValueChange={(v) => {
                  setStatus(v as typeof status)
                  setPage(0)
                }}
              >
                <SelectTrigger className="h-8 w-57.5">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {STATUS_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {data && (
              <div className="ml-auto text-xs text-muted-foreground">
                共 {total} 筆 · {data.active_count} 個進行中
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="p-2">
          {isLoading ? (
            <JobLoading />
          ) : items.length === 0 ? (
            <JobEmpty message="目前沒有符合條件的任務" />
          ) : (
            <JobsList items={items} onSelect={setFocusJobId} />
          )}
        </CardContent>
      </Card>

      <JobDetailDialog jobId={focusJobId} onClose={() => setFocusJobId(null)} />

      {totalPages > 1 && (
        <div className="flex items-center justify-end gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={page === 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
          >
            上一頁
          </Button>
          <span className="text-xs text-muted-foreground">
            第 {page + 1} / {totalPages} 頁
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={page >= totalPages - 1}
            onClick={() => setPage((p) => p + 1)}
          >
            下一頁
          </Button>
        </div>
      )}
    </div>
  )
}

function JobsList({
  items,
  onSelect,
}: {
  items: JobItem[]
  onSelect: (id: string) => void
}) {
  return (
    <div className="divide-y divide-border">
      {items.map((job) => (
        <JobRow key={job.id} job={job} onClick={(j) => onSelect(j.id)} />
      ))}
    </div>
  )
}
