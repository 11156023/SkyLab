import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Globe,
  HelpCircle,
  Loader2,
  Lock,
  Pencil,
  Plus,
  RefreshCw,
  ShieldCheck,
  Trash2,
  Unlock,
} from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import type { ApiError } from "@/client"
import { CreateReverseProxyRuleDialog } from "@/components/ReverseProxy/CreateReverseProxyRuleDialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import useAuth from "@/hooks/useAuth"
import {
  type ManagedReverseProxyRule,
  ReverseProxyApiService,
  type ReverseProxySetupContext,
} from "@/services/reverseProxy"

function isAdminUser(
  user: { role?: string; is_superuser?: boolean } | null | undefined,
) {
  return user?.role === "admin" || user?.is_superuser === true
}

function errorMessage(error: unknown, fallback: string) {
  const apiError = error as ApiError & { body?: { detail?: string } }
  return apiError.body?.detail ?? apiError.message ?? fallback
}

function formatDateTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return date.toLocaleString("zh-TW")
}

function RuleCard({
  rule,
  isDeleting,
  actionsDisabled,
  onDelete,
  onEdit,
  onVisit,
}: {
  rule: ManagedReverseProxyRule
  isDeleting: boolean
  actionsDisabled: boolean
  onDelete: () => void
  onEdit: () => void
  onVisit: () => void
}) {
  const scheme = rule.enable_https ? "https" : "http"
  const fullUrl = `${scheme}://${rule.domain}`

  return (
    <div className="group relative rounded-2xl border border-border/70 bg-card p-5 transition-all hover:border-border hover:shadow-md">
      {/* 網域名稱 - 最醒目 */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Globe className="h-5 w-5 shrink-0 text-sky-500" />
            <h3 className="truncate text-lg font-semibold text-foreground">
              {rule.domain}
            </h3>
          </div>
          <p className="mt-1.5 text-sm text-muted-foreground">
            外部訪問網址：
            <span className="font-mono text-foreground">{fullUrl}</span>
          </p>
        </div>
        <Badge
          variant="outline"
          className={`shrink-0 rounded-full ${
            rule.enable_https
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
              : "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400"
          }`}
        >
          {rule.enable_https ? (
            <Lock className="mr-1 h-3 w-3" />
          ) : (
            <Unlock className="mr-1 h-3 w-3" />
          )}
          {rule.enable_https ? "安全連線" : "未加密"}
        </Badge>
      </div>

      {/* 連線資訊 - 用白話文 */}
      <div className="mt-4 rounded-xl bg-muted/50 px-4 py-3">
        <div className="grid gap-2 text-sm sm:grid-cols-2">
          <div>
            <span className="text-muted-foreground">連線到你的 VM：</span>
            <span className="ml-1 font-medium text-foreground">
              VM {rule.vmid}
            </span>
          </div>
          <div>
            <span className="text-muted-foreground">VM 內部 Port：</span>
            <span className="ml-1 font-mono font-medium text-foreground">
              {rule.internal_port}
            </span>
          </div>
          <div>
            <span className="text-muted-foreground">VM IP：</span>
            <span className="ml-1 font-mono font-medium text-foreground">
              {rule.vm_ip}
            </span>
          </div>
          <div>
            <span className="text-muted-foreground">建立於：</span>
            <span className="ml-1 text-foreground">
              {formatDateTime(rule.created_at)}
            </span>
          </div>
        </div>
      </div>

      {/* 運作原理說明 */}
      <p className="mt-3 text-xs text-muted-foreground">
        當有人訪問{" "}
        <span className="font-mono font-medium text-foreground">
          {rule.domain}
        </span>
        ，系統會自動把流量轉到你 VM 的 Port {rule.internal_port}
      </p>

      {/* 操作按鈕 */}
      <div className="mt-4 flex flex-wrap gap-2">
        <Button variant="default" size="sm" onClick={onVisit}>
          <ExternalLink className="mr-2 h-3.5 w-3.5" />
          開啟網站
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={actionsDisabled}
          onClick={onEdit}
        >
          <Pencil className="mr-2 h-3.5 w-3.5" />
          編輯規則
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={isDeleting || actionsDisabled}
          onClick={onDelete}
          className="text-destructive hover:bg-destructive/10 hover:text-destructive"
        >
          {isDeleting ? (
            <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
          ) : (
            <Trash2 className="mr-2 h-3.5 w-3.5" />
          )}
          刪除規則
        </Button>
      </div>
    </div>
  )
}

function EmptyState({
  onAdd,
  disabled,
}: {
  onAdd: () => void
  disabled: boolean
}) {
  return (
    <div className="flex flex-col items-center rounded-2xl border-2 border-dashed border-border/60 bg-muted/20 px-6 py-16 text-center">
      <div className="flex h-16 w-16 items-center justify-center rounded-full bg-sky-500/10">
        <Globe className="h-8 w-8 text-sky-500" />
      </div>
      <h3 className="mt-5 text-lg font-semibold text-foreground">
        還沒有設定任何網域
      </h3>
      <p className="mt-2 max-w-md text-sm text-muted-foreground">
        網域設定可以讓別人透過一個好記的網址（例如
        app.example.edu.tw）直接訪問你 VM 裡跑的網站或服務，不需要記 IP 和
        Port。
      </p>
      <Button className="mt-6" size="lg" onClick={onAdd} disabled={disabled}>
        <Plus className="mr-2 h-4 w-4" />
        新增第一個網域
      </Button>
    </div>
  )
}

function HowItWorksSection() {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="rounded-2xl border border-border/70 bg-card">
      <button
        type="button"
        className="flex w-full items-center gap-3 px-5 py-4 text-left"
        onClick={() => setExpanded(!expanded)}
      >
        <HelpCircle className="h-5 w-5 shrink-0 text-sky-500" />
        <span className="text-sm font-medium text-foreground">
          這是什麼？反向代理怎麼運作？
        </span>
        {expanded ? (
          <ChevronDown className="ml-auto h-4 w-4 text-muted-foreground" />
        ) : (
          <ChevronRight className="ml-auto h-4 w-4 text-muted-foreground" />
        )}
      </button>
      {expanded && (
        <div className="border-t border-border/60 px-5 pb-5 pt-4">
          <div className="space-y-4 text-sm text-muted-foreground">
            <div className="grid gap-4 md:grid-cols-3">
              <div className="rounded-xl bg-muted/50 p-4">
                <div className="text-2xl">1</div>
                <p className="mt-2 font-medium text-foreground">設定網域</p>
                <p className="mt-1">
                  輸入主機名前綴、選擇 Cloudflare Zone，並指定要綁定的 VM 和
                  Port。
                </p>
              </div>
              <div className="rounded-xl bg-muted/50 p-4">
                <div className="text-2xl">2</div>
                <p className="mt-2 font-medium text-foreground">系統自動設定</p>
                <p className="mt-1">
                  平台會自動幫你設定好路由規則，如果開啟 HTTPS
                  還會自動申請免費的 SSL 憑證。
                </p>
              </div>
              <div className="rounded-xl bg-muted/50 p-4">
                <div className="text-2xl">3</div>
                <p className="mt-2 font-medium text-foreground">直接訪問</p>
                <p className="mt-1">
                  設定完成後，任何人都可以透過這個網址直接訪問你 VM 裡跑的網站或
                  API。
                </p>
              </div>
            </div>
            <div className="rounded-xl border border-sky-500/20 bg-sky-500/5 p-4">
              <p className="font-medium text-foreground">
                常見問題：我需要什麼前置作業？
              </p>
              <ul className="mt-2 list-inside list-disc space-y-1">
                <li>你的 VM 裡需要有一個正在執行的網站或 API 服務</li>
                <li>
                  你需要知道該服務跑在哪個 Port（例如 Node.js 預設 3000、Python
                  Flask 預設 5000、Nginx 預設 80）
                </li>
                <li>
                  管理員需要先在 Cloudflare 網域管理設定預設 A/CNAME 指向與可用
                  Zone
                </li>
              </ul>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export const Route = createFileRoute("/_layout/reverse-proxy")({
  component: ReverseProxyPage,
  head: () => ({
    meta: [
      {
        title: "網域管理 - SkyLab",
      },
    ],
  }),
})

function ReverseProxyPage() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [createDialogOpen, setCreateDialogOpen] = useState(false)
  const [editingRule, setEditingRule] =
    useState<ManagedReverseProxyRule | null>(null)
  const [showAdminPanel, setShowAdminPanel] = useState(false)

  const isAdmin = isAdminUser(user)

  const setupContextQuery = useQuery({
    queryKey: ["reverse-proxy-setup-context"],
    queryFn: () => ReverseProxyApiService.getSetupContext(),
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
    staleTime: 15_000,
    retry: false,
  })

  const rulesQuery = useQuery({
    queryKey: ["reverse-proxy-rules"],
    queryFn: () => ReverseProxyApiService.listRules(),
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
    staleTime: 15_000,
  })

  const deleteRuleMutation = useMutation({
    mutationFn: (ruleId: string) =>
      ReverseProxyApiService.deleteRule({ ruleId }),
    onSuccess: () => {
      toast.success("網域規則已刪除")
      queryClient.invalidateQueries({ queryKey: ["reverse-proxy-rules"] })
    },
    onError: (error: unknown) => {
      toast.error(errorMessage(error, "刪除網域規則失敗"))
    },
  })

  const syncRulesMutation = useMutation({
    mutationFn: () => ReverseProxyApiService.syncRules(),
    onSuccess: () => {
      toast.success("規則已同步到 Gateway")
      queryClient.invalidateQueries({ queryKey: ["reverse-proxy-rules"] })
    },
    onError: (error: unknown) => {
      toast.error(errorMessage(error, "同步規則失敗"))
    },
  })

  const setupContext = setupContextQuery.data as
    | ReverseProxySetupContext
    | undefined
  const actionsDisabled =
    setupContext?.enabled === false || setupContextQuery.isError
  const automationTarget =
    setupContext?.default_dns_target_type &&
    setupContext.default_dns_target_value
      ? `${setupContext.default_dns_target_type} ${setupContext.default_dns_target_value}`
      : null
  const rules = (rulesQuery.data ?? []) as ManagedReverseProxyRule[]

  const refreshAll = () => {
    queryClient.invalidateQueries({ queryKey: ["reverse-proxy-rules"] })
    queryClient.invalidateQueries({ queryKey: ["reverse-proxy-setup-context"] })
  }

  const handleDeleteRule = (rule: ManagedReverseProxyRule) => {
    if (actionsDisabled) {
      return
    }
    const confirmed = window.confirm(
      `確定要刪除「${rule.domain}」這個網域規則嗎？\n\n刪除後，透過這個網址將無法再訪問你的 VM。`,
    )
    if (confirmed) {
      deleteRuleMutation.mutate(rule.id)
    }
  }

  return (
    <TooltipProvider>
      <div className="mx-auto max-w-5xl space-y-6">
        {/* 頁面標題 */}
        <section className="relative overflow-hidden rounded-[28px] border border-sky-500/20 bg-[radial-gradient(circle_at_top_left,rgba(56,189,248,0.18),transparent_40%),linear-gradient(135deg,rgba(255,255,255,0.95),rgba(240,249,255,0.9))] p-6 dark:bg-[radial-gradient(circle_at_top_left,rgba(14,165,233,0.14),transparent_40%),linear-gradient(135deg,rgba(7,10,19,0.96),rgba(8,24,36,0.92))]">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h1 className="text-2xl font-bold tracking-tight text-foreground">
                網域管理
              </h1>
              <p className="mt-1.5 text-sm text-muted-foreground">
                讓別人透過一個好記的網址來訪問你 VM 裡的網站或服務
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="outline" size="icon" onClick={refreshAll}>
                    <RefreshCw className="h-4 w-4" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>重新整理</TooltipContent>
              </Tooltip>
              {isAdmin && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => syncRulesMutation.mutate()}
                      disabled={syncRulesMutation.isPending || actionsDisabled}
                    >
                      {syncRulesMutation.isPending ? (
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      ) : (
                        <ShieldCheck className="mr-2 h-4 w-4" />
                      )}
                      同步到 Gateway
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    將所有規則重新同步到 Gateway VM
                  </TooltipContent>
                </Tooltip>
              )}
              <Button
                onClick={() => {
                  setEditingRule(null)
                  setCreateDialogOpen(true)
                }}
                disabled={actionsDisabled}
              >
                <Plus className="mr-2 h-4 w-4" />
                新增網域
              </Button>
            </div>
          </div>

          {/* 統計 */}
          <div className="mt-4 flex flex-wrap gap-3">
            <Badge
              variant="outline"
              className="rounded-full border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
            >
              {rules.length} 個網域規則
            </Badge>
            {rulesQuery.isFetching && (
              <Badge
                variant="outline"
                className="rounded-full border-sky-500/30 bg-sky-500/10 text-sky-600 dark:text-sky-400"
              >
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                更新中
              </Badge>
            )}
            {automationTarget && (
              <Badge
                variant="outline"
                className="rounded-full border-violet-500/30 bg-violet-500/10 text-violet-700 dark:text-violet-300"
              >
                自動 DNS → {automationTarget}
              </Badge>
            )}
          </div>
        </section>

        {/* 說明區塊 */}
        <HowItWorksSection />

        {setupContextQuery.isError && (
          <div className="rounded-2xl border border-amber-500/25 bg-amber-500/5 p-4 text-sm">
            <div className="flex items-start gap-3 text-amber-700 dark:text-amber-300">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <div>
                <div className="font-medium">無法確認反向代理前置條件</div>
                <div className="mt-1 text-amber-700/80 dark:text-amber-200/80">
                  目前將暫時停用新增、編輯與刪除操作，請稍後重新整理或聯繫管理員。
                </div>
              </div>
            </div>
          </div>
        )}

        {setupContext?.enabled === false && (
          <div className="rounded-2xl border border-amber-500/25 bg-amber-500/5 p-4 text-sm">
            <div className="flex items-start gap-3 text-amber-700 dark:text-amber-300">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <div>
                <div className="font-medium">反向代理功能目前不可用</div>
                <div className="mt-1 text-amber-700/80 dark:text-amber-200/80">
                  {(setupContext.reasons ?? []).join("；")}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* 錯誤提示 */}
        {rulesQuery.isError && (
          <div className="rounded-2xl border border-amber-500/25 bg-amber-500/5 p-4 text-sm">
            <div className="flex items-start gap-3 text-amber-700 dark:text-amber-300">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <div>
                <div className="font-medium">無法載入網域規則</div>
                <div className="mt-1 text-amber-700/80 dark:text-amber-200/80">
                  請檢查網路連線後重試，或聯繫管理員。
                </div>
              </div>
            </div>
          </div>
        )}

        {/* 規則列表 */}
        {rulesQuery.isLoading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </div>
        ) : rules.length === 0 ? (
          <EmptyState
            onAdd={() => {
              setEditingRule(null)
              setCreateDialogOpen(true)
            }}
            disabled={actionsDisabled}
          />
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {rules.map((rule) => (
              <RuleCard
                key={rule.id}
                rule={rule}
                isDeleting={deleteRuleMutation.isPending}
                actionsDisabled={actionsDisabled}
                onDelete={() => handleDeleteRule(rule)}
                onEdit={() => {
                  setEditingRule(rule)
                  setCreateDialogOpen(true)
                }}
                onVisit={() =>
                  window.open(
                    `${rule.enable_https ? "https" : "http"}://${rule.domain}`,
                    "_blank",
                    "noopener,noreferrer",
                  )
                }
              />
            ))}
          </div>
        )}

        {/* 管理員進階面板 */}
        {isAdmin && (
          <div className="rounded-2xl border border-border/70 bg-card">
            <button
              type="button"
              className="flex w-full items-center gap-3 px-5 py-4 text-left"
              onClick={() => setShowAdminPanel(!showAdminPanel)}
            >
              <ShieldCheck className="h-5 w-5 shrink-0 text-amber-500" />
              <span className="text-sm font-medium text-foreground">
                管理員工具（Traefik Runtime）
              </span>
              <Badge variant="outline" className="ml-1 rounded-full text-xs">
                Admin
              </Badge>
              {showAdminPanel ? (
                <ChevronDown className="ml-auto h-4 w-4 text-muted-foreground" />
              ) : (
                <ChevronRight className="ml-auto h-4 w-4 text-muted-foreground" />
              )}
            </button>
            {showAdminPanel && <AdminRuntimePanel />}
          </div>
        )}

        <CreateReverseProxyRuleDialog
          open={createDialogOpen}
          onOpenChange={(open) => {
            setCreateDialogOpen(open)
            if (!open) {
              setEditingRule(null)
            }
          }}
          onCompleted={() => {
            refreshAll()
          }}
          setupContext={setupContext}
          rule={editingRule}
        />
      </div>
    </TooltipProvider>
  )
}

/** 管理員專用的 Traefik runtime 簡易面板 */
function AdminRuntimePanel() {
  const runtimeQuery = useQuery({
    queryKey: ["reverse-proxy-runtime"],
    queryFn: () => ReverseProxyApiService.getRuntimeSnapshot(),
    refetchInterval: 15_000,
    staleTime: 30_000,
    retry: false,
  })

  if (runtimeQuery.isLoading) {
    return (
      <div className="flex items-center justify-center border-t border-border/60 py-10">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (runtimeQuery.isError) {
    return (
      <div className="border-t border-border/60 px-5 py-6 text-sm text-amber-600 dark:text-amber-400">
        <div className="flex items-center gap-2">
          <AlertTriangle className="h-4 w-4" />
          無法連線 Traefik runtime
        </div>
      </div>
    )
  }

  const snapshot = runtimeQuery.data
  const version =
    typeof snapshot?.version?.version === "string"
      ? snapshot.version.version
      : "unknown"

  const httpRouters = snapshot?.http?.routers?.length ?? 0
  const httpServices = snapshot?.http?.services?.length ?? 0
  const httpMiddlewares = snapshot?.http?.middlewares?.length ?? 0
  const tcpRouters = snapshot?.tcp?.routers?.length ?? 0
  const tcpServices = snapshot?.tcp?.services?.length ?? 0
  const udpRouters = snapshot?.udp?.routers?.length ?? 0
  const udpServices = snapshot?.udp?.services?.length ?? 0
  const entrypoints = snapshot?.entrypoints ?? []

  return (
    <div className="space-y-4 border-t border-border/60 px-5 pb-5 pt-4">
      {snapshot?.runtime_error && (
        <div className="rounded-xl bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-300">
          <span className="font-medium">Runtime 錯誤：</span>{" "}
          {snapshot.runtime_error}
        </div>
      )}

      <div className="flex flex-wrap gap-2 text-xs">
        <Badge variant="outline" className="rounded-full">
          Traefik {version}
        </Badge>
        <Badge variant="outline" className="rounded-full">
          {entrypoints.length} entrypoints
        </Badge>
      </div>

      <div className="grid gap-3 text-sm sm:grid-cols-3">
        <div className="rounded-xl border border-border/60 bg-muted/30 p-3">
          <div className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            HTTP
          </div>
          <div className="mt-2 space-y-1 text-muted-foreground">
            <div>
              Routers:{" "}
              <span className="font-medium text-foreground">{httpRouters}</span>
            </div>
            <div>
              Services:{" "}
              <span className="font-medium text-foreground">
                {httpServices}
              </span>
            </div>
            <div>
              Middlewares:{" "}
              <span className="font-medium text-foreground">
                {httpMiddlewares}
              </span>
            </div>
          </div>
        </div>
        <div className="rounded-xl border border-border/60 bg-muted/30 p-3">
          <div className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            TCP
          </div>
          <div className="mt-2 space-y-1 text-muted-foreground">
            <div>
              Routers:{" "}
              <span className="font-medium text-foreground">{tcpRouters}</span>
            </div>
            <div>
              Services:{" "}
              <span className="font-medium text-foreground">{tcpServices}</span>
            </div>
          </div>
        </div>
        <div className="rounded-xl border border-border/60 bg-muted/30 p-3">
          <div className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            UDP
          </div>
          <div className="mt-2 space-y-1 text-muted-foreground">
            <div>
              Routers:{" "}
              <span className="font-medium text-foreground">{udpRouters}</span>
            </div>
            <div>
              Services:{" "}
              <span className="font-medium text-foreground">{udpServices}</span>
            </div>
          </div>
        </div>
      </div>

      {entrypoints.length > 0 && (
        <div>
          <div className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Entrypoints
          </div>
          <div className="flex flex-wrap gap-2">
            {entrypoints.map((ep) => {
              const name = typeof ep.name === "string" ? ep.name : "unknown"
              const addr = typeof ep.address === "string" ? ep.address : ":?"
              return (
                <Badge
                  key={`${name}-${addr}`}
                  variant="outline"
                  className="rounded-full font-mono text-xs"
                >
                  {name} ({addr})
                </Badge>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
