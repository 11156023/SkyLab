import { useQuery } from "@tanstack/react-query"
import { createFileRoute, redirect } from "@tanstack/react-router"
import {
  Activity,
  BarChart3,
  Bot,
  BrainCircuit,
  FileText,
  Users,
  Zap,
} from "lucide-react"
import { useMemo, useState } from "react"

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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { requireAdminUser } from "@/features/auth/guards"
import { queryKeys } from "@/lib/queryKeys"
import {
  type AIProxyCallRecord,
  type AITemplateCallRecord,
  type AIUserUsageSummary,
  AiAdminMonitoringService,
} from "@/services/aiMonitoring"

export const Route = createFileRoute("/_layout/admin/ai-monitoring")({
  component: AdminAiMonitoringPage,
  beforeLoad: () => {
    requireAdminUser()
    throw redirect({ to: "/admin/ai-management" })
  },
})

const LIMIT = 50

type DatePreset = "7d" | "30d" | "90d" | "custom"

function getPresetDates(preset: DatePreset): { start: string; end: string } {
  const now = new Date()
  const end = now.toISOString().split("T")[0]!
  const start = new Date(now)
  if (preset === "7d") start.setDate(start.getDate() - 7)
  else if (preset === "30d") start.setDate(start.getDate() - 30)
  else if (preset === "90d") start.setDate(start.getDate() - 90)
  return { start: start.toISOString().split("T")[0]!, end }
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

  const org = match[1]
  const name = match[2]
  return `${org}/${name}`
}

function StatusBadge({ status }: { status: string }) {
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

// ---- Sub-components for each Tab ----

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
                          <StatusBadge status={row.status} />
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
                          <StatusBadge status={row.status} />
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

// ---- Main page ----

function AdminAiMonitoringPage() {
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
      {/* Header */}
      <div className="space-y-1">
        <div className="text-xs uppercase tracking-[0.22em] text-muted-foreground">
          SkyLab AI
        </div>
        <h1 className="text-2xl font-bold tracking-tight">AI 用量監控</h1>
        <p className="text-sm text-muted-foreground">
          全系統 AI Proxy 與 Template 呼叫統計。
        </p>
      </div>

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

      {/* Detail tabs */}
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
