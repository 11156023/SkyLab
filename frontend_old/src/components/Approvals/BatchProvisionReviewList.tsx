/**
 * Reusable list of pending batch-provision jobs awaiting admin review.
 *
 * Each job is rendered as an expandable card that mirrors the depth of the
 * individual VM-request review page: spec snapshot, full member roster, and
 * (when scheduled) the next few computed windows so the admin can sanity-check
 * the RRULE before approving.
 *
 * Polls every 15s.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  Calendar,
  Check,
  ChevronDown,
  ChevronUp,
  Clock,
  RefreshCw,
  User,
  Users,
  X,
} from "lucide-react"
import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Textarea } from "@/components/ui/textarea"
import {
  type BatchJob,
  type BatchJobSpec,
  GroupFeatureService,
} from "@/features/groups/api"
import useCustomToast from "@/hooks/useCustomToast"

const PENDING_QUERY_KEY = ["batchProvision", "pending"] as const

export function BatchProvisionReviewList({
  showHeader = true,
}: {
  showHeader?: boolean
}) {
  const qc = useQueryClient()
  const toast = useCustomToast()

  const {
    data: jobs,
    isFetching,
    refetch,
  } = useQuery({
    queryKey: PENDING_QUERY_KEY,
    queryFn: () => GroupFeatureService.listPendingBatchJobs(),
    refetchInterval: 15_000,
  })

  const reviewMutation = useMutation({
    mutationFn: (args: {
      jobId: string
      decision: "approved" | "rejected"
      comment: string
    }) =>
      GroupFeatureService.reviewBatchJob({
        jobId: args.jobId,
        requestBody: { decision: args.decision, review_comment: args.comment },
      }),
    onSuccess: (_data, variables) => {
      toast.showSuccessToast(
        variables.decision === "approved" ? "已通過此申請" : "已退回此申請",
      )
      qc.invalidateQueries({ queryKey: PENDING_QUERY_KEY })
    },
    onError: (e: any) => {
      toast.showErrorToast(e?.body?.detail ?? "操作失敗")
    },
  })

  return (
    <div className="space-y-4">
      {showHeader && (
        <div className="flex items-center justify-between">
          <p className="text-sm text-muted-foreground">
            老師送交的群組批量建立申請，通過後立即進入建立流程
          </p>
          <Button variant="outline" size="sm" onClick={() => refetch()}>
            <RefreshCw
              className={`mr-2 h-4 w-4 ${isFetching ? "animate-spin" : ""}`}
            />
            重新整理
          </Button>
        </div>
      )}

      {!jobs?.length ? (
        <div className="rounded-md border border-dashed p-8 text-center text-muted-foreground">
          目前沒有待審核的批量申請
        </div>
      ) : (
        <div className="space-y-4">
          {jobs.map((job) => (
            <JobCard
              key={job.id}
              job={job}
              onDecide={(decision, comment) =>
                reviewMutation.mutate({ jobId: job.id, decision, comment })
              }
              busy={reviewMutation.isPending}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function JobCard({
  job,
  onDecide,
  busy,
}: {
  job: BatchJob
  onDecide: (decision: "approved" | "rejected", comment: string) => void
  busy: boolean
}) {
  const [dialog, setDialog] = useState<"approve" | "reject" | null>(null)
  const [expanded, setExpanded] = useState(true)
  const [comment, setComment] = useState("")
  const close = () => {
    setDialog(null)
    setComment("")
  }

  const previewQuery = useQuery({
    queryKey: ["batchProvision", "preview", job.id],
    queryFn: () =>
      GroupFeatureService.getRecurrencePreview({ jobId: job.id, count: 5 }),
    enabled: expanded && Boolean(job.recurrence_rule),
  })

  return (
    <div className="rounded-md border">
      {/* ── Header (always visible) ── */}
      <div className="flex items-start justify-between gap-4 p-4">
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          className="flex flex-1 items-start gap-3 text-left"
        >
          <div className="mt-1">
            {expanded ? (
              <ChevronUp className="h-4 w-4 text-muted-foreground" />
            ) : (
              <ChevronDown className="h-4 w-4 text-muted-foreground" />
            )}
          </div>
          <div className="flex-1 space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="font-semibold">{job.hostname_prefix}-*</h2>
              <Badge variant="secondary">
                {job.resource_type === "lxc" ? "LXC 容器" : "VM 虛擬機"}
              </Badge>
              {job.recurrence_rule && (
                <Badge variant="outline" className="gap-1">
                  <Clock className="h-3 w-3" /> 週期排程
                </Badge>
              )}
              <Badge variant="outline">
                <Users className="mr-1 h-3 w-3" /> {job.total} 位成員
              </Badge>
            </div>
            <p className="text-sm text-muted-foreground">
              <strong>{job.group_name ?? job.group_id.slice(0, 8)}</strong>
              {job.initiated_by_name && (
                <>
                  ・送出人 {job.initiated_by_name}（{job.initiated_by_email}）
                </>
              )}
              ・{fmtDateTime(job.created_at)}
            </p>
          </div>
        </button>

        <div className="flex shrink-0 gap-2">
          <Button
            variant="default"
            size="sm"
            onClick={() => setDialog("approve")}
            disabled={busy}
          >
            <Check className="mr-1 h-4 w-4" /> 通過
          </Button>
          <Button
            variant="destructive"
            size="sm"
            onClick={() => setDialog("reject")}
            disabled={busy}
          >
            <X className="mr-1 h-4 w-4" /> 退回
          </Button>
        </div>
      </div>

      {/* ── Expanded body ── */}
      {expanded && (
        <>
          <Separator />
          <div className="grid gap-6 p-4 md:grid-cols-2">
            <SpecSection job={job} />
            <ScheduleSection
              job={job}
              previewWindows={previewQuery.data?.windows ?? []}
              previewLoading={previewQuery.isFetching}
            />
          </div>

          <Separator />
          <MemberTable job={job} />
        </>
      )}

      {/* ── Approve / reject dialog ── */}
      <Dialog open={dialog !== null} onOpenChange={(v) => !v && close()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {dialog === "approve" ? "通過此批量申請" : "退回此批量申請"}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            <Label>備註（選填）</Label>
            <Textarea
              rows={3}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder={
                dialog === "reject"
                  ? "退回原因會回傳給老師看，例如：規格過大、時間衝突…"
                  : "通過備註（選填）"
              }
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={close}>
              取消
            </Button>
            <Button
              variant={dialog === "reject" ? "destructive" : "default"}
              onClick={() => {
                if (!dialog) return
                onDecide(
                  dialog === "approve" ? "approved" : "rejected",
                  comment,
                )
                close()
              }}
              disabled={busy}
            >
              確認
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function SpecSection({ job }: { job: BatchJob }) {
  const spec: BatchJobSpec = job.spec ?? {}
  const memoryGb = spec.memory ? (spec.memory / 1024).toFixed(1) : null
  const disk = job.resource_type === "lxc" ? spec.rootfs_size : spec.disk_size
  return (
    <section className="space-y-2">
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        <User className="h-4 w-4" /> 每位成員的規格
      </h3>
      <dl className="space-y-1 text-sm">
        <Row
          k="CPU / RAM / Disk"
          v={`${spec.cores ?? "?"} CPU・${memoryGb ?? "?"} GB RAM・${disk ?? "?"} GB Disk`}
        />
        {job.resource_type === "lxc" ? (
          <Row k="OS Template" v={spec.ostemplate ?? "—"} />
        ) : (
          <>
            <Row k="VM Template" v={String(spec.template_id ?? "—")} />
            <Row k="Username" v={spec.username ?? "—"} />
          </>
        )}
        <Row k="環境類型" v={spec.environment_type ?? "—"} />
        {spec.os_info && <Row k="OS Info" v={spec.os_info} />}
        {spec.expiry_date && <Row k="到期日" v={spec.expiry_date} />}
        <Row
          k="Hostnames 範例"
          v={`${job.hostname_prefix}-1, ${job.hostname_prefix}-2, …, ${job.hostname_prefix}-${job.total}`}
        />
      </dl>
    </section>
  )
}

function ScheduleSection({
  job,
  previewWindows,
  previewLoading,
}: {
  job: BatchJob
  previewWindows: [string, string][]
  previewLoading: boolean
}) {
  if (!job.recurrence_rule) {
    return (
      <section className="space-y-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <Calendar className="h-4 w-4" /> 排程
        </h3>
        <p className="text-sm text-muted-foreground">
          一次性建立，沒有週期排程。
        </p>
      </section>
    )
  }

  return (
    <section className="space-y-2">
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        <Calendar className="h-4 w-4" /> 週期排程
      </h3>
      <dl className="space-y-1 text-sm">
        <Row k="RRULE" v={job.recurrence_rule} mono />
        <Row
          k="每次時長"
          v={`${job.recurrence_duration_minutes ?? "?"} 分鐘`}
        />
        <Row k="時區" v={job.schedule_timezone ?? "UTC"} />
      </dl>
      <div className="mt-3">
        <p className="mb-1 text-xs font-medium text-muted-foreground">
          下 5 次預覽
        </p>
        {previewLoading ? (
          <p className="text-xs text-muted-foreground">計算中…</p>
        ) : previewWindows.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            無下次窗口（規則可能已過期）
          </p>
        ) : (
          <ol className="space-y-0.5 text-xs font-mono">
            {previewWindows.map(([start, end], i) => (
              <li key={i}>
                {fmtDateTime(start, job.schedule_timezone)} →{" "}
                {fmtDateTime(end, job.schedule_timezone)}
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  )
}

function MemberTable({ job }: { job: BatchJob }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="px-4 pb-4">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="mb-2 flex w-full items-center justify-between rounded-md border bg-muted/30 px-3 py-2 text-sm font-semibold hover:bg-muted/50"
      >
        <span>將建立的資源（{job.total} 位）</span>
        {open ? (
          <ChevronUp className="h-4 w-4 text-muted-foreground" />
        ) : (
          <ChevronDown className="h-4 w-4 text-muted-foreground" />
        )}
      </button>
      {open && (
        <div className="max-h-72 overflow-y-auto rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-12">#</TableHead>
                <TableHead>成員</TableHead>
                <TableHead>Hostname</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {job.tasks.map((t) => (
                <TableRow key={t.id}>
                  <TableCell className="text-xs text-muted-foreground">
                    {t.member_index}
                  </TableCell>
                  <TableCell className="text-sm">
                    <div>{t.user_name ?? "-"}</div>
                    <div className="text-xs text-muted-foreground">
                      {t.user_email}
                    </div>
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {job.hostname_prefix}-{t.member_index}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  )
}

function Row({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-border/40 py-1 last:border-0">
      <dt className="text-xs uppercase tracking-wider text-muted-foreground">
        {k}
      </dt>
      <dd
        className={`max-w-[60%] text-right text-sm leading-snug ${mono ? "font-mono text-xs" : ""}`}
      >
        {v}
      </dd>
    </div>
  )
}

function fmtDateTime(iso: string, timezone?: string | null): string {
  return new Intl.DateTimeFormat("zh-TW", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: timezone || "Asia/Taipei",
  }).format(new Date(iso))
}

export function useBatchProvisionPendingCount() {
  const { data } = useQuery({
    queryKey: PENDING_QUERY_KEY,
    queryFn: () => GroupFeatureService.listPendingBatchJobs(),
    refetchInterval: 15_000,
  })
  return data?.length ?? 0
}
