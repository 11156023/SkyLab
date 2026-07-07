import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import {
  BarChart3,
  BrainCircuit,
  CheckCircle2,
  Clock3,
  Copy,
  Eye,
  EyeOff,
  KeyRound,
  Link2,
  Pencil,
  RefreshCw,
  Save,
  Send,
  Trash2,
  TrendingUp,
  X,
  XCircle,
} from "lucide-react"
import { type ReactNode, useMemo, useState } from "react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { LoadingButton } from "@/components/ui/loading-button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import {
  aiApiMyCredentialsQueryOptions,
  aiApiMyRequestsQueryOptions,
} from "@/features/aiApi/queryOptions"
import useCustomToast from "@/hooks/useCustomToast"
import { queryKeys } from "@/lib/queryKeys"
import {
  type AiApiCredentialPublic,
  type AiApiRequestPublic,
  type AiApiRequestStatus,
  AiApiService,
} from "@/services/aiApi"
import {
  AiUserUsageService,
  type TemplateUsageStatsResponse,
  type UsageStatsResponse,
} from "@/services/aiMonitoring"
import { handleError } from "@/utils"

export const Route = createFileRoute("/_layout/ai-api")({
  component: AiApiPage,
  head: () => ({
    meta: [
      {
        title: "AI API - SkyLab",
      },
    ],
  }),
})

const DURATION_OPTIONS = [
  { value: "1h", label: "1 小時" },
  { value: "1d", label: "1 天" },
  { value: "7d", label: "1 週" },
  { value: "30d", label: "1 個月" },
  { value: "never", label: "永不過期" },
]

function formatTime(value?: string | null) {
  if (!value) return "尚未處理"
  return new Date(value).toLocaleString()
}

function formatExpiry(value?: string | null) {
  if (!value) return "永不過期"
  const d = new Date(value)
  const now = new Date()
  if (d < now) return `已過期（${d.toLocaleString()}）`
  return d.toLocaleString()
}

function isExpired(value?: string | null) {
  if (!value) return false
  return new Date(value) < new Date()
}

function maskApiKey(value: string) {
  if (value.length <= 14) return value
  return `${value.slice(0, 8)}••••••${value.slice(-6)}`
}

function requestStatusStyle(status: AiApiRequestStatus) {
  if (status === "approved") {
    return "border-emerald-500/20 bg-emerald-500/10 text-emerald-700"
  }
  if (status === "rejected") {
    return "border-destructive/20 bg-destructive/10 text-destructive"
  }
  return "border-amber-500/20 bg-amber-500/10 text-amber-700"
}

function requestStatusLabel(status: AiApiRequestStatus) {
  if (status === "approved") return "已通過"
  if (status === "rejected") return "已拒絕"
  return "待審核"
}

function RequestStatusIcon({ status }: { status: AiApiRequestStatus }) {
  if (status === "approved") {
    return <CheckCircle2 className="h-3.5 w-3.5" />
  }
  if (status === "rejected") {
    return <XCircle className="h-3.5 w-3.5" />
  }
  return <Clock3 className="h-3.5 w-3.5" />
}

function StatPill({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-full border bg-background/60 px-4 py-2">
      <div className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
        {label}
      </div>
      <div className="mt-1 text-lg font-semibold leading-none">{value}</div>
    </div>
  )
}

function Panel({
  title,
  description,
  children,
}: {
  title: string
  description?: string
  children: ReactNode
}) {
  return (
    <div className="rounded-xl border bg-background/70 p-4">
      <div className="mb-4 space-y-1">
        <div className="text-base font-semibold">{title}</div>
        {description ? (
          <div className="text-sm text-muted-foreground">{description}</div>
        ) : null}
      </div>
      {children}
    </div>
  )
}

