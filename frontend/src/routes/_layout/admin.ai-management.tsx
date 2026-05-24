import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import type { ColumnDef } from "@tanstack/react-table"
import {
  Activity,
  BarChart3,
  Bot,
  BrainCircuit,
  Check,
  ClipboardCheck,
  FileText,
  KeyRound,
  Trash2,
  Users,
  X,
  Zap,
} from "lucide-react"
import { useMemo, useState } from "react"
import { DataTable } from "@/components/Common/DataTable"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
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
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { LoadingButton } from "@/components/ui/loading-button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import {
  aiApiAdminCredentialsCountQueryOptions,
  aiApiAdminCredentialsQueryOptions,
  aiApiAdminRequestsQueryOptions,
} from "@/features/aiApi/queryOptions"
import { requireAdminUser } from "@/features/auth/guards"
import useCustomToast from "@/hooks/useCustomToast"
import { queryKeys } from "@/lib/queryKeys"
import {
  type AiApiCredentialAdminPublic,
  type AiApiCredentialAdminStatus,
  type AiApiRequestPublic,
  type AiApiRequestStatus,
  AiApiService,
} from "@/services/aiApi"
import {
  type AIProxyCallRecord,
  type AITemplateCallRecord,
  type AIUserUsageSummary,
  AiAdminMonitoringService,
} from "@/services/aiMonitoring"
import { handleError } from "@/utils"

export const Route = createFileRoute("/_layout/admin/ai-management")({
  component: AdminAiManagementPage,
  beforeLoad: () => requireAdminUser(),
  head: () => ({
    meta: [{ title: "AI 管理中心 - SkyLab" }],
  }),
})

// ─── Constants ────────────────────────────────────────────────────────────────

const PAGE_SIZE = 50
const LIMIT = 50

// ─── Utility functions ────────────────────────────────────────────────────────

function formatTime(value?: string | null) {
  if (!value) return "-"
  return new Date(value).toLocaleString()
}

function formatTimeReview(value?: string | null) {
  if (!value) return "尚未審核"
  return new Date(value).toLocaleString()
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

function formatDuration(ms?: number | null): string {
  if (ms == null) return "-"
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`
  return `${ms}ms`
}

function formatModelDisplay(modelName?: string | null): string {
  if (!modelName) return "-"
  const trimmed = modelName.trim()
  if (!trimmed) return "-"
  const match = trimmed.match(/models--([^/]+)--([^/]+)/)
  if (!match) return trimmed
  return `${match[1]}/${match[2]}`
}

function inactiveReasonLabel(reason?: "revoked" | "expired" | null) {
  if (!reason) return "-"
  return reason === "revoked" ? "已撤銷" : "已過期"
}

type DatePreset = "7d" | "30d" | "90d" | "custom"

function getPresetDates(preset: Exclude<DatePreset, "custom">): {
  start: string
  end: string
} {
  const now = new Date()
  const end = now.toISOString().split("T")[0]!
  const start = new Date(now)
  if (preset === "7d") start.setDate(start.getDate() - 7)
  else if (preset === "30d") start.setDate(start.getDate() - 30)
  else if (preset === "90d") start.setDate(start.getDate() - 90)
  return { start: start.toISOString().split("T")[0]!, end }
}

// ─── Shared UI Components ─────────────────────────────────────────────────────

function RequestStatusBadge({ status }: { status: AiApiRequestStatus }) {
  const cls = {
    approved: "border-emerald-500/20 bg-emerald-500/10 text-emerald-700",
    rejected: "border-destructive/20 bg-destructive/10 text-destructive",
    pending: "border-amber-500/20 bg-amber-500/10 text-amber-700",
  }[status]
  const label = {
    approved: "已通過",
    rejected: "已拒絕",
    pending: "待審核",
  }[status]
  return (
    <span
      className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-medium ${cls}`}
    >
      {label}
    </span>
  )
}

function CredentialStatusBadge({ item }: { item: AiApiCredentialAdminPublic }) {
  if (item.status === "active") {
    return <Badge className="bg-emerald-600">啟用</Badge>
  }
  return <Badge variant="destructive">失效</Badge>
}

