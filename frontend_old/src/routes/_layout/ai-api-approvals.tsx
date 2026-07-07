import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, redirect } from "@tanstack/react-router"
import type { ColumnDef } from "@tanstack/react-table"
import { Check, ClipboardCheck, X } from "lucide-react"
import { useMemo, useState } from "react"
import { DataTable } from "@/components/Common/DataTable"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { LoadingButton } from "@/components/ui/loading-button"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { aiApiAdminRequestsQueryOptions } from "@/features/aiApi/queryOptions"
import { requireAdminUser } from "@/features/auth/guards"
import useCustomToast from "@/hooks/useCustomToast"
import { queryKeys } from "@/lib/queryKeys"
import {
  type AiApiRequestPublic,
  type AiApiRequestStatus,
  AiApiService,
} from "@/services/aiApi"
import { handleError } from "@/utils"

export const Route = createFileRoute("/_layout/ai-api-approvals")({
  component: AiApiApprovalsPage,
  beforeLoad: () => {
    requireAdminUser()
    throw redirect({ to: "/admin/ai-management" })
  },
})

function formatTime(value?: string | null) {
  if (!value) return "尚未審核"
  return new Date(value).toLocaleString()
}

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
            <div>申請時間：{formatTime(request.created_at)}</div>
            <div className="whitespace-pre-wrap text-muted-foreground">
              用途：{request.purpose}
            </div>
          </div>

          <Textarea
            value={comment}
            onChange={(event) => setComment(event.target.value)}
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

function statusLabel(status: AiApiRequestStatus) {
  if (status === "approved") return "已通過"
  if (status === "rejected") return "已拒絕"
  return "待審核"
}

function statusClass(status: AiApiRequestStatus) {
  if (status === "approved") {
    return "border-emerald-500/20 bg-emerald-500/10 text-emerald-700"
  }
  if (status === "rejected") {
    return "border-destructive/20 bg-destructive/10 text-destructive"
  }
  return "border-amber-500/20 bg-amber-500/10 text-amber-700"
}

function AiApiApprovalsPage() {
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
        cell: ({ row }) => (
          <span
            className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-medium ${statusClass(
              row.original.status,
            )}`}
          >
            {statusLabel(row.original.status)}
          </span>
        ),
      },
      {
        accessorKey: "created_at",
        header: "申請時間",
        cell: ({ row }) => (
          <span className="block min-w-[170px] text-sm text-muted-foreground">
            {formatTime(row.original.created_at)}
          </span>
        ),
      },
      {
        id: "reviewed_at",
        header: "審核時間",
        cell: ({ row }) => (
          <span className="block min-w-[170px] text-sm text-muted-foreground">
            {formatTime(row.original.reviewed_at)}
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
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">AI API 審核</h1>
        <p className="text-muted-foreground">審核申請並核發 API 存取參數。</p>
      </div>

      <Tabs
        value={statusFilter}
        onValueChange={(value) =>
          setStatusFilter(value as AiApiRequestStatus | "all")
        }
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
