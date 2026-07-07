import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, redirect } from "@tanstack/react-router"
import { Trash2 } from "lucide-react"
import { useMemo, useState } from "react"
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
import { Input } from "@/components/ui/input"
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
import {
  aiApiAdminCredentialsCountQueryOptions,
  aiApiAdminCredentialsQueryOptions,
} from "@/features/aiApi/queryOptions"
import { requireAdminUser } from "@/features/auth/guards"
import useCustomToast from "@/hooks/useCustomToast"
import { queryKeys } from "@/lib/queryKeys"
import {
  type AiApiCredentialAdminPublic,
  type AiApiCredentialAdminStatus,
  AiApiService,
} from "@/services/aiApi"
import { handleError } from "@/utils"

const PAGE_SIZE = 50

export const Route = createFileRoute("/_layout/ai-api-credentials")({
  component: AiApiCredentialsAdminPage,
  beforeLoad: () => {
    requireAdminUser()
    throw redirect({ to: "/admin/ai-management" })
  },
})

function formatTime(value?: string | null) {
  if (!value) return "-"
  return new Date(value).toLocaleString()
}

function inactiveReasonLabel(reason?: "revoked" | "expired" | null) {
  if (!reason) return "-"
  return reason === "revoked" ? "已撤銷" : "已過期"
}

function StatusBadge({ item }: { item: AiApiCredentialAdminPublic }) {
  if (item.status === "active") {
    return <Badge className="bg-emerald-600">啟用</Badge>
  }
  return <Badge variant="destructive">失效</Badge>
}

function AiApiCredentialsAdminPage() {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [statusFilter, setStatusFilter] = useState<
    "all" | AiApiCredentialAdminStatus
  >("all")
  const [userEmail, setUserEmail] = useState("")
  const [page, setPage] = useState(0)
  const [deletingItem, setDeletingItem] =
    useState<AiApiCredentialAdminPublic | null>(null)

  const queryInput = useMemo(
    () => ({
      status: statusFilter === "all" ? null : statusFilter,
      userEmail: userEmail.trim() || null,
      skip: page * PAGE_SIZE,
      limit: PAGE_SIZE,
    }),
    [statusFilter, userEmail, page],
  )

  const listQuery = useQuery(
    aiApiAdminCredentialsQueryOptions({
      skip: queryInput.skip ?? 0,
      limit: queryInput.limit ?? PAGE_SIZE,
      status: statusFilter,
      userEmail: userEmail,
    }),
  )

  const allCountQuery = useQuery(aiApiAdminCredentialsCountQueryOptions("all"))

  const activeCountQuery = useQuery(
    aiApiAdminCredentialsCountQueryOptions("active"),
  )

  const inactiveCountQuery = useQuery(
    aiApiAdminCredentialsCountQueryOptions("inactive"),
  )

  const deleteMutation = useMutation({
    mutationFn: (credentialId: string) =>
      AiApiService.deleteCredential({
        credentialId,
      }),
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
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">AI API 金鑰狀態</h1>
        <p className="text-muted-foreground">
          查看目前資料庫中所有 AI API 金鑰紀錄與狀態（僅顯示現存紀錄）。
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>全部紀錄</CardDescription>
            <CardTitle className="text-3xl">
              {allCountQuery.data?.count ?? 0}
            </CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>啟用</CardDescription>
            <CardTitle className="text-3xl text-emerald-600">
              {activeCountQuery.data?.count ?? 0}
            </CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>失效</CardDescription>
            <CardTitle className="text-3xl text-red-600">
              {inactiveCountQuery.data?.count ?? 0}
            </CardTitle>
          </CardHeader>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>篩選</CardTitle>
          <CardDescription>可依狀態與使用者 Email 篩選。</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 sm:grid-cols-2">
            <Select
              value={statusFilter}
              onValueChange={(value) => {
                setStatusFilter(value as "all" | AiApiCredentialAdminStatus)
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
              onChange={(event) => {
                setUserEmail(event.target.value)
                setPage(0)
              }}
            />
          </div>
        </CardContent>
      </Card>

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
                      <StatusBadge item={item} />
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
                onClick={() => setPage((prev) => Math.max(0, prev - 1))}
              >
                上一頁
              </Button>
              <Button
                variant="outline"
                disabled={page + 1 >= totalPages}
                onClick={() =>
                  setPage((prev) => (prev + 1 >= totalPages ? prev : prev + 1))
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
          if (!open && !deleteMutation.isPending) {
            setDeletingItem(null)
          }
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
              onClick={(event) => {
                event.preventDefault()
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