function CallStatusBadge({ status }: { status: string }) {
  const cls =
    status === "success"
      ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-700"
      : "border-destructive/20 bg-destructive/10 text-destructive"
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${cls}`}
    >
      {status}
    </span>
  )
}

function StatCard({
  icon: Icon,
  label,
  value,
  sub,
  color,
}: {
  icon: React.ElementType
  label: string
  value: string | number
  sub?: string
  color?: string
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardDescription className="text-sm font-medium">
          {label}
        </CardDescription>
        <Icon className={`h-4 w-4 ${color ?? "text-muted-foreground"}`} />
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
        {sub ? (
          <p className="mt-1 text-xs text-muted-foreground">{sub}</p>
        ) : null}
      </CardContent>
    </Card>
  )
}

function PaginationBar({
  page,
  total,
  limit,
  onPrev,
  onNext,
}: {
  page: number
  total: number
  limit: number
  onPrev: () => void
  onNext: () => void
}) {
  const totalPages = Math.max(1, Math.ceil(total / limit))
  if (totalPages <= 1) return null
  return (
    <div className="mt-4 flex items-center justify-between">
      <div className="text-sm text-muted-foreground">
        第 {page + 1} / {totalPages} 頁，共 {total} 筆
      </div>
      <div className="flex gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={onPrev}
          disabled={page === 0}
        >
          上一頁
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={onNext}
          disabled={page >= totalPages - 1}
        >
          下一頁
        </Button>
      </div>
    </div>
  )
}

function UserCell({
  email,
  fullName,
}: {
  email?: string | null
  fullName?: string | null
}) {
  return (
    <div>
      <div className="text-sm font-medium">{fullName || "-"}</div>
      {email ? (
        <div className="text-xs text-muted-foreground">{email}</div>
      ) : null}
    </div>
  )
}

// ─── Overview Card ────────────────────────────────────────────────────────────

function OverviewCard({
  icon: Icon,
  label,
  value,
  sub,
  urgent,
  color,
  onClick,
}: {
  icon: React.ElementType
  label: string
  value: string | number
  sub?: string
  urgent?: boolean
  color?: string
  onClick?: () => void
}) {
  return (
    <Card
      className={[
        "transition-colors",
        onClick ? "cursor-pointer hover:bg-accent/50" : "",
        urgent ? "border-amber-500/50 bg-amber-500/5" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      onClick={onClick}
    >
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardDescription className="text-sm font-medium">
          {label}
        </CardDescription>
        <Icon
          className={`h-4 w-4 ${urgent ? "text-amber-500" : (color ?? "text-muted-foreground")}`}
        />
      </CardHeader>
      <CardContent>
        <div
          className={`text-2xl font-bold ${urgent && value !== 0 ? "text-amber-600" : ""}`}
        >
          {value}
        </div>
        {sub ? (
          <p className="mt-1 text-xs text-muted-foreground">{sub}</p>
        ) : null}
      </CardContent>
    </Card>
  )
}

// ─── Tab 1: 審核申請 ──────────────────────────────────────────────────────────

function ReviewDialog({
  open,
  onOpenChange,
  request,
  action,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  request: AiApiRequestPublic
  action: "approved" | "rejected"
}) {
  const [comment, setComment] = useState("")
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const mutation = useMutation({
    mutationFn: () =>
      AiApiService.reviewRequest({
        requestId: request.id,
        requestBody: {
          status: action,
          review_comment: comment || null,
        },
      }),
    onSuccess: () => {
      showSuccessToast(
        action === "approved" ? "AI API 申請已通過" : "AI API 申請已拒絕",
      )
      setComment("")
      onOpenChange(false)
      queryClient.invalidateQueries({ queryKey: queryKeys.aiApi.adminRequests })
      queryClient.invalidateQueries({ queryKey: queryKeys.aiApi.all })
    },
    onError: handleError.bind(showErrorToast),
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {action === "approved" ? "通過 AI API 申請" : "拒絕 AI API 申請"}
          </DialogTitle>
          <DialogDescription>
            {action === "approved"
              ? "通過後，系統會直接核發可用的 base_url 與 api_key。"
              : "你可以留下拒絕原因，讓申請者知道下一步。"}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          <div className="space-y-2 rounded-lg border p-3 text-sm">
            <div>申請者：{request.user_full_name || request.user_email}</div>
            <div>申請時間：{formatTimeReview(request.created_at)}</div>
            <div className="whitespace-pre-wrap text-muted-foreground">
              用途：{request.purpose}
            </div>
          </div>
          <Textarea
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="審核備註（可留空）"
            rows={4}
          />
        </div>

        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline" disabled={mutation.isPending}>
              取消
            </Button>
          </DialogClose>
          <LoadingButton
            loading={mutation.isPending}
            onClick={() => mutation.mutate()}
            variant={action === "approved" ? "default" : "destructive"}
          >
            {action === "approved" ? "確認通過" : "確認拒絕"}
          </LoadingButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ReviewActionCell({ item }: { item: AiApiRequestPublic }) {
  const [approveOpen, setApproveOpen] = useState(false)
  const [rejectOpen, setRejectOpen] = useState(false)

  return (
    <div className="flex min-w-[180px] items-center gap-2">
      {item.status === "pending" ? (
        <>
          <Button size="sm" onClick={() => setApproveOpen(true)}>
            <Check className="mr-1 h-4 w-4" />
            通過
          </Button>
          <Button
            size="sm"
            variant="destructive"
            onClick={() => setRejectOpen(true)}
          >
            <X className="mr-1 h-4 w-4" />
            拒絕
          </Button>
        </>
      ) : (
        <span className="truncate text-sm text-muted-foreground">
          {item.review_comment || "-"}
        </span>
      )}
      <ReviewDialog
        open={approveOpen}
        onOpenChange={setApproveOpen}
        request={item}
        action="approved"
      />
      <ReviewDialog
        open={rejectOpen}
        onOpenChange={setRejectOpen}
        request={item}
        action="rejected"
      />
    </div>
  )
}

function ReviewTabContent() {
  const [statusFilter, setStatusFilter] = useState<AiApiRequestStatus | "all">(
    "pending",
  )
  const requestsQuery = useQuery(aiApiAdminRequestsQueryOptions(statusFilter))

  const columns = useMemo<ColumnDef<AiApiRequestPublic>[]>(
    () => [
      {
        id: "applicant",
        header: "申請者",
        cell: ({ row }) => (
          <div className="min-w-[180px] max-w-[220px] overflow-hidden">
            <div className="truncate font-medium">
              {row.original.user_full_name || row.original.user_email}
            </div>
            <div className="truncate text-xs text-muted-foreground">
              {row.original.user_email}
            </div>
          </div>
        ),
      },
      {
        accessorKey: "api_key_name",
        header: "金鑰名稱",
        cell: ({ row }) => (
          <span className="block min-w-[140px] max-w-[180px] truncate">
            {row.original.api_key_name}
          </span>
        ),
      },
      {
        accessorKey: "purpose",
        header: "用途",
        cell: ({ row }) => (
          <span className="block min-w-[260px] max-w-[360px] truncate text-muted-foreground">
            {row.original.purpose}
          </span>
        ),
      },
      {
        accessorKey: "status",
        header: "狀態",
        cell: ({ row }) => <RequestStatusBadge status={row.original.status} />,
      },
      {
        accessorKey: "created_at",
        header: "申請時間",
        cell: ({ row }) => (
          <span className="block min-w-[170px] text-sm text-muted-foreground">
            {formatTimeReview(row.original.created_at)}
          </span>
        ),
      },
      {
        id: "reviewed_at",
        header: "審核時間",
        cell: ({ row }) => (
          <span className="block min-w-[170px] text-sm text-muted-foreground">
            {formatTimeReview(row.original.reviewed_at)}
          </span>
        ),
      },
      {
        id: "actions",
        header: "操作",
        cell: ({ row }) => <ReviewActionCell item={row.original} />,
      },
    ],
    [],
  )

  return (
    <div className="flex flex-col gap-4">
      <Tabs
        value={statusFilter}
        onValueChange={(v) => setStatusFilter(v as AiApiRequestStatus | "all")}
      >
        <TabsList>
          <TabsTrigger value="pending">待審核</TabsTrigger>
          <TabsTrigger value="approved">已通過</TabsTrigger>
          <TabsTrigger value="rejected">已拒絕</TabsTrigger>
          <TabsTrigger value="all">全部</TabsTrigger>
        </TabsList>
      </Tabs>

      {requestsQuery.data?.data.length ? (
        <DataTable columns={columns} data={requestsQuery.data.data} />
      ) : (
        <div className="flex flex-col items-center justify-center rounded-xl border border-dashed py-12 text-center">
          <div className="mb-4 rounded-full bg-muted p-4">
            <ClipboardCheck className="h-8 w-8 text-muted-foreground" />
          </div>
          <div className="font-medium">目前沒有符合條件的 AI API 申請</div>
        </div>
      )}
    </div>
  )
}

// ─── Tab 2: 金鑰管理 ──────────────────────────────────────────────────────────

function KeysTabContent({
  allCount,
  activeCount,
  inactiveCount,
}: {
  allCount: number
  activeCount: number
  inactiveCount: number
}) {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [statusFilter, setStatusFilter] = useState<
    "all" | AiApiCredentialAdminStatus
  >("all")
  const [userEmail, setUserEmail] = useState("")
  const [page, setPage] = useState(0)
  const [deletingItem, setDeletingItem] =
    useState<AiApiCredentialAdminPublic | null>(null)

  const listQuery = useQuery(
    aiApiAdminCredentialsQueryOptions({
      skip: page * PAGE_SIZE,
      limit: PAGE_SIZE,
      status: statusFilter,
      userEmail,
    }),
  )

  const deleteMutation = useMutation({
    mutationFn: (credentialId: string) =>
      AiApiService.deleteCredential({ credentialId }),
    onSuccess: () => {
      showSuccessToast("金鑰已刪除")
      setDeletingItem(null)
      queryClient.invalidateQueries({
        queryKey: queryKeys.aiApi.adminCredentials,
      })
    },
    onError: handleError.bind((message: string) =>
      showErrorToast(`刪除失敗：${message}`),
    ),
  })

  const rows = listQuery.data?.data ?? []
  const total = listQuery.data?.count ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="flex flex-col gap-4">
      {/* Sub stat cards — shared counts from parent, no extra requests */}
      <div className="grid gap-4 sm:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>全部紀錄</CardDescription>
            <CardTitle className="text-3xl">{allCount}</CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>啟用</CardDescription>
            <CardTitle className="text-3xl text-emerald-600">
              {activeCount}
            </CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>失效</CardDescription>
            <CardTitle className="text-3xl text-red-600">
              {inactiveCount}
            </CardTitle>
          </CardHeader>
        </Card>
      </div>

      {/* Filter */}
      <Card>
        <CardHeader>
          <CardTitle>篩選</CardTitle>
          <CardDescription>可依狀態與使用者 Email 篩選。</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 sm:grid-cols-2">
            <Select
              value={statusFilter}
              onValueChange={(v) => {
                setStatusFilter(v as "all" | AiApiCredentialAdminStatus)
                setPage(0)
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder="選擇狀態" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部</SelectItem>
                <SelectItem value="active">啟用</SelectItem>
                <SelectItem value="inactive">失效</SelectItem>
              </SelectContent>
            </Select>
            <Input
              placeholder="使用者 Email 關鍵字"
              value={userEmail}
              onChange={(e) => {
                setUserEmail(e.target.value)
                setPage(0)
              }}
            />
          </div>
        </CardContent>
      </Card>

      {/* Table */}
      <Card>
        <CardHeader>
          <CardTitle>金鑰清單</CardTitle>
          <CardDescription>
            共 {total} 筆，狀態定義：啟用(active)、失效(inactive)。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {listQuery.isLoading ? (
            <div className="text-sm text-muted-foreground">讀取中...</div>
          ) : rows.length ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>使用者</TableHead>
                  <TableHead>金鑰名稱</TableHead>
                  <TableHead>金鑰前綴</TableHead>
                  <TableHead>狀態</TableHead>
                  <TableHead>失效原因</TableHead>
                  <TableHead>建立時間</TableHead>
                  <TableHead>過期時間</TableHead>
                  <TableHead>撤銷時間</TableHead>
                  <TableHead>操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell
                      className="max-w-60 truncate"
                      title={item.user_email || "-"}
                    >
                      {item.user_full_name
                        ? `${item.user_full_name} (${item.user_email || "-"})`
                        : (item.user_email ?? "-")}
                    </TableCell>
                    <TableCell>{item.api_key_name}</TableCell>
                    <TableCell className="font-mono">
                      {item.api_key_prefix}
                    </TableCell>
                    <TableCell>
                      <CredentialStatusBadge item={item} />
                    </TableCell>
                    <TableCell>
                      {inactiveReasonLabel(item.inactive_reason)}
                    </TableCell>
                    <TableCell>{formatTime(item.created_at)}</TableCell>
                    <TableCell>{formatTime(item.expires_at)}</TableCell>
                    <TableCell>{formatTime(item.revoked_at)}</TableCell>
                    <TableCell>
                      <Button
                        size="sm"
                        variant="destructive"
                        onClick={() => setDeletingItem(item)}
                        disabled={deleteMutation.isPending}
                      >
                        <Trash2 className="mr-1.5 h-4 w-4" />
                        刪除
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <div className="rounded-xl border border-dashed py-10 text-center text-sm text-muted-foreground">
              目前沒有符合條件的金鑰紀錄。
            </div>
          )}

          <div className="flex items-center justify-between">
            <div className="text-sm text-muted-foreground">
              第 {page + 1} / {totalPages} 頁
            </div>
            <div className="flex gap-2">
              <Button
                variant="outline"
                disabled={page === 0}
                onClick={() => setPage((p) => Math.max(0, p - 1))}
              >
                上一頁
              </Button>
              <Button
                variant="outline"
                disabled={page + 1 >= totalPages}
                onClick={() =>
                  setPage((p) => (p + 1 >= totalPages ? p : p + 1))
                }
              >
                下一頁
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <AlertDialog
        open={Boolean(deletingItem)}
        onOpenChange={(open) => {
          if (!open && !deleteMutation.isPending) setDeletingItem(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>確認刪除這把金鑰？</AlertDialogTitle>
            <AlertDialogDescription>
              你即將刪除
              {deletingItem ? `「${deletingItem.api_key_name}」` : "這筆資料"}。
              這個動作無法復原。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteMutation.isPending}>
              取消
            </AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              disabled={deleteMutation.isPending || !deletingItem}
              onClick={(e) => {
                e.preventDefault()
                if (!deletingItem) return
                deleteMutation.mutate(deletingItem.id)
              }}
            >
              {deleteMutation.isPending ? "刪除中..." : "確認刪除"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

// ─── Tab 3: 用量監控 — Sub-tabs ───────────────────────────────────────────────

function ProxyCallsTab({
  startDate,
  endDate,
}: {
  startDate: string
  endDate: string
}) {
  const [page, setPage] = useState(0)
  const [modelFilter, setModelFilter] = useState("")
  const [statusFilter, setStatusFilter] = useState("")

  const params = useMemo(
    () => ({
      start_date: startDate,
      end_date: endDate,
      model_name: modelFilter || undefined,
      status: statusFilter || undefined,
      skip: page * LIMIT,
      limit: LIMIT,
    }),
    [startDate, endDate, modelFilter, statusFilter, page],
  )

  const { data } = useQuery({
    queryKey: queryKeys.aiMonitoring.apiCalls(params),
    queryFn: () => AiAdminMonitoringService.listApiCalls(params),
  })

  const records = data?.data ?? []
  const total = data?.count ?? 0

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-3">
        <Input
          placeholder="篩選模型名稱"
          value={modelFilter}
          onChange={(e) => {
            setModelFilter(e.target.value)
            setPage(0)
          }}
          className="w-48"
        />
        <Select
          value={statusFilter || "__all__"}
          onValueChange={(v) => {
            setStatusFilter(v === "__all__" ? "" : v)
            setPage(0)
          }}
        >
          <SelectTrigger className="w-36">
            <SelectValue placeholder="所有狀態" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__all__">所有狀態</SelectItem>
            <SelectItem value="success">success</SelectItem>
            <SelectItem value="error">error</SelectItem>
          </SelectContent>
        </Select>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            setModelFilter("")
            setStatusFilter("")
            setPage(0)
          }}
        >
          重設
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Proxy 呼叫紀錄</CardTitle>
          <CardDescription>共 {total} 筆</CardDescription>
        </CardHeader>
        <CardContent>
          {records.length === 0 ? (
            <div className="py-8 text-center text-muted-foreground">
              此時段無紀錄
            </div>
          ) : (
            <>
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[160px]">時間</TableHead>
                      <TableHead>使用者</TableHead>
                      <TableHead>模型</TableHead>
                      <TableHead>類型</TableHead>
                      <TableHead className="text-right">輸入</TableHead>
                      <TableHead className="text-right">輸出</TableHead>
                      <TableHead className="text-right">耗時</TableHead>
                      <TableHead>狀態</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {records.map((row: AIProxyCallRecord) => (
                      <TableRow key={row.id}>
                        <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                          {new Date(row.created_at).toLocaleString()}
                        </TableCell>
                        <TableCell>
                          <UserCell
                            email={row.user_email}
                            fullName={row.user_full_name}
                          />
                        </TableCell>
                        <TableCell
                          className="font-mono text-sm"
                          title={row.model_name}
                        >
                          {formatModelDisplay(row.model_name)}
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {row.request_type}
                        </TableCell>
                        <TableCell className="text-right font-mono text-sm">
                          {formatTokens(row.input_tokens)}
                        </TableCell>
                        <TableCell className="text-right font-mono text-sm">
                          {formatTokens(row.output_tokens)}
                        </TableCell>
                        <TableCell className="text-right text-xs text-muted-foreground">
                          {formatDuration(row.request_duration_ms)}
                        </TableCell>
                        <TableCell>
                          <CallStatusBadge status={row.status} />
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              <PaginationBar
                page={page}
                total={total}
                limit={LIMIT}
                onPrev={() => setPage((p) => Math.max(0, p - 1))}
                onNext={() => setPage((p) => p + 1)}
              />
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function TemplateCallsTab({
  startDate,
  endDate,
}: {
  startDate: string
  endDate: string
}) {
  const [page, setPage] = useState(0)
  const [callTypeFilter, setCallTypeFilter] = useState("")
  const [statusFilter, setStatusFilter] = useState("")

  const params = useMemo(
    () => ({
      start_date: startDate,
      end_date: endDate,
      call_type: callTypeFilter || undefined,
      status: statusFilter || undefined,
      skip: page * LIMIT,
      limit: LIMIT,
    }),
    [startDate, endDate, callTypeFilter, statusFilter, page],
  )

  const { data } = useQuery({
    queryKey: queryKeys.aiMonitoring.templateCalls(params),
    queryFn: () => AiAdminMonitoringService.listTemplateCalls(params),
  })

  const records = data?.data ?? []
  const total = data?.count ?? 0

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-3">
        <Input
          placeholder="篩選呼叫類型"
          value={callTypeFilter}
          onChange={(e) => {
            setCallTypeFilter(e.target.value)
            setPage(0)
          }}
          className="w-48"
        />
        <Select
          value={statusFilter || "__all__"}
          onValueChange={(v) => {
            setStatusFilter(v === "__all__" ? "" : v)
            setPage(0)
          }}
        >
          <SelectTrigger className="w-36">
            <SelectValue placeholder="所有狀態" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__all__">所有狀態</SelectItem>
            <SelectItem value="success">success</SelectItem>
            <SelectItem value="error">error</SelectItem>
          </SelectContent>
        </Select>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            setCallTypeFilter("")
            setStatusFilter("")
            setPage(0)
          }}
        >
          重設
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Template 呼叫紀錄</CardTitle>
          <CardDescription>共 {total} 筆</CardDescription>
        </CardHeader>
        <CardContent>
          {records.length === 0 ? (
            <div className="py-8 text-center text-muted-foreground">
              此時段無紀錄
            </div>
          ) : (
            <>
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[160px]">時間</TableHead>
                      <TableHead>使用者</TableHead>
                      <TableHead>呼叫類型</TableHead>
                      <TableHead>模型</TableHead>
                      <TableHead>Preset</TableHead>
                      <TableHead className="text-right">輸入</TableHead>
                      <TableHead className="text-right">輸出</TableHead>
                      <TableHead className="text-right">耗時</TableHead>
                      <TableHead>狀態</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {records.map((row: AITemplateCallRecord) => (
                      <TableRow key={row.id}>
                        <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                          {new Date(row.created_at).toLocaleString()}
                        </TableCell>
                        <TableCell>
                          <UserCell
                            email={row.user_email}
                            fullName={row.user_full_name}
                          />
                        </TableCell>
                        <TableCell className="text-sm">
                          {row.call_type}
                        </TableCell>
                        <TableCell
                          className="font-mono text-sm"
                          title={row.model_name}
                        >
                          {formatModelDisplay(row.model_name)}
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {row.preset ?? "-"}
                        </TableCell>
                        <TableCell className="text-right font-mono text-sm">
                          {formatTokens(row.input_tokens)}
                        </TableCell>
                        <TableCell className="text-right font-mono text-sm">
                          {formatTokens(row.output_tokens)}
                        </TableCell>
                        <TableCell className="text-right text-xs text-muted-foreground">
                          {formatDuration(row.request_duration_ms)}
                        </TableCell>
                        <TableCell>
                          <CallStatusBadge status={row.status} />
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              <PaginationBar
                page={page}
                total={total}
                limit={LIMIT}
                onPrev={() => setPage((p) => Math.max(0, p - 1))}
                onNext={() => setPage((p) => p + 1)}
              />
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function UsersUsageTab({
  startDate,
  endDate,
}: {
  startDate: string
  endDate: string
}) {
  const [page, setPage] = useState(0)

  const params = useMemo(
    () => ({
      start_date: startDate,
      end_date: endDate,
      skip: page * LIMIT,
      limit: LIMIT,
    }),
    [startDate, endDate, page],
  )

  const { data } = useQuery({
    queryKey: queryKeys.aiMonitoring.usersUsage(params),
    queryFn: () => AiAdminMonitoringService.listUsersUsage(params),
  })

  const rows = data?.data ?? []
  const total = data?.count ?? 0

  return (
    <Card>
      <CardHeader>
        <CardTitle>使用者用量彙總</CardTitle>
        <CardDescription>共 {total} 位使用者</CardDescription>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <div className="py-8 text-center text-muted-foreground">
            此時段無紀錄
          </div>
        ) : (
          <>
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>使用者</TableHead>
                    <TableHead className="text-right">Proxy 呼叫</TableHead>
                    <TableHead className="text-right">Proxy 輸入</TableHead>
                    <TableHead className="text-right">Proxy 輸出</TableHead>
                    <TableHead className="text-right">Template 呼叫</TableHead>
                    <TableHead className="text-right">Template 輸入</TableHead>
                    <TableHead className="text-right">Template 輸出</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row: AIUserUsageSummary) => (
                    <TableRow key={row.user_id}>
                      <TableCell>
                        <UserCell
                          email={row.user_email}
                          fullName={row.user_full_name}
                        />
                      </TableCell>
                      <TableCell className="text-right font-mono text-sm">
                        {row.proxy_calls}
                      </TableCell>
                      <TableCell className="text-right font-mono text-sm">
                        {formatTokens(row.proxy_input_tokens)}
                      </TableCell>
                      <TableCell className="text-right font-mono text-sm">
                        {formatTokens(row.proxy_output_tokens)}
                      </TableCell>
                      <TableCell className="text-right font-mono text-sm">
                        {row.template_calls}
                      </TableCell>
                      <TableCell className="text-right font-mono text-sm">
                        {formatTokens(row.template_input_tokens)}
                      </TableCell>
                      <TableCell className="text-right font-mono text-sm">
                        {formatTokens(row.template_output_tokens)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <PaginationBar
              page={page}
              total={total}
              limit={LIMIT}
              onPrev={() => setPage((p) => Math.max(0, p - 1))}
              onNext={() => setPage((p) => p + 1)}
            />
          </>
        )}
      </CardContent>
    </Card>
  )
}

// ─── Tab 3: 用量監控 — Main ───────────────────────────────────────────────────

function MonitoringTabContent() {
  const [preset, setPreset] = useState<DatePreset>("30d")
  const [customStart, setCustomStart] = useState("")
  const [customEnd, setCustomEnd] = useState("")

  const { start_date, end_date } = useMemo(() => {
    if (preset === "custom") {
      return { start_date: customStart, end_date: customEnd }
    }
    const { start, end } = getPresetDates(preset)
    return { start_date: start, end_date: end }
  }, [preset, customStart, customEnd])

  const statsParams = useMemo(
    () => ({ start_date, end_date }),
    [start_date, end_date],
  )

  const { data: stats, isError: statsIsError } = useQuery({
    queryKey: queryKeys.aiMonitoring.stats(statsParams),
    queryFn: () => AiAdminMonitoringService.getStats(statsParams),
    enabled: Boolean(start_date && end_date),
  })

  return (
    <div className="flex flex-col gap-6">
      {/* Date range controls */}
      <div className="flex flex-wrap items-center gap-2">
        {(["7d", "30d", "90d"] as const).map((p) => (
          <Button
            key={p}
            size="sm"
            variant={preset === p ? "default" : "outline"}
            onClick={() => setPreset(p)}
          >
            {p === "7d"
              ? "過去 7 天"
              : p === "30d"
                ? "過去 30 天"
                : "過去 90 天"}
          </Button>
        ))}
        <Button
          size="sm"
          variant={preset === "custom" ? "default" : "outline"}
          onClick={() => setPreset("custom")}
        >
          自訂
        </Button>
        {preset === "custom" && (
          <>
            <Input
              type="date"
              value={customStart}
              onChange={(e) => setCustomStart(e.target.value)}
              className="h-8 w-40"
            />
            <span className="text-muted-foreground">—</span>
            <Input
              type="date"
              value={customEnd}
              onChange={(e) => setCustomEnd(e.target.value)}
              className="h-8 w-40"
            />
          </>
        )}
        <span className="text-xs text-muted-foreground">
          {start_date} ~ {end_date}
        </span>
      </div>

      {/* Stats error banner */}
      {statsIsError && (
        <div className="rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          無法載入統計資料，請確認後端服務正常後重新整理。
        </div>
      )}

      {/* Stats cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          icon={Zap}
          label="Proxy 呼叫次數"
          value={stats?.proxy_total_calls ?? "-"}
          color="text-blue-500"
        />
        <StatCard
          icon={BarChart3}
          label="Proxy Tokens（輸入）"
          value={stats ? formatTokens(stats.proxy_total_input_tokens) : "-"}
          color="text-blue-400"
        />
        <StatCard
          icon={BarChart3}
          label="Proxy Tokens（輸出）"
          value={stats ? formatTokens(stats.proxy_total_output_tokens) : "-"}
          color="text-blue-300"
        />
        <StatCard
          icon={Activity}
          label="活躍使用者"
          value={stats?.active_users ?? "-"}
          color="text-green-500"
        />
        <StatCard
          icon={BrainCircuit}
          label="Template 呼叫次數"
          value={stats?.template_total_calls ?? "-"}
          color="text-purple-500"
        />
        <StatCard
          icon={FileText}
          label="Template Tokens（輸入）"
          value={stats ? formatTokens(stats.template_total_input_tokens) : "-"}
          color="text-purple-400"
        />
        <StatCard
          icon={FileText}
          label="Template Tokens（輸出）"
          value={stats ? formatTokens(stats.template_total_output_tokens) : "-"}
          color="text-purple-300"
        />
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardDescription className="text-sm font-medium">
              使用中模型
            </CardDescription>
            <Bot className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            {stats?.models_used && stats.models_used.length > 0 ? (
              <div className="space-y-1">
                {stats.models_used.map((m) => (
                  <div key={m} className="truncate font-mono text-sm" title={m}>
                    {formatModelDisplay(m)}
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-2xl font-bold">-</div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Detail sub-tabs */}
      <Tabs defaultValue="proxy" className="space-y-5">
        <TabsList className="grid h-auto w-full grid-cols-3 p-1 md:w-[480px]">
          <TabsTrigger value="proxy">Proxy 呼叫</TabsTrigger>
          <TabsTrigger value="template">Template 呼叫</TabsTrigger>
          <TabsTrigger value="users">
            <Users className="mr-1.5 h-3.5 w-3.5" />
            使用者彙總
          </TabsTrigger>
        </TabsList>

        <TabsContent value="proxy">
          {start_date && end_date ? (
            <ProxyCallsTab startDate={start_date} endDate={end_date} />
          ) : (
            <div className="text-sm text-muted-foreground">
              請選擇日期範圍。
            </div>
          )}
        </TabsContent>

        <TabsContent value="template">
          {start_date && end_date ? (
            <TemplateCallsTab startDate={start_date} endDate={end_date} />
          ) : (
            <div className="text-sm text-muted-foreground">
              請選擇日期範圍。
            </div>
          )}
        </TabsContent>

        <TabsContent value="users">
          {start_date && end_date ? (
            <UsersUsageTab startDate={start_date} endDate={end_date} />
          ) : (
            <div className="text-sm text-muted-foreground">
              請選擇日期範圍。
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  )
}

// ─── Main Page ────────────────────────────────────────────────────────────────

function AdminAiManagementPage() {
  const [activeTab, setActiveTab] = useState("review")

  // Shared credential count queries — used by both overview cards and Keys tab
  const allCountQuery = useQuery(aiApiAdminCredentialsCountQueryOptions("all"))
  const activeCountQuery = useQuery(
    aiApiAdminCredentialsCountQueryOptions("active"),
  )
  const inactiveCountQuery = useQuery(
    aiApiAdminCredentialsCountQueryOptions("inactive"),
  )

  // Pending requests count for overview card
  const pendingRequestsQuery = useQuery(
    aiApiAdminRequestsQueryOptions("pending"),
  )

  // 7-day stats for overview cards (fixed window, not affected by monitoring tab's date picker)
  const stats7dDates = useMemo(() => getPresetDates("7d"), [])
  const stats7dQuery = useQuery({
    queryKey: queryKeys.aiMonitoring.stats({
      start_date: stats7dDates.start,
      end_date: stats7dDates.end,
    }),
    queryFn: () =>
      AiAdminMonitoringService.getStats({
        start_date: stats7dDates.start,
        end_date: stats7dDates.end,
      }),
  })

  const pendingCount =
    pendingRequestsQuery.data?.count ??
    pendingRequestsQuery.data?.data?.length ??
    0
  const activeKeysCount = activeCountQuery.data?.count ?? 0
  const inactiveKeysCount = inactiveCountQuery.data?.count ?? 0
  const allKeysCount = allCountQuery.data?.count ?? 0

  const stats7d = stats7dQuery.data
  const totalTokens7d = stats7d
    ? stats7d.proxy_total_input_tokens +
      stats7d.proxy_total_output_tokens +
      stats7d.template_total_input_tokens +
      stats7d.template_total_output_tokens
    : null

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="space-y-1">
        <div className="text-xs uppercase tracking-[0.22em] text-muted-foreground">
          SkyLab Admin
        </div>
        <h1 className="text-2xl font-bold tracking-tight">AI 管理中心</h1>
        <p className="text-sm text-muted-foreground">
          統一管理 AI API 申請審核、金鑰狀態與系統用量監控。
        </p>
      </div>

      {/* Overview cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <OverviewCard
          icon={ClipboardCheck}
          label="待審核申請"
          value={pendingCount}
          urgent={pendingCount > 0}
          sub={pendingCount > 0 ? "點擊前往審核" : "目前無待審"}
          onClick={() => setActiveTab("review")}
        />
        <OverviewCard
          icon={KeyRound}
          label="活躍金鑰"
          value={activeKeysCount}
          color="text-emerald-500"
          sub={`失效 ${inactiveKeysCount} 筆，共 ${allKeysCount} 筆`}
          onClick={() => setActiveTab("keys")}
        />
        <OverviewCard
          icon={Zap}
          label="近 7 天 Tokens"
          value={totalTokens7d != null ? formatTokens(totalTokens7d) : "-"}
          color="text-blue-500"
          sub="Proxy + Template 合計"
          onClick={() => setActiveTab("monitoring")}
        />
        <OverviewCard
          icon={Activity}
          label="近 7 天活躍用戶"
          value={stats7d?.active_users ?? "-"}
          color="text-green-500"
          sub="有 AI 使用紀錄的用戶"
          onClick={() => setActiveTab("monitoring")}
        />
      </div>

      {/* Main tabs */}
      <Tabs
        value={activeTab}
        onValueChange={setActiveTab}
        className="space-y-4"
      >
        <TabsList className="h-auto p-1 sm:inline-flex">
          <TabsTrigger value="review" className="gap-1.5">
            <ClipboardCheck className="h-4 w-4" />
            審核申請
            {pendingCount > 0 && (
              <Badge className="ml-0.5 h-5 min-w-5 rounded-full bg-amber-500 px-1.5 text-xs text-white">
                {pendingCount}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="keys" className="gap-1.5">
            <KeyRound className="h-4 w-4" />
            金鑰管理
          </TabsTrigger>
          <TabsTrigger value="monitoring" className="gap-1.5">
            <BarChart3 className="h-4 w-4" />
            用量監控
          </TabsTrigger>
        </TabsList>

        <TabsContent value="review">
          <ReviewTabContent />
        </TabsContent>

        <TabsContent value="keys">
          <KeysTabContent
            allCount={allKeysCount}
            activeCount={activeKeysCount}
            inactiveCount={inactiveKeysCount}
          />
        </TabsContent>

        <TabsContent value="monitoring">
          <MonitoringTabContent />
        </TabsContent>
      </Tabs>
    </div>
  )
}
