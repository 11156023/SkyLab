import Editor from "@monaco-editor/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Circle,
  ClipboardCopy,
  Download,
  FileText,
  Loader2,
  RefreshCw,
  Save,
  Server,
  ShieldAlert,
  Wifi,
  WifiOff,
  XCircle,
} from "lucide-react"
import { useEffect, useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { CloudflareApiService } from "@/services/cloudflare"
import { FirewallService } from "@/services/firewall"
import {
  GatewayApiService,
  type GatewayService,
  type ServiceAction,
} from "@/services/gateway"

export const Route = createFileRoute("/_layout/admin/gateway")({
  component: GatewayPage,
})

function getApiErrorMessage(error: unknown): string {
  if (error && typeof error === "object") {
    const maybeApiError = error as {
      body?: { detail?: string }
      message?: string
    }
    return maybeApiError.body?.detail ?? maybeApiError.message ?? "未知錯誤"
  }
  return "未知錯誤"
}

// ─── 安裝一行指令元件 ──────────────────────────────────────────────────────────

function InstallCommand({ publicKey }: { publicKey: string }) {
  const scriptUrl = GatewayApiService.getInstallScriptUrl()
  const command = `bash <(curl -sSL '${scriptUrl}') '${publicKey}'`

  const copy = () => {
    navigator.clipboard.writeText(command)
    toast.success("安裝指令已複製")
  }

  return (
    <div className="flex gap-2 items-start">
      <code className="flex-1 text-xs bg-muted/80 border border-border rounded px-3 py-2 text-emerald-400 break-all font-mono leading-relaxed">
        {command}
      </code>
      <Button
        size="sm"
        variant="outline"
        onClick={copy}
        className="shrink-0 border-border bg-card hover:bg-accent h-auto py-2"
      >
        <ClipboardCopy className="w-3 h-3" />
      </Button>
    </div>
  )
}

// ─── 服務控制面板 ──────────────────────────────────────────────────────────────

function ServicePanel({ service }: { service: GatewayService }) {
  const [editorContent, setEditorContent] = useState<string>("")
  const [confirmAction, setConfirmAction] = useState<ServiceAction | null>(null)
  const [showLogs, setShowLogs] = useState(false)
  const [logs, setLogs] = useState<string>("")
  const [logsLoading, setLogsLoading] = useState(false)

  const configLang: Record<GatewayService, string> = {
    haproxy: "plaintext",
    traefik: "yaml",
    frps: "ini",
    frpc: "ini",
  }

  const { data: configData, isLoading: configLoading } = useQuery({
    queryKey: ["gateway-config", service],
    queryFn: () => GatewayApiService.readServiceConfig(service),
    retry: false,
  })

  const { data: statusData, refetch: refetchStatus } = useQuery({
    queryKey: ["gateway-status", service],
    queryFn: () => GatewayApiService.getServiceStatus(service),
    refetchInterval: 10000,
    retry: false,
  })

  useEffect(() => {
    if (configData?.content !== undefined) {
      setEditorContent(configData.content)
    }
  }, [configData])

  const saveMutation = useMutation({
    mutationFn: () =>
      GatewayApiService.writeServiceConfig(service, editorContent),
    onSuccess: () => toast.success(`${service} 設定已儲存`),
    onError: (error: unknown) =>
      toast.error(`儲存失敗：${getApiErrorMessage(error)}`),
  })

  const actionMutation = useMutation({
    mutationFn: (action: ServiceAction) =>
      GatewayApiService.controlService(service, action),
    onSuccess: (data) => {
      if (data.success) {
        toast.success(`${data.service} ${data.action} 成功`)
      } else {
        toast.error(`操作失敗：${data.output}`)
      }
      refetchStatus()
      setConfirmAction(null)
    },
    onError: (error: unknown) => {
      toast.error(`操作失敗：${getApiErrorMessage(error)}`)
      setConfirmAction(null)
    },
  })

  const isActive = statusData?.active ?? false

  const fetchLogs = async () => {
    setLogsLoading(true)
    try {
      const text = await GatewayApiService.getServiceLogs(service, 50)
      setLogs(text)
      setShowLogs(true)
    } catch (e: any) {
      toast.error(`取得日誌失敗：${e.message}`)
    } finally {
      setLogsLoading(false)
    }
  }

  const handleAction = (action: ServiceAction) => {
    if (action === "restart" || action === "stop") {
      setConfirmAction(action)
    } else {
      actionMutation.mutate(action)
    }
  }

  return (
    <div className="space-y-4">
      {/* 狀態列 */}
      <div className="flex items-center justify-between bg-card border border-border rounded-lg px-4 py-2">
        <div className="flex items-center gap-2">
          {isActive ? (
            <CheckCircle2 className="w-4 h-4 text-emerald-400" />
          ) : (
            <Circle className="w-4 h-4 text-muted-foreground" />
          )}
          <span className="text-sm text-foreground/80">
            {isActive ? "運行中" : "已停止"}
          </span>
          {statusData?.status_text && (
            <span className="text-xs text-muted-foreground/50 ml-2 font-mono">
              {statusData.status_text.split("\n")[0]}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {(["start", "stop", "restart", "reload"] as ServiceAction[]).map(
            (action) => (
              <Button
                key={action}
                size="sm"
                variant="outline"
                disabled={actionMutation.isPending}
                onClick={() => handleAction(action)}
                className={`h-7 text-xs border-border bg-card hover:bg-accent ${
                  action === "stop"
                    ? "hover:text-red-400"
                    : action === "restart"
                      ? "hover:text-yellow-400"
                      : "hover:text-emerald-400"
                }`}
              >
                {actionMutation.isPending &&
                actionMutation.variables === action ? (
                  <Loader2 className="w-3 h-3 animate-spin" />
                ) : (
                  action
                )}
              </Button>
            ),
          )}
          <Button
            size="sm"
            variant="outline"
            disabled={logsLoading}
            onClick={fetchLogs}
            className="h-7 text-xs border-border bg-card hover:bg-accent hover:text-blue-400"
          >
            {logsLoading ? (
              <Loader2 className="w-3 h-3 animate-spin" />
            ) : (
              <FileText className="w-3 h-3 mr-1" />
            )}
            日誌
          </Button>
        </div>
      </div>

      {/* 危險操作確認 */}
      {confirmAction && (
        <div className="flex items-center gap-3 bg-yellow-900/20 border border-yellow-700/50 rounded-lg px-4 py-3">
          <AlertTriangle className="w-4 h-4 text-yellow-400 shrink-0" />
          <span className="text-sm text-yellow-300 flex-1">
            {confirmAction === "restart"
              ? "重啟服務將導致所有通過此服務的連線短暫中斷，確定繼續？"
              : "停止服務將導致所有連線中斷，確定繼續？"}
          </span>
          <Button
            size="sm"
            variant="destructive"
            className="h-7 text-xs"
            onClick={() => actionMutation.mutate(confirmAction)}
            disabled={actionMutation.isPending}
          >
            確定
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="h-7 text-xs"
            onClick={() => setConfirmAction(null)}
          >
            取消
          </Button>
        </div>
      )}

      {/* 日誌面板 */}
      {showLogs && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted-foreground">
              服務日誌（最近 50 行）
            </span>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={fetchLogs}
                disabled={logsLoading}
                className="h-7 text-xs border-border bg-card hover:bg-accent"
              >
                {logsLoading ? (
                  <Loader2 className="w-3 h-3 animate-spin mr-1" />
                ) : (
                  <RefreshCw className="w-3 h-3 mr-1" />
                )}
                重新整理
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setShowLogs(false)}
                className="h-7 text-xs"
              >
                關閉
              </Button>
            </div>
          </div>
          <pre className="bg-black/80 border border-border rounded-lg p-4 text-xs text-green-400 font-mono overflow-auto max-h-80 whitespace-pre-wrap">
            {logs || "（無日誌）"}
          </pre>
        </div>
      )}

      {/* 編輯器 */}
      <div className="border border-border rounded-lg overflow-hidden">
        {configLoading ? (
          <div className="h-96 flex items-center justify-center bg-card">
            <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <Editor
            height="480px"
            language={configLang[service]}
            theme="vs-dark"
            value={editorContent}
            onChange={(v: string | undefined) => setEditorContent(v ?? "")}
            options={{
              minimap: { enabled: false },
              fontSize: 13,
              lineNumbers: "on",
              scrollBeyondLastLine: false,
              wordWrap: "off",
              tabSize: 2,
            }}
          />
        )}
      </div>

      <div className="flex justify-end">
        <Button
          onClick={() => saveMutation.mutate()}
          disabled={saveMutation.isPending}
          className="bg-emerald-700 hover:bg-emerald-600 text-white"
        >
          {saveMutation.isPending ? (
            <Loader2 className="w-4 h-4 animate-spin mr-2" />
          ) : (
            <Save className="w-4 h-4 mr-2" />
          )}
          儲存設定
        </Button>
      </div>
    </div>
  )
}

// ─── 連線設定面板 ──────────────────────────────────────────────────────────────

function ConnectionPanel() {
  const queryClient = useQueryClient()
  const [host, setHost] = useState("")
  const [sshPort, setSshPort] = useState(22)
  const [sshUser, setSshUser] = useState("root")

  const syncMutation = useMutation({
    mutationFn: async () => {
      await FirewallService.syncNATRules()
      await FirewallService.syncReverseProxyRules()
    },
    onSuccess: () =>
      toast.success("Port Forwarding 與反向代理規則已同步到 Gateway VM"),
    onError: (error: unknown) =>
      toast.error(`同步失敗：${getApiErrorMessage(error)}`),
  })

  const { data: config, isLoading } = useQuery({
    queryKey: ["gateway-config-conn"],
    queryFn: GatewayApiService.getConfig,
  })

  const { data: cloudflareConfig } = useQuery({
    queryKey: ["cloudflare-config"],
    queryFn: CloudflareApiService.getConfig,
    retry: false,
  })

  const {
    data: serviceVersions,
    isLoading: serviceVersionsLoading,
    isFetching: serviceVersionsFetching,
    refetch: refetchServiceVersions,
  } = useQuery({
    queryKey: ["gateway-service-versions"],
    queryFn: GatewayApiService.getServiceVersions,
    enabled: !!config?.is_configured,
    retry: false,
  })

  useEffect(() => {
    if (config) {
      setHost(config.host)
      setSshPort(config.ssh_port)
      setSshUser(config.ssh_user)
    }
  }, [config])

  const saveMutation = useMutation({
    mutationFn: () =>
      GatewayApiService.updateConfig({
        host,
        ssh_port: sshPort,
        ssh_user: sshUser,
      }),
    onSuccess: () => {
      toast.success("連線設定已儲存")
      queryClient.invalidateQueries({ queryKey: ["gateway-config-conn"] })
    },
    onError: (error: unknown) =>
      toast.error(`儲存失敗：${getApiErrorMessage(error)}`),
  })

  const keypairMutation = useMutation({
    mutationFn: GatewayApiService.generateKeypair,
    onSuccess: () => {
      toast.success("SSH Keypair 已生成")
      queryClient.invalidateQueries({ queryKey: ["gateway-config-conn"] })
    },
    onError: (error: unknown) =>
      toast.error(`生成失敗：${getApiErrorMessage(error)}`),
  })

  const testMutation = useMutation({
    mutationFn: GatewayApiService.testConnection,
    onSuccess: (data) => {
      if (data.success) {
        toast.success(data.message)
      } else {
        toast.error(data.message)
      }
    },
  })

  const dnsChallengeMutation = useMutation({
    mutationFn: GatewayApiService.syncTraefikDnsChallenge,
    onSuccess: (data) => {
      toast.success(data.message)
      queryClient.invalidateQueries({ queryKey: ["gateway-config", "traefik"] })
      queryClient.invalidateQueries({ queryKey: ["gateway-status", "traefik"] })
    },
    onError: (error: unknown) =>
      toast.error(`套用失敗：${getApiErrorMessage(error)}`),
  })

  const copyPublicKey = () => {
    if (config?.public_key) {
      navigator.clipboard.writeText(config.public_key)
      toast.success("公鑰已複製到剪貼簿")
    }
  }

  if (isLoading) {
    return (
      <div className="flex justify-center py-12">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* SSH 連線設定 */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base text-foreground/90 flex items-center gap-2">
            <Server className="w-4 h-4" />
            SSH 連線設定
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-3 gap-4">
            <div className="col-span-2 space-y-1.5">
              <Label className="text-muted-foreground text-xs">
                Gateway VM IP
              </Label>
              <Input
                value={host}
                onChange={(e) => setHost(e.target.value)}
                placeholder="192.168.1.100"
                className="bg-card border-border text-foreground h-9"
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-muted-foreground text-xs">SSH Port</Label>
              <Input
                type="number"
                value={sshPort}
                onChange={(e) => setSshPort(Number(e.target.value))}
                className="bg-card border-border text-foreground h-9"
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label className="text-muted-foreground text-xs">SSH 使用者</Label>
            <Input
              value={sshUser}
              onChange={(e) => setSshUser(e.target.value)}
              placeholder="root"
              className="bg-card border-border text-foreground h-9 w-48"
            />
          </div>
          <div className="flex gap-2">
            <Button
              onClick={() => saveMutation.mutate()}
              disabled={saveMutation.isPending}
              size="sm"
              className="bg-emerald-700 hover:bg-emerald-600 text-white"
            >
              {saveMutation.isPending && (
                <Loader2 className="w-3 h-3 animate-spin mr-1" />
              )}
              <Save className="w-3 h-3 mr-1" />
              儲存
            </Button>
            <Button
              onClick={() => testMutation.mutate()}
              disabled={testMutation.isPending || !config?.is_configured}
              size="sm"
              variant="outline"
              className="border-border bg-card hover:bg-accent"
            >
              {testMutation.isPending ? (
                <Loader2 className="w-3 h-3 animate-spin mr-1" />
              ) : testMutation.data?.success ? (
                <Wifi className="w-3 h-3 mr-1 text-emerald-400" />
              ) : testMutation.data ? (
                <WifiOff className="w-3 h-3 mr-1 text-red-400" />
              ) : (
                <Activity className="w-3 h-3 mr-1" />
              )}
              測試連線
            </Button>
            <Button
              onClick={() => syncMutation.mutate()}
              disabled={syncMutation.isPending || !config?.is_configured}
              size="sm"
              variant="outline"
              className="border-border bg-card hover:bg-accent"
              title="將資料庫中的 Port Forwarding + 反向代理規則重新同步到 Gateway VM"
            >
              {syncMutation.isPending ? (
                <Loader2 className="w-3 h-3 animate-spin mr-1" />
              ) : (
                <RefreshCw className="w-3 h-3 mr-1" />
              )}
              同步規則到 Gateway
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* SSH Keypair */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base text-foreground/90 flex items-center gap-2">
            <ShieldAlert className="w-4 h-4" />
            SSH 金鑰管理
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {config?.public_key ? (
            <div className="space-y-2">
              <Label className="text-muted-foreground text-xs">
                公鑰（貼到 Gateway VM 的 authorized_keys）
              </Label>
              <div className="flex gap-2">
                <code className="flex-1 text-xs bg-muted/80 border border-border rounded px-3 py-2 text-emerald-400 break-all font-mono leading-relaxed">
                  {config.public_key}
                </code>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={copyPublicKey}
                  className="shrink-0 border-border bg-card hover:bg-accent h-auto"
                >
                  <ClipboardCopy className="w-3 h-3" />
                </Button>
              </div>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              尚未生成 SSH 金鑰，請點擊下方按鈕生成。
            </p>
          )}
          <Button
            onClick={() => keypairMutation.mutate()}
            disabled={keypairMutation.isPending}
            size="sm"
            variant="outline"
            className="border-border bg-card hover:bg-accent"
          >
            {keypairMutation.isPending && (
              <Loader2 className="w-3 h-3 animate-spin mr-1" />
            )}
            <RefreshCw className="w-3 h-3 mr-1" />
            {config?.public_key ? "重新生成 Keypair" : "生成 SSH Keypair"}
          </Button>
          {config?.public_key && (
            <p className="text-xs text-yellow-600">
              ⚠ 重新生成後舊公鑰將失效，需重新將新公鑰加入 Gateway VM。
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base text-foreground/90 flex items-center gap-2">
            <ShieldAlert className="w-4 h-4" />
            Traefik 憑證設定
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="rounded-lg border border-border bg-card px-4 py-3 space-y-2">
            <div className="flex items-center gap-2 text-sm text-foreground/80">
              {cloudflareConfig?.is_configured ? (
                <CheckCircle2 className="w-4 h-4 text-emerald-400" />
              ) : (
                <AlertTriangle className="w-4 h-4 text-yellow-500" />
              )}
              <span>
                {cloudflareConfig?.is_configured
                  ? "已偵測到 admin/domains 的 Cloudflare API Token"
                  : "尚未在 admin/domains 設定 Cloudflare API Token"}
              </span>
            </div>
            <p className="text-xs text-muted-foreground leading-relaxed">
              套用後會將 Gateway VM 上的 Traefik 切換為 Cloudflare DNS
              Challenge，並保留 127.0.0.1:8080 的 runtime API 供 SkyLab
              後端查詢。
            </p>
            {cloudflareConfig?.last_verified_at && (
              <p className="text-xs text-muted-foreground">
                最近驗證：
                {new Date(cloudflareConfig.last_verified_at).toLocaleString()}
              </p>
            )}
          </div>
          <Button
            onClick={() => dnsChallengeMutation.mutate()}
            disabled={
              dnsChallengeMutation.isPending ||
              !config?.is_configured ||
              !cloudflareConfig?.is_configured
            }
            size="sm"
            variant="outline"
            className="border-border bg-card hover:bg-accent"
          >
            {dnsChallengeMutation.isPending ? (
              <Loader2 className="w-3 h-3 animate-spin mr-1" />
            ) : (
              <RefreshCw className="w-3 h-3 mr-1" />
            )}
            套用 Cloudflare DNS Challenge
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base text-foreground/90 flex items-center gap-2">
            <RefreshCw className="w-4 h-4" />
            軟體版本偵測
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card px-4 py-3">
            <div>
              <p className="text-sm text-foreground/90">
                讀取 Gateway VM
                上實際安裝版本，並與平台安裝腳本或套件來源的目標版本比較。
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                若顯示可更新，通常代表重新執行安裝腳本或升級套件後可與平台維持一致。
              </p>
            </div>
            <Button
              onClick={() => refetchServiceVersions()}
              disabled={!config?.is_configured || serviceVersionsFetching}
              size="sm"
              variant="outline"
              className="border-border bg-card hover:bg-accent"
            >
              {serviceVersionsFetching ? (
                <Loader2 className="w-3 h-3 animate-spin mr-1" />
              ) : (
                <RefreshCw className="w-3 h-3 mr-1" />
              )}
              重新檢查
            </Button>
          </div>

          {!config?.is_configured ? (
            <p className="text-sm text-muted-foreground">
              請先完成 Gateway VM 連線設定。
            </p>
          ) : serviceVersionsLoading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
            </div>
          ) : serviceVersions?.items?.length ? (
            <div className="grid gap-3 md:grid-cols-2">
              {serviceVersions.items.map((item) => {
                const statusText =
                  item.update_available === null
                    ? "狀態未知"
                    : item.update_available
                      ? "可更新"
                      : "已最新"
                const statusClass =
                  item.update_available === null
                    ? "text-muted-foreground"
                    : item.update_available
                      ? "text-amber-500"
                      : "text-emerald-500"

                return (
                  <div
                    key={item.service}
                    className="rounded-lg border border-border bg-muted/20 px-4 py-3 space-y-2"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <div className="text-sm font-medium text-foreground">
                        {item.service}
                      </div>
                      <div className={`text-xs font-medium ${statusClass}`}>
                        {statusText}
                      </div>
                    </div>
                    <div className="space-y-1 text-xs text-muted-foreground">
                      <div>
                        目前版本：
                        <span className="ml-1 font-mono text-foreground">
                          {item.current_version ?? "未偵測"}
                        </span>
                      </div>
                      <div>
                        目標版本：
                        <span className="ml-1 font-mono text-foreground">
                          {item.target_version ?? "未提供"}
                        </span>
                      </div>
                      <div>
                        來源：
                        <span className="ml-1 text-foreground">
                          {item.source}
                        </span>
                      </div>
                      {item.detection_error && (
                        <div className="text-amber-500">
                          偵測失敗：{item.detection_error}
                        </div>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">尚未取得版本資訊。</p>
          )}

          {serviceVersions?.checked_at && (
            <p className="text-xs text-muted-foreground">
              最近檢查：
              {new Date(serviceVersions.checked_at).toLocaleString("zh-TW")}
            </p>
          )}
        </CardContent>
      </Card>

      {/* 安裝教學 */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base text-foreground/90 flex items-center gap-2">
            <Download className="w-4 h-4" />
            Gateway VM 安裝指引
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4 text-sm text-muted-foreground">
          <ol className="space-y-4 list-none">
            {/* 步驟 1 */}
            <li className="flex gap-3">
              <span className="w-6 h-6 rounded-full bg-accent text-foreground/80 text-xs flex items-center justify-center shrink-0 mt-0.5">
                1
              </span>
              <div>
                <p className="text-foreground/80 font-medium">
                  準備一台 Debian 12 VM
                </p>
                <p className="text-muted-foreground mt-0.5">
                  建議規格：1 vCPU、512MB RAM、8GB Disk。確保 VM 有外網
                  IP（FortiGate 指向此 IP）。
                </p>
              </div>
            </li>

            {/* 步驟 2 */}
            <li className="flex gap-3">
              <span className="w-6 h-6 rounded-full bg-accent text-foreground/80 text-xs flex items-center justify-center shrink-0 mt-0.5">
                2
              </span>
              <div>
                <p className="text-foreground/80 font-medium">
                  生成 SSH Keypair
                </p>
                <p className="text-muted-foreground mt-0.5">
                  點擊上方「生成 SSH Keypair」按鈕。
                </p>
              </div>
            </li>

            {/* 步驟 3：一行安裝指令 */}
            <li className="flex gap-3">
              <span className="w-6 h-6 rounded-full bg-accent text-foreground/80 text-xs flex items-center justify-center shrink-0 mt-0.5">
                3
              </span>
              <div className="flex-1 min-w-0">
                <p className="text-foreground/80 font-medium">
                  在 Gateway VM 上執行安裝指令
                </p>
                <p className="text-muted-foreground mt-0.5 mb-2">
                  以 root 身份登入 Gateway
                  VM，複製下方指令貼上執行。指令會自動安裝所有服務並設定 SSH
                  金鑰。
                </p>
                {config?.public_key ? (
                  <InstallCommand publicKey={config.public_key} />
                ) : (
                  <div className="bg-muted/80 border border-border rounded p-3 text-yellow-600 text-xs">
                    ⚠ 請先生成 SSH Keypair 才能取得安裝指令
                  </div>
                )}
              </div>
            </li>

            {/* 步驟 4 */}
            <li className="flex gap-3">
              <span className="w-6 h-6 rounded-full bg-accent text-foreground/80 text-xs flex items-center justify-center shrink-0 mt-0.5">
                4
              </span>
              <div>
                <p className="text-foreground/80 font-medium">
                  填入 IP 並測試連線
                </p>
                <p className="text-muted-foreground mt-0.5">
                  在上方連線設定填入 Gateway VM
                  IP，點擊「測試連線」確認成功；若要讓 Traefik 用 Cloudflare DNS
                  Challenge 申請憑證，再套用一次上方憑證設定。
                </p>
              </div>
            </li>
          </ol>
        </CardContent>
      </Card>
    </div>
  )
}

// ─── 主頁面 ────────────────────────────────────────────────────────────────────

function GatewayPage() {
  const { data: config } = useQuery({
    queryKey: ["gateway-config-conn"],
    queryFn: GatewayApiService.getConfig,
  })

  const isConfigured = config?.is_configured ?? false

  return (
    <div className="p-6 space-y-6 max-w-5xl mx-auto">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-foreground">
            Gateway VM 管理
          </h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            管理 haproxy、Traefik、frp 服務設定與狀態
          </p>
        </div>
        <div className="flex items-center gap-2 text-sm">
          {isConfigured ? (
            <>
              <CheckCircle2 className="w-4 h-4 text-emerald-400" />
              <span className="text-emerald-400">{config?.host}</span>
            </>
          ) : (
            <>
              <XCircle className="w-4 h-4 text-muted-foreground" />
              <span className="text-muted-foreground">尚未設定</span>
            </>
          )}
        </div>
      </div>

      <Tabs defaultValue="connection" className="space-y-4">
        <TabsList>
          {[
            { value: "connection", label: "連線設定" },
            { value: "haproxy", label: "haproxy" },
            { value: "traefik", label: "Traefik" },
            { value: "frps", label: "frps" },
            { value: "frpc", label: "frpc" },
          ].map(({ value, label }) => (
            <TabsTrigger key={value} value={value}>
              {label}
            </TabsTrigger>
          ))}
        </TabsList>

        <TabsContent value="connection">
          <ConnectionPanel />
        </TabsContent>

        {(["haproxy", "traefik", "frps", "frpc"] as GatewayService[]).map(
          (svc) => (
            <TabsContent key={svc} value={svc}>
              {isConfigured ? (
                <ServicePanel service={svc} />
              ) : (
                <div className="flex flex-col items-center justify-center py-16 text-muted-foreground space-y-2">
                  <Server className="w-10 h-10" />
                  <p>請先完成連線設定</p>
                </div>
              )}
            </TabsContent>
          ),
        )}
      </Tabs>
    </div>
  )
}