function CredentialRow({
  item,
  onSuccess,
  onError,
}: {
  item: AiApiCredentialPublic
  onSuccess: (message: string) => void
  onError: (message: string) => void
}) {
  const queryClient = useQueryClient()
  const [showKey, setShowKey] = useState(false)
  const [editingName, setEditingName] = useState(false)
  const [nameInput, setNameInput] = useState(item.api_key_name)
  const isInactive = Boolean(item.revoked_at)
  const expired = isExpired(item.expires_at)

  const rotateMutation = useMutation({
    mutationFn: () =>
      AiApiService.rotateCredential({
        credentialId: item.id,
      }),
    onSuccess: () => {
      onSuccess("API Key 已刷新，請同步更新你的客戶端設定")
      queryClient.invalidateQueries({ queryKey: queryKeys.aiApi.all })
    },
    onError: handleError.bind(onError),
  })

  const deleteMutation = useMutation({
    mutationFn: () =>
      AiApiService.deleteCredential({
        credentialId: item.id,
      }),
    onSuccess: () => {
      onSuccess("API Key 已刪除")
      queryClient.invalidateQueries({ queryKey: queryKeys.aiApi.all })
    },
    onError: handleError.bind(onError),
  })

  const renameMutation = useMutation({
    mutationFn: () =>
      AiApiService.updateCredentialName({
        credentialId: item.id,
        requestBody: { api_key_name: nameInput.trim() },
      }),
    onSuccess: () => {
      onSuccess(`名稱已更新為「${nameInput.trim()}」`)
      setEditingName(false)
      queryClient.invalidateQueries({ queryKey: queryKeys.aiApi.all })
    },
    onError: handleError.bind(onError),
  })

  const copyValue = async (label: string, value: string) => {
    try {
      await navigator.clipboard.writeText(value)
      onSuccess(label)
    } catch {
      onError(`${label}失敗`)
    }
  }

  const handleCancelRename = () => {
    setNameInput(item.api_key_name)
    setEditingName(false)
  }

  const statusBadge = (() => {
    if (isInactive)
      return {
        label: "已替換",
        cls: "border-muted-foreground/20 bg-muted text-muted-foreground",
      }
    if (expired)
      return {
        label: "已過期",
        cls: "border-destructive/20 bg-destructive/10 text-destructive",
      }
    return {
      label: "使用中",
      cls: "border-emerald-500/20 bg-emerald-500/10 text-emerald-700",
    }
  })()

  return (
    <div className="border-b py-3 last:border-b-0 first:pt-0 last:pb-0">
      <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_auto] xl:items-start">
        <div className="grid gap-3 md:grid-cols-2">
          <div className="min-w-0 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              {editingName ? (
                <div className="flex items-center gap-2">
                  <Input
                    id={`rename-input-${item.id}`}
                    value={nameInput}
                    onChange={(e) => setNameInput(e.target.value)}
                    maxLength={20}
                    className="h-7 w-44 text-sm font-medium"
                    autoFocus
                    onKeyDown={(e) => {
                      if (e.key === "Enter") renameMutation.mutate()
                      if (e.key === "Escape") handleCancelRename()
                    }}
                  />
                  <LoadingButton
                    loading={renameMutation.isPending}
                    size="sm"
                    variant="outline"
                    className="h-7 px-2"
                    onClick={() => renameMutation.mutate()}
                    disabled={nameInput.trim().length === 0}
                  >
                    <Save className="h-3.5 w-3.5" />
                  </LoadingButton>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-7 px-2"
                    onClick={handleCancelRename}
                  >
                    <X className="h-3.5 w-3.5" />
                  </Button>
                </div>
              ) : (
                <div className="flex items-center gap-1.5">
                  <div className="truncate font-medium">
                    {item.api_key_name}
                  </div>
                  <Button
                    id={`rename-btn-${item.id}`}
                    size="sm"
                    variant="ghost"
                    className="h-6 w-6 p-0 text-muted-foreground hover:text-foreground"
                    onClick={() => {
                      setNameInput(item.api_key_name)
                      setEditingName(true)
                    }}
                  >
                    <Pencil className="h-3 w-3" />
                  </Button>
                </div>
              )}
              <span
                className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium ${statusBadge.cls}`}
              >
                {statusBadge.label}
              </span>
            </div>

            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              <span>Prefix：{item.api_key_prefix}</span>
              <span>建立：{formatTime(item.created_at)}</span>
              <span className={expired ? "font-medium text-destructive" : ""}>
                到期：{formatExpiry(item.expires_at)}
              </span>
              {item.revoked_at ? (
                <span>失效：{formatTime(item.revoked_at)}</span>
              ) : null}
            </div>
          </div>

          <div className="min-w-0 space-y-2 text-sm">
            <div className="min-w-0">
              <div className="mb-1 flex items-center gap-1.5 text-[11px] uppercase tracking-[0.16em] text-muted-foreground">
                <Link2 className="h-3.5 w-3.5" />
                Base URL
              </div>
              <div className="truncate font-mono text-foreground">
                {item.base_url}
              </div>
            </div>
            <div className="min-w-0">
              <div className="mb-1 flex items-center gap-1.5 text-[11px] uppercase tracking-[0.16em] text-muted-foreground">
                <KeyRound className="h-3.5 w-3.5" />
                API Key
              </div>
              <div className="truncate font-mono text-foreground">
                {showKey ? item.api_key : maskApiKey(item.api_key)}
              </div>
            </div>
          </div>
        </div>

        <div className="flex flex-wrap gap-2 xl:justify-end">
          <Button
            size="sm"
            variant="outline"
            onClick={() => setShowKey((previous) => !previous)}
          >
            {showKey ? (
              <EyeOff className="mr-1.5 h-4 w-4" />
            ) : (
              <Eye className="mr-1.5 h-4 w-4" />
            )}
            {showKey ? "隱藏" : "顯示"}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => copyValue("Base URL 已複製", item.base_url)}
          >
            <Copy className="mr-1.5 h-4 w-4" />
            Base URL
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => copyValue("API Key 已複製", item.api_key)}
          >
            <Copy className="mr-1.5 h-4 w-4" />
            API Key
          </Button>
          <LoadingButton
            loading={rotateMutation.isPending}
            onClick={() => rotateMutation.mutate()}
            disabled={isInactive || deleteMutation.isPending}
            size="sm"
            variant="outline"
          >
            <RefreshCw className="mr-1.5 h-4 w-4" />
            刷新
          </LoadingButton>
          <LoadingButton
            loading={deleteMutation.isPending}
            onClick={() => deleteMutation.mutate()}
            disabled={rotateMutation.isPending}
            size="sm"
            variant="destructive"
          >
            <Trash2 className="mr-1.5 h-4 w-4" />
            刪除
          </LoadingButton>
        </div>
      </div>
    </div>
  )
}

function RequestRow({ item }: { item: AiApiRequestPublic }) {
  return (
    <div className="border-b py-3 last:border-b-0 first:pt-0 last:pb-0">
      <div className="grid gap-3 lg:grid-cols-[220px_minmax(0,1fr)_220px] lg:items-start">
        <div className="min-w-0 space-y-1">
          <div className="truncate font-medium">{item.api_key_name}</div>
          <span
            className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${requestStatusStyle(
              item.status,
            )}`}
          >
            <RequestStatusIcon status={item.status} />
            {requestStatusLabel(item.status)}
          </span>
        </div>

        <p className="line-clamp-2 text-sm text-muted-foreground">
          {item.purpose}
        </p>

        <div className="space-y-1 text-xs text-muted-foreground lg:text-right">
          <div>申請：{formatTime(item.created_at)}</div>
          <div>審核：{formatTime(item.reviewed_at)}</div>
          {item.review_comment ? (
            <div className="truncate">備註：{item.review_comment}</div>
          ) : null}
        </div>
      </div>
    </div>
  )
}

