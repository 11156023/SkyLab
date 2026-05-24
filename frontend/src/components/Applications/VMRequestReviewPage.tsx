import {
  useMutation,
  useQueryClient,
  useSuspenseQuery,
} from "@tanstack/react-query"
import { Link, useNavigate } from "@tanstack/react-router"
import { AlertTriangle, ArrowLeft, Check, Pin, X } from "lucide-react"
import { useMemo, useState } from "react"
import { useTranslation } from "react-i18next"

import { VmRequestsService } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { LoadingButton } from "@/components/ui/loading-button"
import { Textarea } from "@/components/ui/textarea"
import useCustomToast from "@/hooks/useCustomToast"
import { queryKeys } from "@/lib/queryKeys"
import { cn } from "@/lib/utils"
import {
  type VmRequestReviewNodeScore,
  type VmRequestReviewOverlapItem,
  type VmRequestReviewRuntimeResource,
  VmRequestReviewService,
} from "@/services/vmRequestReview"
import { handleError } from "@/utils"

function formatDateTime(value?: string | null) {
  if (!value) return "未排程"
  return new Intl.DateTimeFormat("zh-TW", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Taipei",
  }).format(new Date(value))
}

function formatDateRange(startAt?: string | null, endAt?: string | null) {
  if (!startAt || !endAt) return "未排程"
  return `${formatDateTime(startAt)} - ${formatDateTime(endAt)}`
}

function statusMeta(status: string) {
  if (status === "approved") {
    return { label: "已通過", variant: "default" as const }
  }
  if (status === "cancelled") {
    return { label: "已撤銷", variant: "secondary" as const }
  }
  if (status === "rejected") {
    return { label: "已拒絕", variant: "destructive" as const }
  }
  return { label: "待審核", variant: "outline" as const }
}

function migrationMeta(status: VmRequestReviewOverlapItem["migration_status"]) {
  if (status === "completed")
    return { label: "已平衡", variant: "default" as const }
  if (status === "running")
    return { label: "搬移中", variant: "secondary" as const }
  if (status === "failed" || status === "blocked") {
    return {
      label: status === "failed" ? "失敗" : "受阻",
      variant: "destructive" as const,
    }
  }
  if (status === "pending")
    return { label: "待搬移", variant: "secondary" as const }
  return { label: "穩定", variant: "outline" as const }
}

function resourceTypeLabel(resourceType: string) {
  return resourceType === "lxc" ? "LXC 容器" : "VM 虛擬機"
}

function specLabel(request: {
  resource_type: string
  cores: number
  memory: number
  disk_size?: number | null
  rootfs_size?: number | null
}) {
  const disk =
    request.resource_type === "vm" ? request.disk_size : request.rootfs_size
  return `${request.cores} CPU / ${(request.memory / 1024).toFixed(1)} GB RAM / ${disk ?? 0} GB Disk`
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-border/70 py-2 last:border-b-0">
      <span className="text-xs uppercase tracking-[0.16em] text-muted-foreground">
        {label}
      </span>
      <span className="max-w-[70%] text-right text-sm leading-snug">
        {value}
      </span>
    </div>
  )
}

type StackItem = {
  name: string
  count: number
  kind: "running" | "projected" | "current"
}

type ReviewContextShape = {
  cluster_nodes: string[]
  current_running_resources: VmRequestReviewRuntimeResource[]
  overlapping_approved_requests: VmRequestReviewOverlapItem[]
}

