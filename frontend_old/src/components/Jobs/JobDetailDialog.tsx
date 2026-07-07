import { useQuery } from "@tanstack/react-query"
import { AlertCircle, Loader2 } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { JobsAPI } from "@/services/jobs"
import { JOB_KIND_LABEL, JOB_STATUS_META } from "./JobRow"

type Props = {
  jobId: string | null
  onClose: () => void
}

const fmt = (iso: string | null | undefined) => {
  if (!iso) return "—"
  try {
    return new Intl.DateTimeFormat("zh-TW", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(new Date(iso))
  } catch {
    return iso
  }
}

const formatExtraValue = (v: unknown): string => {
  if (v === null || v === undefined || v === "") return "—"
  if (typeof v === "boolean") return v ? "是" : "否"
  if (typeof v === "object") return JSON.stringify(v)
  return String(v)
}

const EXTRA_LABELS: Record<string, string> = {
  request_id: "請求 ID",
  vmid: "VMID",
  source_node: "來源節點",
  target_node: "目標節點",
  attempt_count: "嘗試次數",
  rebalance_epoch: "Rebalance epoch",
  claimed_by: "Claimed by",
  requested_at: "請求時間",
  available_at: "可開始時間",
  claimed_at: "Claim 時間",
  started_at: "開始時間",
  finished_at: "完成時間",
  hostname: "Hostname",
  task_id: "Task ID",
  template_slug: "模板 slug",
  template_name: "模板名稱",
  script_path: "腳本路徑",
  raw_status: "原始狀態",
  progress_text: "進度文字",
  resource_type: "資源類型",
  cores: "CPU 核心數",
  memory: "記憶體 (MB)",
  storage: "Storage",
  disk_size: "磁碟 (GB)",
  rootfs_size: "rootfs (GB)",
  ostemplate: "OS 模板",
  template_id: "模板 ID",
  service_template_slug: "服務模板",
  assigned_node: "指派節點",
  actual_node: "實際節點",
  desired_node: "期望節點",
  migration_status: "遷移狀態",
  expiry_date: "到期日",
  start_at: "開始時間",
  end_at: "結束時間",
  reason: "原因",
  review_comment: "審核備註",
  change_type: "變更類型",
  current_cpu: "目前 CPU",
  current_memory: "目前記憶體",
  current_disk: "目前磁碟",
  requested_cpu: "請求 CPU",
  requested_memory: "請求記憶體",
  requested_disk: "請求磁碟",
  applied_at: "套用時間",
}

export function JobDetailDialog({ jobId, onClose }: Props) {
  const open = jobId !== null
  const query = useQuery({
    queryKey: ["jobs", "detail", jobId],
    queryFn: () => JobsAPI.detail(jobId as string),
    enabled: open,
    refetchInterval: (q) => {
      const data = q.state.data
      if (!data) return false
      const s = data.item.status
      // 任務還在跑就持續刷新
      return s === "pending" || s === "running" || s === "blocked"
        ? 3000
        : false
    },
  })

  // 切換 jobId 時 useQuery 會自動以新 key 重新抓取，不需手動 refetch

  const data = query.data
  const item = data?.item

  const statusMeta = item ? JOB_STATUS_META[item.status] : null
  const StatusIcon = statusMeta?.icon

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {item ? (
              <>
                <Badge variant="outline" className="font-normal">
                  {JOB_KIND_LABEL[item.kind]}
                </Badge>
                <span className="truncate">{item.title}</span>
              </>
            ) : (
              "任務詳細"
            )}
          </DialogTitle>
          <DialogDescription className="font-mono text-xs">
            {jobId ?? ""}
          </DialogDescription>
        </DialogHeader>

        {query.isLoading && (
          <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            載入中…
          </div>
        )}

        {query.isError && (
          <div className="flex items-start gap-2 rounded-md border border-destructive/50 bg-destructive/5 p-3 text-sm text-destructive">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <div>
              <div className="font-medium">無法載入任務詳細</div>
              <div className="mt-1 break-all">
                {(query.error as Error)?.message ?? "Unknown error"}
              </div>
            </div>
          </div>
        )}

        {item && data && (
          <div className="space-y-4">
            {/* 狀態列 */}
            <div className="flex flex-wrap items-center gap-2 text-sm">
              {StatusIcon && statusMeta && (
                <Badge variant="secondary" className="gap-1">
                  <StatusIcon className={`h-3.5 w-3.5 ${statusMeta.cls}`} />
                  {statusMeta.label}
                </Badge>
              )}
              {typeof item.progress === "number" && (
                <Badge variant="outline">{item.progress}%</Badge>
              )}
              {item.user_email && (
                <span className="text-muted-foreground">
                  發起人：{item.user_email}
                </span>
              )}
            </div>

            {/* 時間 */}
            <div className="grid grid-cols-3 gap-3 rounded-md border bg-muted/30 p-3 text-xs">
              <div>
                <div className="text-muted-foreground">建立</div>
                <div className="mt-0.5 font-mono">{fmt(item.created_at)}</div>
              </div>
              <div>
                <div className="text-muted-foreground">更新</div>
                <div className="mt-0.5 font-mono">{fmt(item.updated_at)}</div>
              </div>
              <div>
                <div className="text-muted-foreground">完成</div>
                <div className="mt-0.5 font-mono">{fmt(item.completed_at)}</div>
              </div>
            </div>

            {/* 訊息 */}
            {item.message && (
              <div>
                <div className="mb-1 text-xs font-medium text-muted-foreground">
                  訊息
                </div>
                <div className="rounded-md border bg-muted/30 p-2 text-sm whitespace-pre-wrap wrap-break-word">
                  {item.message}
                </div>
              </div>
            )}

            {/* 詳細欄位 */}
            {Object.keys(data.extra).length > 0 && (
              <div>
                <div className="mb-1 text-xs font-medium text-muted-foreground">
                  詳細
                </div>
                <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 rounded-md border bg-muted/30 p-3 text-xs">
                  {Object.entries(data.extra)
                    .filter(
                      ([, v]) => v !== null && v !== undefined && v !== "",
                    )
                    .map(([k, v]) => (
                      <div key={k} className="flex items-baseline gap-2">
                        <span className="text-muted-foreground shrink-0">
                          {EXTRA_LABELS[k] ?? k}：
                        </span>
                        <span className="font-mono break-all">
                          {formatExtraValue(v)}
                        </span>
                      </div>
                    ))}
                </div>
              </div>
            )}

            {/* 錯誤 */}
            {data.error && (
              <div>
                <div className="mb-1 text-xs font-medium text-destructive">
                  錯誤
                </div>
                <div className="max-h-40 overflow-auto rounded-md border border-destructive/50 bg-destructive/5">
                  <pre className="whitespace-pre-wrap wrap-break-word p-2 font-mono text-xs text-destructive">
                    {data.error}
                  </pre>
                </div>
              </div>
            )}

            {/* 輸出 (script_deploy) */}
            {data.output && (
              <div>
                <div className="mb-1 text-xs font-medium text-muted-foreground">
                  輸出
                </div>
                <div className="h-72 overflow-auto rounded-md border bg-zinc-950 text-zinc-100">
                  <pre className="whitespace-pre-wrap wrap-break-word p-3 font-mono text-xs">
                    {data.output}
                  </pre>
                </div>
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