// ---- 我的用量 tab helpers ----

type UsageDatePreset = "7d" | "30d" | "90d"

function getUsageDates(preset: UsageDatePreset): {
  start: string
  end: string
} {
  const now = new Date()
  const end = now.toISOString().split("T")[0]!
  const start = new Date(now)
  if (preset === "7d") start.setDate(start.getDate() - 7)
  else if (preset === "30d") start.setDate(start.getDate() - 30)
  else start.setDate(start.getDate() - 90)
  return { start: start.toISOString().split("T")[0]!, end }
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
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

function UsageStatCard({
  label,
  value,
}: {
  label: string
  value: string | number
}) {
  return (
    <div className="rounded-xl border bg-background/70 p-4">
      <div className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
        {label}
      </div>
      <div className="mt-2 text-2xl font-bold">{value}</div>
    </div>
  )
}

function MyUsageTab() {
  const [preset, setPreset] = useState<UsageDatePreset>("30d")
  const { start, end } = useMemo(() => getUsageDates(preset), [preset])

  const templateQuery = useQuery({
    queryKey: queryKeys.aiMonitoring.myTemplateUsage({ start, end }),
    queryFn: () =>
      AiUserUsageService.getMyTemplateUsage({
        start_date: start,
        end_date: end,
      }),
    enabled: Boolean(start && end),
  })

  const proxyQuery = useQuery({
    queryKey: queryKeys.aiMonitoring.myProxyUsage({ start, end }),
    queryFn: () =>
      AiUserUsageService.getMyProxyUsage({
        start_date: start,
        end_date: end,
      }),
    enabled: Boolean(start && end),
    retry: false,
  })

  const tpl = templateQuery.data as TemplateUsageStatsResponse | undefined
  const proxy = proxyQuery.data as UsageStatsResponse | undefined

  return (
    <div className="space-y-6">
      {/* Date range selector */}
      <div className="flex items-center gap-2">
        {(["7d", "30d", "90d"] as const).map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => setPreset(p)}
            className={`rounded-full border px-4 py-1.5 text-sm font-medium transition-colors ${
              preset === p
                ? "border-primary bg-primary text-primary-foreground"
                : "border-border bg-background/60 text-muted-foreground hover:text-foreground"
            }`}
          >
            {p === "7d" ? "7 天" : p === "30d" ? "30 天" : "90 天"}
          </button>
        ))}
        <span className="text-xs text-muted-foreground">
          {start} ~ {end}
        </span>
      </div>

      {/* Proxy usage */}
      <Panel title="Proxy 用量" description="直接呼叫 AI API 的 Token 用量。">
        {proxyQuery.isLoading ? (
          <div className="text-sm text-muted-foreground">載入中…</div>
        ) : proxyQuery.isError ? (
          <div className="text-sm text-destructive">
            無法取得 Proxy 用量資料。
          </div>
        ) : (
          proxy && (
            <div className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-3">
                <UsageStatCard
                  label="總呼叫次數"
                  value={proxy.total_requests}
                />
                <UsageStatCard
                  label="輸入 Tokens"
                  value={formatTokens(proxy.total_input_tokens)}
                />
                <UsageStatCard
                  label="輸出 Tokens"
                  value={formatTokens(proxy.total_output_tokens)}
                />
              </div>
              {Object.keys(proxy.by_model).length > 0 && (
                <div className="space-y-2">
                  <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
                    <BarChart3 className="h-3.5 w-3.5" />
                    按模型
                  </div>
                  <div className="divide-y rounded-lg border">
                    {Object.entries(proxy.by_model).map(([model, stats]) => (
                      <div
                        key={model}
                        className="grid grid-cols-[minmax(0,1fr)_80px_80px_80px] items-center gap-4 px-4 py-2.5 text-sm"
                      >
                        <span
                          className="truncate font-mono text-xs"
                          title={model}
                        >
                          {formatModelDisplay(model)}
                        </span>
                        <span className="text-right text-muted-foreground">
                          {stats.requests} 次
                        </span>
                        <span className="text-right text-muted-foreground">
                          ↑ {formatTokens(stats.input_tokens)}
                        </span>
                        <span className="text-right text-muted-foreground">
                          ↓ {formatTokens(stats.output_tokens)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )
        )}
      </Panel>

      {/* Template usage */}
      <Panel
        title="Template 用量"
        description="使用 AI Template API 的 Token 用量。"
      >
        {templateQuery.isLoading ? (
          <div className="text-sm text-muted-foreground">載入中…</div>
        ) : templateQuery.isError ? (
          <div className="text-sm text-destructive">
            無法取得 Template 用量資料。
          </div>
        ) : tpl ? (
          <div className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-3">
              <UsageStatCard label="總呼叫次數" value={tpl.total_calls} />
              <UsageStatCard
                label="輸入 Tokens"
                value={formatTokens(tpl.total_input_tokens)}
              />
              <UsageStatCard
                label="輸出 Tokens"
                value={formatTokens(tpl.total_output_tokens)}
              />
            </div>
            {Object.keys(tpl.by_call_type).length > 0 && (
              <div className="space-y-2">
                <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
                  <BrainCircuit className="h-3.5 w-3.5" />
                  按呼叫類型
                </div>
                <div className="divide-y rounded-lg border">
                  {Object.entries(tpl.by_call_type).map(([callType, stats]) => (
                    <div
                      key={callType}
                      className="grid grid-cols-[minmax(0,1fr)_80px_80px_80px] items-center gap-4 px-4 py-2.5 text-sm"
                    >
                      <span className="truncate text-xs">{callType}</span>
                      <span className="text-right text-muted-foreground">
                        {stats.calls} 次
                      </span>
                      <span className="text-right text-muted-foreground">
                        ↑ {formatTokens(stats.input_tokens)}
                      </span>
                      <span className="text-right text-muted-foreground">
                        ↓ {formatTokens(stats.output_tokens)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
            此時段無 Template 呼叫紀錄。
          </div>
        )}
      </Panel>
    </div>
  )
}

function AiApiPage() {
  const [purpose, setPurpose] = useState("")
  const [apiKeyName, setApiKeyName] = useState("test")
  const [duration, setDuration] = useState("never")
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const requestsQuery = useQuery(aiApiMyRequestsQueryOptions())

  const credentialsQuery = useQuery(aiApiMyCredentialsQueryOptions())

  const createMutation = useMutation({
    mutationFn: () =>
      AiApiService.createRequest({
        requestBody: {
          purpose: purpose.trim(),
          api_key_name: apiKeyName.trim(),
          duration,
        },
      }),
    onSuccess: () => {
      setPurpose("")
      setApiKeyName("test")
      setDuration("never")
      showSuccessToast("AI API 申請已送出")
      queryClient.invalidateQueries({ queryKey: queryKeys.aiApi.all })
    },
    onError: handleError.bind(showErrorToast),
  })

  const requests = requestsQuery.data?.data ?? []
  const credentials = credentialsQuery.data?.data ?? []
  const activeCredentials = credentials.filter(
    (item) => !item.revoked_at && !isExpired(item.expires_at),
  )
  const expiredCredentials = credentials.filter(
    (item) => !item.revoked_at && isExpired(item.expires_at),
  )
  const approvedRequests = requests.filter((item) => item.status === "approved")

  return (
    <div className="flex flex-col gap-6">
      <div className="space-y-4">
        <div className="space-y-2">
          <div className="text-xs uppercase tracking-[0.22em] text-muted-foreground">
            SkyLab AI API
          </div>
          <h1 className="text-2xl font-bold tracking-tight">
            AI API 金鑰申請與管理
          </h1>
          <p className="max-w-3xl text-sm text-muted-foreground">
            申請、管理與查詢 AI API 金鑰。
          </p>
        </div>

        <div className="flex flex-wrap gap-3">
          <StatPill label="申請紀錄" value={requests.length} />
          <StatPill label="使用中金鑰" value={activeCredentials.length} />
          <StatPill label="過期金鑰" value={expiredCredentials.length} />
          <StatPill label="已通過申請" value={approvedRequests.length} />
        </div>
      </div>

      <Tabs defaultValue="request" className="space-y-5">
        <TabsList className="grid h-auto w-full grid-cols-4 p-1 md:w-[680px]">
          <TabsTrigger value="request">申請</TabsTrigger>
          <TabsTrigger value="keys">API Keys</TabsTrigger>
          <TabsTrigger value="history">申請紀錄</TabsTrigger>
          <TabsTrigger value="usage">
            <TrendingUp className="mr-1 h-3.5 w-3.5" />
            我的用量
          </TabsTrigger>
        </TabsList>

        <TabsContent value="request" className="space-y-5">
          <Panel title="送出新申請" description="填寫用途後送審。">
            <div className="space-y-4">
              <div className="space-y-1.5">
                <label htmlFor="api-key-name" className="text-sm font-medium">
                  金鑰名稱
                </label>
                <Input
                  id="api-key-name"
                  value={apiKeyName}
                  onChange={(event) => setApiKeyName(event.target.value)}
                  placeholder="例如：課程專案用、測試用、我的 App"
                  maxLength={20}
                />
              </div>

              <div className="space-y-1.5">
                <label htmlFor="api-purpose" className="text-sm font-medium">
                  申請目的
                </label>
                <Textarea
                  id="api-purpose"
                  value={purpose}
                  onChange={(event) => setPurpose(event.target.value)}
                  placeholder="例如：課程專題串接聊天模型、工具原型開發、知識庫問答測試或自動化腳本整合。"
                  rows={5}
                />
              </div>

              {/* 過期時間選單 */}
              <div className="space-y-1.5">
                <label
                  htmlFor="duration-select"
                  className="text-sm font-medium"
                >
                  金鑰有效期限
                </label>
                <Select value={duration} onValueChange={setDuration}>
                  <SelectTrigger id="duration-select" className="w-48">
                    <SelectValue placeholder="選擇有效期限" />
                  </SelectTrigger>
                  <SelectContent>
                    {DURATION_OPTIONS.map((opt) => (
                      <SelectItem key={opt.value} value={opt.value}>
                        {opt.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="text-xs text-muted-foreground">
                  用途需至少 10 字。
                </div>
                <LoadingButton
                  onClick={() => createMutation.mutate()}
                  loading={createMutation.isPending}
                  disabled={purpose.trim().length < 10}
                >
                  <Send className="mr-2 h-4 w-4" />
                  送出申請
                </LoadingButton>
              </div>
            </div>
          </Panel>
        </TabsContent>

        <TabsContent value="keys" className="space-y-5">
          <Panel
            title="我的 API Keys"
            description="查看、複製、刷新或刪除金鑰。"
          >
            {credentials.length ? (
              <div>
                {credentials.map((item) => (
                  <CredentialRow
                    key={item.id}
                    item={item}
                    onSuccess={showSuccessToast}
                    onError={(message) =>
                      showErrorToast(`無法完成操作：${message}`)
                    }
                  />
                ))}
              </div>
            ) : (
              <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
                目前還沒有任何已核發的 AI API
                Key。當申請通過後，新的金鑰會出現在這裡。
              </div>
            )}
          </Panel>
        </TabsContent>

        <TabsContent value="history" className="space-y-5">
          <Panel title="申請紀錄" description="近期申請狀態。">
            {requests.length ? (
              <div>
                {requests.map((item) => (
                  <RequestRow key={item.id} item={item} />
                ))}
              </div>
            ) : (
              <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
                目前還沒有 AI API 申請紀錄。
              </div>
            )}
          </Panel>
        </TabsContent>

        <TabsContent value="usage" className="space-y-5">
          <MyUsageTab />
        </TabsContent>
      </Tabs>
    </div>
  )
}