function buildPveStacks(context: ReviewContextShape) {
  const byNode = new Map<string, Map<string, StackItem>>()

  const ensureNode = (node: string) => {
    if (!byNode.has(node)) byNode.set(node, new Map())
    return byNode.get(node)!
  }

  for (const node of context.cluster_nodes) {
    ensureNode(node)
  }

  for (const resource of context.current_running_resources) {
    const node = resource.node || "unknown"
    const items = ensureNode(node)
    const resourceKey = `running:${resource.name}`
    const current = items.get(resourceKey)
    items.set(resourceKey, {
      name: resource.name,
      count: (current?.count ?? 0) + 1,
      kind: "running",
    })
  }

  for (const request of context.overlapping_approved_requests) {
    const node = request.projected_node
    if (!node) continue
    const items = ensureNode(node)
    const kind = request.is_current_request ? "current" : "projected"
    const requestKey = `${kind}:${request.hostname}`
    const current = items.get(requestKey)
    items.set(requestKey, {
      name: request.hostname,
      count: (current?.count ?? 0) + 1,
      kind,
    })
  }

  return Array.from(byNode.entries())
    .map(([node, items]) => ({
      node,
      items: Array.from(items.values()).sort(
        (a, b) =>
          (a.kind === "current" ? -1 : a.kind === "projected" ? 0 : 1) -
            (b.kind === "current" ? -1 : b.kind === "projected" ? 0 : 1) ||
          a.name.localeCompare(b.name),
      ),
    }))
    .sort((a, b) => a.node.localeCompare(b.node))
}

function RuntimeResourceCard({
  resource,
}: {
  resource: VmRequestReviewRuntimeResource
}) {
  return (
    <article className="rounded-xl border border-border/70 bg-background/50 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="font-medium">{resource.name}</h3>
          <p className="text-xs text-muted-foreground">
            {resource.node} / {resource.resource_type.toUpperCase()} / VMID{" "}
            {resource.vmid}
          </p>
        </div>
        <Badge variant="secondary">
          {resource.status === "running" ? "執行中" : resource.status}
        </Badge>
      </div>
      <div className="mt-3 text-sm text-muted-foreground">
        {resource.linked_hostname
          ? `關聯申請：${resource.linked_hostname}`
          : "目前沒有對應中的啟用申請。"}
      </div>
    </article>
  )
}

function PveStackColumn({ node, items }: { node: string; items: StackItem[] }) {
  return (
    <article className="grid gap-2.5">
      <div className="rounded-b-[18px] border-x-4 border-b-4 border-t-0 border-slate-500/80 px-2.5 pb-0 pt-2.5">
        <div className="flex items-center gap-1.5 border-b-[3px] border-slate-500/80 pb-1.5 text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
          <span>{node}</span>
          <span className="rounded bg-accent px-1.5 py-0.5 text-[9px] font-semibold text-accent-foreground">
            PVE
          </span>
        </div>
        <div className="flex h-[280px] flex-col-reverse gap-1.5 overflow-y-auto py-2 pr-1">
          {items.length === 0 ? (
            <div className="grid min-h-[64px] place-items-center text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              empty
            </div>
          ) : (
            items.map((item) => (
              <div
                key={`${node}-${item.name}`}
                className={cn(
                  "flex min-h-[42px] items-center justify-between gap-2 rounded-b-[12px] border-[2px] bg-background/90 px-2.5 py-1.5 text-xs font-medium",
                  item.kind === "current" &&
                    "border-teal-500 bg-teal-500/10 text-teal-300",
                  item.kind === "projected" &&
                    "border-amber-400 bg-amber-500/10 text-amber-200",
                  item.kind === "running" && "border-slate-500/70",
                )}
              >
                <span className="truncate">{item.name}</span>
                <div className="shrink-0 text-right text-[10px]">
                  <div>x{item.count}</div>
                  <div className="text-muted-foreground">
                    {item.kind === "current"
                      ? "本次申請"
                      : item.kind === "projected"
                        ? "預計同時段"
                        : "目前在線"}
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
      <div className="text-center">
        <h3 className="text-lg font-semibold tracking-tight">{node}</h3>
        <p className="mt-0.5 text-[11px] text-muted-foreground">
          顯示目前在線資源與核准後該時段的預計堆疊
        </p>
      </div>
    </article>
  )
}

function NodeScoreCard({ score }: { score: VmRequestReviewNodeScore }) {
  const pct = (v: number) => `${(v * 100).toFixed(1)}%`
  return (
    <article
      className={cn(
        "rounded-xl border border-border/70 bg-background/50 p-3",
        score.is_selected && "border-primary/60 bg-primary/5",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h3 className="font-medium">{score.node}</h3>
          {score.is_selected && <Badge variant="secondary">選中</Badge>}
        </div>
        <div className="text-right">
          <span className="text-lg font-bold tabular-nums">
            {score.balance_score.toFixed(4)}
          </span>
          <div className="text-[10px] text-muted-foreground">Balance Score</div>
        </div>
      </div>
      <div className="mt-3 grid grid-cols-3 gap-x-3 gap-y-2 text-xs">
        <div>
          <div className="text-muted-foreground">CPU 佔比</div>
          <div className="font-medium tabular-nums">{pct(score.cpu_share)}</div>
        </div>
        <div>
          <div className="text-muted-foreground">RAM 佔比</div>
          <div className="font-medium tabular-nums">
            {pct(score.memory_share)}
          </div>
        </div>
        <div>
          <div className="text-muted-foreground">Disk 佔比</div>
          <div className="font-medium tabular-nums">
            {pct(score.disk_share)}
          </div>
        </div>
        <div>
          <div className="text-muted-foreground">Peak Penalty</div>
          <div
            className={cn(
              "font-medium tabular-nums",
              score.peak_penalty > 0 && "text-amber-500",
            )}
          >
            {score.peak_penalty.toFixed(3)}
          </div>
        </div>
        <div>
          <div className="text-muted-foreground">Loadavg Penalty</div>
          <div
            className={cn(
              "font-medium tabular-nums",
              score.loadavg_penalty > 0 && "text-amber-500",
            )}
          >
            {score.loadavg_penalty.toFixed(3)}
          </div>
        </div>
        <div>
          <div className="text-muted-foreground">Storage Penalty</div>
          <div
            className={cn(
              "font-medium tabular-nums",
              score.storage_penalty > 0 && "text-amber-500",
            )}
          >
            {score.storage_penalty.toFixed(3)}
          </div>
        </div>
        <div>
          <div className="text-muted-foreground">Migration Cost</div>
          <div className="font-medium tabular-nums">
            {score.migration_cost.toFixed(3)}
          </div>
        </div>
        <div>
          <div className="text-muted-foreground">Priority</div>
          <div className="font-medium tabular-nums">{score.priority}</div>
        </div>
      </div>
      {score.reason && (
        <div className="mt-2 text-xs text-muted-foreground">{score.reason}</div>
      )}
    </article>
  )
}

function OverlapCard({ item }: { item: VmRequestReviewOverlapItem }) {
  const status = statusMeta(item.status)
  const migration = migrationMeta(item.migration_status)

  return (
    <article
      className={cn(
        "rounded-xl border border-border/70 bg-background/50 p-3",
        item.is_current_request && "border-primary/60 bg-primary/5",
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-medium">{item.hostname}</h3>
            {item.is_current_request && (
              <Badge variant="secondary">本次申請</Badge>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            {resourceTypeLabel(item.resource_type)} /{" "}
            {formatDateRange(item.start_at, item.end_at)}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge variant={status.variant}>{status.label}</Badge>
          <Badge variant={migration.variant}>{migration.label}</Badge>
        </div>
      </div>
      <div className="mt-3 grid gap-2 text-sm md:grid-cols-2 xl:grid-cols-4">
        <div>
          <div className="text-xs uppercase tracking-[0.14em] text-muted-foreground">
            目前位置
          </div>
          <div>{item.actual_node ?? item.assigned_node ?? "尚未建立"}</div>
        </div>
        <div>
          <div className="text-xs uppercase tracking-[0.14em] text-muted-foreground">
            期望位置
          </div>
          <div>{item.desired_node ?? "等待重排"}</div>
        </div>
        <div>
          <div className="text-xs uppercase tracking-[0.14em] text-muted-foreground">
            預測位置
          </div>
          <div>{item.projected_node ?? "無法分配"}</div>
        </div>
        <div>
          <div className="text-xs uppercase tracking-[0.14em] text-muted-foreground">
            執行狀態
          </div>
          <div>
            {item.is_provisioned ? `VMID ${item.vmid}` : "尚未建立"}
            {item.is_running_now ? " / 執行中" : ""}
          </div>
        </div>
      </div>
    </article>
  )
}

export function VMRequestReviewPage({ requestId }: { requestId: string }) {
  const { t } = useTranslation(["approvals", "messages"])
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [comment, setComment] = useState("")

  const reviewContextQuery = useSuspenseQuery({
    queryKey: queryKeys.vmRequests.reviewContext(requestId),
    queryFn: () => VmRequestReviewService.getContext({ requestId }),
  })

  const context = reviewContextQuery.data
  const request = context.request
  const requestStatus = statusMeta(request.status)
  const isPending = request.status === "pending"

  const overlapSummary = useMemo(() => {
    const approved = context.overlapping_approved_requests.filter(
      (item) => !item.is_current_request,
    ).length
    const provisioned = context.overlapping_approved_requests.filter(
      (item) => item.is_provisioned,
    ).length
    return { approved, provisioned }
  }, [context.overlapping_approved_requests])
  const pveStacks = useMemo(() => buildPveStacks(context), [context])

  const reviewMutation = useMutation({
    mutationFn: (status: "approved" | "rejected") =>
      VmRequestsService.reviewVmRequest({
        requestId: request.id,
        requestBody: {
          status,
          review_comment: comment || null,
        },
      }),
    onSuccess: (_data, status) => {
      showSuccessToast(
        status === "approved"
          ? t("messages:success.applicationApproved")
          : t("messages:success.applicationRejected"),
      )
      queryClient.invalidateQueries({ queryKey: queryKeys.vmRequests.admin })
      queryClient.invalidateQueries({
        queryKey: queryKeys.vmRequests.detail(request.id),
      })
      queryClient.invalidateQueries({
        queryKey: queryKeys.vmRequests.reviewContext(request.id),
      })
      // Approving a request whose start window is already open triggers
      // background provisioning immediately on the backend; refresh the
      // creating-placeholder list so /resources shows the in-progress VM.
      queryClient.invalidateQueries({ queryKey: ["pending-resources"] })
      navigate({ to: "/approvals" })
    },
    onError: handleError.bind(showErrorToast),
  })

  return (
    <div className="space-y-5">
      <header className="border-b border-border/70 pb-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-2">
            <Button asChild variant="outline" size="sm">
              <Link to="/approvals">
                <ArrowLeft className="mr-2 h-4 w-4" />
                返回審核列表
              </Link>
            </Button>
            <div>
              <h1 className="text-2xl font-bold tracking-tight">
                {request.hostname}
              </h1>
              <p className="text-sm text-muted-foreground">
                檢視申請時段、目前在線資源，以及這筆申請加入後的平衡分配結果，再決定是否通過。
              </p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant={requestStatus.variant}>{requestStatus.label}</Badge>
            <Badge variant={context.feasible ? "default" : "destructive"}>
              {context.feasible ? "可分配" : "無法分配"}
            </Badge>
            {request.migration_pinned && (
              <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-400">
                <Pin className="h-3 w-3" />
                Pinned
              </span>
            )}
            {context.projected_node && (
              <Badge variant="secondary">
                預測節點 {context.projected_node}
              </Badge>
            )}
            {context.window_active_now && (
              <Badge variant="outline">時段進行中</Badge>
            )}
          </div>
        </div>
      </header>

      {request.resource_warning && (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-300">
          <AlertTriangle className="mr-1.5 inline h-4 w-4" />
          {request.resource_warning}
        </div>
      )}

      <section className="grid gap-5 xl:grid-cols-[420px_minmax(0,1fr)]">
        <div className="glass-panel rounded-2xl p-4">
          <h2 className="text-lg font-semibold">申請資訊</h2>
          <div className="mt-3">
            <InfoRow
              label="申請人"
              value={request.user_full_name || request.user_email || "未知"}
            />
            <InfoRow
              label="類型"
              value={resourceTypeLabel(request.resource_type)}
            />
            <InfoRow label="規格" value={specLabel(request)} />
            <InfoRow label="儲存體" value={request.storage || "未設定"} />
            <InfoRow
              label="模板"
              value={
                request.template_id
                  ? `Template #${request.template_id}`
                  : request.ostemplate || "未設定"
              }
            />
            <InfoRow
              label="時段"
              value={formatDateRange(context.window_start, context.window_end)}
            />
            <InfoRow
              label="預測節點"
              value={context.projected_node || "無法分配"}
            />
            <InfoRow
              label="策略"
              value={context.placement_strategy || "priority_dominant_share"}
            />
          </div>
          <div className="mt-4 border-t border-border/70 pt-3 text-sm">
            <div className="mb-1 text-xs uppercase tracking-[0.16em] text-muted-foreground">
              申請原因
            </div>
            <p>{request.reason}</p>
          </div>
        </div>

        <div className="glass-panel rounded-2xl p-4">
          <h2 className="text-lg font-semibold">審核判斷</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            {context.summary}
          </p>

          {context.reasons.length > 0 && (
            <div className="mt-4 rounded-xl border border-border/70 bg-background/50 p-3">
              <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">
                為什麼選這台節點
              </div>
              <ul className="mt-2 grid gap-2 text-sm leading-6">
                {context.reasons.map((reason) => (
                  <li key={reason} className="flex gap-2">
                    <span className="mt-[2px] text-primary">•</span>
                    <span>{reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {context.warnings.length > 0 && (
            <div className="mt-4 flex flex-wrap gap-2">
              {context.warnings.map((warning) => (
                <Badge key={warning} variant="outline">
                  {warning}
                </Badge>
              ))}
            </div>
          )}

          {context.resource_warnings?.length > 0 && (
            <div className="mt-4 space-y-1.5">
              {context.resource_warnings.map((w) => (
                <div
                  key={w}
                  className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-2.5 text-xs text-amber-400"
                >
                  <AlertTriangle className="mr-1 inline h-3.5 w-3.5" />
                  {w}
                </div>
              ))}
            </div>
          )}

          <div className="mt-4 grid gap-3 md:grid-cols-3">
            <div className="rounded-xl border border-border/70 bg-background/50 p-3">
              <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">
                目前在線
              </div>
              <div className="mt-2 text-2xl font-semibold">
                {context.current_running_resources.length}
              </div>
              <div className="mt-1 text-sm text-muted-foreground">
                目前 SkyLab pool 內正在運行的資源數量。
              </div>
            </div>
            <div className="rounded-xl border border-border/70 bg-background/50 p-3">
              <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">
                同時段已通過
              </div>
              <div className="mt-2 text-2xl font-semibold">
                {overlapSummary.approved}
              </div>
              <div className="mt-1 text-sm text-muted-foreground">
                與這筆申請時段重疊的已通過申請數量。
              </div>
            </div>
            <div className="rounded-xl border border-border/70 bg-background/50 p-3">
              <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">
                已建立重疊機器
              </div>
              <div className="mt-2 text-2xl font-semibold">
                {overlapSummary.provisioned}
              </div>
              <div className="mt-1 text-sm text-muted-foreground">
                同時段中已經有 VMID 的申請數量。
              </div>
            </div>
          </div>

          <div className="mt-4">
            <label htmlFor="review-note" className="text-sm font-medium">
              {t("approvals:review.reviewNote")}
            </label>
            <Textarea
              id="review-note"
              placeholder={t("approvals:review.reviewNotePlaceholder")}
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              className="mt-1"
              disabled={!isPending || reviewMutation.isPending}
            />
          </div>

          <div className="mt-4 flex flex-wrap gap-3">
            {isPending ? (
              <>
                <LoadingButton
                  type="button"
                  onClick={() => reviewMutation.mutate("approved")}
                  loading={reviewMutation.isPending}
                  disabled={!context.feasible}
                >
                  <Check className="mr-2 h-4 w-4" />
                  {t("approvals:review.confirmApprove")}
                </LoadingButton>
                <LoadingButton
                  type="button"
                  variant="destructive"
                  onClick={() => reviewMutation.mutate("rejected")}
                  loading={reviewMutation.isPending}
                >
                  <X className="mr-2 h-4 w-4" />
                  {t("approvals:review.confirmReject")}
                </LoadingButton>
              </>
            ) : (
              <div className="rounded-lg border border-dashed px-3 py-3 text-sm text-muted-foreground">
                這筆申請已經審核完成。
              </div>
            )}
          </div>
        </div>
      </section>

      {context.node_scores.length > 0 && (
        <section className="glass-panel rounded-2xl p-4">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/70 pb-3">
            <div>
              <h2 className="text-lg font-semibold">節點評分比較</h2>
              <p className="text-sm text-muted-foreground">
                各節點的平衡分數、資源佔比與懲罰項，分數越低代表資源越均衡，演算法會選擇分數最低的節點。
              </p>
            </div>
          </div>
          <div className="mt-4 grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
            {context.node_scores
              .slice()
              .sort((a, b) => a.balance_score - b.balance_score)
              .map((score) => (
                <NodeScoreCard key={score.node} score={score} />
              ))}
          </div>
        </section>
      )}

      <section className="glass-panel rounded-2xl p-4">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/70 pb-3">
          <div>
            <h2 className="text-lg font-semibold">PVE Stack 視圖</h2>
            <p className="text-sm text-muted-foreground">
              以節點堆疊顯示目前在線資源，以及這筆申請加入後該時段的預計配置。
            </p>
          </div>
        </div>
        <div className="mt-4 grid gap-4 lg:grid-cols-2 xl:grid-cols-4">
          {pveStacks.length > 0 ? (
            pveStacks.map((stack) => (
              <PveStackColumn
                key={stack.node}
                node={stack.node}
                items={stack.items}
              />
            ))
          ) : (
            <div className="rounded-lg border border-dashed px-3 py-3 text-sm text-muted-foreground">
              目前沒有可顯示的節點堆疊資料。
            </div>
          )}
        </div>
      </section>

      <section className="glass-panel rounded-2xl p-4">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/70 pb-3">
          <div>
            <h2 className="text-lg font-semibold">目前在線資源</h2>
            <p className="text-sm text-muted-foreground">
              審核當下叢集內正在執行的資源，這一區只看現在，不代表該申請時段的最終落點。
            </p>
          </div>
        </div>
        <div className="mt-4 grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
          {context.current_running_resources.length > 0 ? (
            context.current_running_resources.map((resource) => (
              <RuntimeResourceCard
                key={`${resource.node}-${resource.vmid}`}
                resource={resource}
              />
            ))
          ) : (
            <div className="rounded-lg border border-dashed px-3 py-3 text-sm text-muted-foreground">
              SkyLab pool 目前沒有在線資源。
            </div>
          )}
        </div>
      </section>

      <section className="glass-panel rounded-2xl p-4">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/70 pb-3">
          <div>
            <h2 className="text-lg font-semibold">同時段核准後預測</h2>
            <p className="text-sm text-muted-foreground">
              顯示與這筆申請時段重疊的已通過申請，以及這筆申請加入後的預測節點與搬移結果。
            </p>
          </div>
        </div>
        <div className="mt-4 space-y-3">
          {context.overlapping_approved_requests.map((item) => (
            <OverlapCard key={item.request_id} item={item} />
          ))}
        </div>
      </section>
    </div>
  )
}

export default VMRequestReviewPage
