import { useMutation, useQuery } from "@tanstack/react-query"
import { Globe, Loader2, Lock, Server, WandSparkles } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { toast } from "sonner"

import { type ApiError, type ResourcePublic, ResourcesService } from "@/client"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import useAuth from "@/hooks/useAuth"
import {
  type ManagedReverseProxyRule,
  ReverseProxyApiService,
  type ReverseProxySetupContext,
} from "@/services/reverseProxy"

type Props = {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCompleted?: () => void
  setupContext?: ReverseProxySetupContext
  rule?: ManagedReverseProxyRule | null
}

function isAdminUser(
  user: { role?: string; is_superuser?: boolean } | null | undefined,
) {
  return user?.role === "admin" || user?.is_superuser === true
}

function errorMessage(error: unknown, fallback: string) {
  const apiError = error as ApiError & { body?: { detail?: string } }
  return apiError.body?.detail ?? apiError.message ?? fallback
}

function findZoneByDomain(
  domain: string,
  setupContext?: ReverseProxySetupContext,
) {
  const zones = setupContext?.zones ?? []
  return [...zones]
    .sort((left, right) => right.name.length - left.name.length)
    .find((zone) => domain === zone.name || domain.endsWith(`.${zone.name}`))
}

function extractHostnamePrefix(domain: string, zoneName: string) {
  if (domain === zoneName) return ""
  const suffix = `.${zoneName}`
  if (!domain.endsWith(suffix)) return domain
  return domain.slice(0, -suffix.length)
}

function buildPreviewDomain(hostnamePrefix: string, zoneName: string) {
  const normalizedPrefix = hostnamePrefix
    .trim()
    .toLowerCase()
    .replace(/^\.+|\.+$/g, "")
  const normalizedZoneName = zoneName.trim().toLowerCase()
  if (!normalizedPrefix) return normalizedZoneName
  return `${normalizedPrefix}.${normalizedZoneName}`
}

const COMMON_PORTS = [
  { value: "80", label: "80 — Nginx / Apache（網頁伺服器）" },
  { value: "443", label: "443 — HTTPS" },
  { value: "3000", label: "3000 — Node.js / React / Next.js" },
  { value: "5000", label: "5000 — Flask / Python" },
  { value: "8000", label: "8000 — FastAPI / Django" },
  { value: "8080", label: "8080 — 常見替代 Port" },
  { value: "8888", label: "8888 — Jupyter Notebook" },
] as const

export function CreateReverseProxyRuleDialog({
  open,
  onOpenChange,
  onCompleted,
  setupContext,
  rule,
}: Props) {
  const { user } = useAuth()
  const [selectedVmid, setSelectedVmid] = useState("")
  const [selectedZoneId, setSelectedZoneId] = useState("")
  const [hostnamePrefix, setHostnamePrefix] = useState("")
  const [internalPort, setInternalPort] = useState("80")
  const [customPort, setCustomPort] = useState("")
  const [useCustomPort, setUseCustomPort] = useState(false)
  const [enableHttps, setEnableHttps] = useState(true)

  const isAdmin = isAdminUser(user)
  const isEditMode = Boolean(rule)

  const resourcesQuery = useQuery({
    queryKey: ["reverse-proxy-resource-options", isAdmin ? "all" : "mine"],
    queryFn: () =>
      isAdmin
        ? ResourcesService.listResources({})
        : ResourcesService.listMyResources(),
    enabled: open && !!user,
    staleTime: 30_000,
  })

  const createOrUpdateRuleMutation = useMutation({
    mutationFn: () => {
      const port = useCustomPort ? Number(customPort) : Number(internalPort)
      const payload = {
        vmid: Number(selectedVmid),
        zone_id: selectedZoneId,
        hostname_prefix: hostnamePrefix.trim().toLowerCase(),
        internal_port: port,
        enable_https: enableHttps,
      }

      if (rule) {
        return ReverseProxyApiService.updateRule({
          ruleId: rule.id,
          requestBody: payload,
        })
      }

      return ReverseProxyApiService.createRule(payload)
    },
    onSuccess: () => {
      toast.success(
        isEditMode
          ? "網域規則已更新，Cloudflare 與路由設定已同步"
          : "網域規則建立成功，系統正在自動設定 Cloudflare 與路由",
      )
      onCompleted?.()
      onOpenChange(false)
    },
    onError: (error: unknown) => {
      toast.error(
        errorMessage(
          error,
          isEditMode ? "更新網域規則失敗" : "建立網域規則失敗",
        ),
      )
    },
  })

  useEffect(() => {
    if (!open) {
      return
    }

    if (rule) {
      const matchedZone =
        setupContext?.zones.find((zone) => zone.id === rule.zone_id) ??
        findZoneByDomain(rule.domain, setupContext)
      const matchedCommonPort = COMMON_PORTS.find(
        (port) => port.value === String(rule.internal_port),
      )

      setSelectedVmid(String(rule.vmid))
      setSelectedZoneId(matchedZone?.id ?? "")
      setHostnamePrefix(
        matchedZone
          ? extractHostnamePrefix(rule.domain, matchedZone.name)
          : rule.domain,
      )
      setEnableHttps(rule.enable_https)
      setInternalPort(matchedCommonPort?.value ?? "80")
      setUseCustomPort(!matchedCommonPort)
      setCustomPort(matchedCommonPort ? "" : String(rule.internal_port))
      return
    }

    setSelectedVmid("")
    setSelectedZoneId(setupContext?.zones[0]?.id ?? "")
    setHostnamePrefix("")
    setInternalPort("80")
    setCustomPort("")
    setUseCustomPort(false)
    setEnableHttps(true)
  }, [open, rule, setupContext])

  const resources = (resourcesQuery.data ?? []) as ResourcePublic[]
  const selectedResource = resources.find(
    (resource) => String(resource.vmid) === selectedVmid,
  )
  const selectedZone = setupContext?.zones.find(
    (zone) => zone.id === selectedZoneId,
  )
  const effectivePort = useCustomPort ? customPort : internalPort
  const previewDomain = selectedZone
    ? buildPreviewDomain(hostnamePrefix, selectedZone.name)
    : ""
  const scheme = enableHttps ? "https" : "http"
  const setupBlocked = setupContext?.enabled === false
  const automationTarget = useMemo(() => {
    if (
      !setupContext?.default_dns_target_type ||
      !setupContext.default_dns_target_value
    ) {
      return null
    }
    return `${setupContext.default_dns_target_type} ${setupContext.default_dns_target_value}`
  }, [setupContext])

  const handleCreateOrUpdate = () => {
    if (setupBlocked) {
      toast.error(setupContext?.reasons[0] ?? "反向代理功能目前不可用")
      return
    }
    if (!selectedVmid) {
      toast.error("請先選擇你要綁定的 VM")
      return
    }
    if (!selectedZoneId) {
      toast.error("請先選擇 Cloudflare Zone")
      return
    }

    const parsedPort = Number(effectivePort)
    if (!Number.isInteger(parsedPort) || parsedPort < 1 || parsedPort > 65535) {
      toast.error("Port 必須是 1 到 65535 之間的數字")
      return
    }

    createOrUpdateRuleMutation.mutate()
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Globe className="h-5 w-5 text-sky-500" />
            {isEditMode ? "編輯網域規則" : "新增網域"}
          </DialogTitle>
          <DialogDescription>
            反向代理網址只能綁定到 Cloudflare 中已存在的
            Zone。儲存後，系統會自動把 DNS record 指向預設目標並同步 Gateway
            路由。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5">
          {setupBlocked && (
            <Alert variant="destructive">
              <AlertTitle>反向代理功能暫時不可用</AlertTitle>
              <AlertDescription>
                {(setupContext?.reasons ?? []).join("；") || "請先完成必要設定"}
              </AlertDescription>
            </Alert>
          )}

          {automationTarget && (
            <div className="rounded-xl border border-sky-500/20 bg-sky-500/5 px-4 py-3 text-sm">
              <div className="flex items-center gap-2 font-medium text-foreground">
                <WandSparkles className="h-4 w-4 text-sky-500" />
                自動化目標
              </div>
              <p className="mt-1 text-muted-foreground">
                建立或更新網域時，Cloudflare 會自動建立/更新成指向
                <span className="ml-1 font-mono text-foreground">
                  {automationTarget}
                </span>
              </p>
            </div>
          )}

          <div className="space-y-2">
            <Label className="flex items-center gap-2">
              <Server className="h-4 w-4 text-muted-foreground" />
              選擇你的 VM
            </Label>
            <Select
              value={selectedVmid}
              onValueChange={setSelectedVmid}
              disabled={setupBlocked}
            >
              <SelectTrigger>
                <SelectValue placeholder="選擇一台 VM..." />
              </SelectTrigger>
              <SelectContent>
                {resources.map((resource) => (
                  <SelectItem key={resource.vmid} value={String(resource.vmid)}>
                    {resource.name} (VM {resource.vmid})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {resourcesQuery.isLoading && (
              <p className="text-xs text-muted-foreground">
                正在載入你的 VM 列表...
              </p>
            )}
            {!resourcesQuery.isLoading && resources.length === 0 && (
              <p className="text-xs text-amber-600 dark:text-amber-400">
                你目前沒有任何 VM，請先建立一台 VM。
              </p>
            )}
          </div>

          <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_220px]">
            <div className="space-y-2">
              <Label className="flex items-center gap-2">
                <Globe className="h-4 w-4 text-muted-foreground" />
                主機名前綴
              </Label>
              <Input
                value={hostnamePrefix}
                onChange={(event) => setHostnamePrefix(event.target.value)}
                placeholder="例如 app、student-portal，留空代表根網域"
                disabled={setupBlocked}
              />
              <p className="text-xs text-muted-foreground">
                你只需要輸入前綴，後綴會固定使用 Cloudflare Zone。
              </p>
            </div>

            <div className="space-y-2">
              <Label>Cloudflare Zone</Label>
              <Select
                value={selectedZoneId}
                onValueChange={setSelectedZoneId}
                disabled={
                  setupBlocked || (setupContext?.zones.length ?? 0) === 0
                }
              >
                <SelectTrigger>
                  <SelectValue placeholder="選擇網域後綴" />
                </SelectTrigger>
                <SelectContent>
                  {(setupContext?.zones ?? []).map((zone) => (
                    <SelectItem key={zone.id} value={zone.id}>
                      {zone.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-2">
            <Label>你的服務跑在哪個 Port？</Label>
            <p className="text-xs text-muted-foreground">
              如果你不確定，通常網頁伺服器用 80，Node.js 用 3000，Python 用
              5000。
            </p>
            {!useCustomPort ? (
              <Select
                value={internalPort}
                onValueChange={setInternalPort}
                disabled={setupBlocked}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {COMMON_PORTS.map((port) => (
                    <SelectItem key={port.value} value={port.value}>
                      {port.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <Input
                type="number"
                min={1}
                max={65535}
                value={customPort}
                onChange={(event) => setCustomPort(event.target.value)}
                placeholder="輸入 Port 號碼（1-65535）"
                disabled={setupBlocked}
              />
            )}
            <button
              type="button"
              className="text-xs text-sky-600 hover:underline disabled:cursor-not-allowed disabled:text-muted-foreground dark:text-sky-400"
              onClick={() => setUseCustomPort(!useCustomPort)}
              disabled={setupBlocked}
            >
              {useCustomPort ? "← 選擇常見 Port" : "我的 Port 不在列表中"}
            </button>
          </div>

          <div className="flex items-center justify-between rounded-xl border border-border/60 bg-muted/30 px-4 py-3">
            <div className="flex items-center gap-3">
              <Lock className="h-4 w-4 text-emerald-500" />
              <div>
                <div className="text-sm font-medium text-foreground">
                  啟用安全連線 (HTTPS)
                </div>
                <div className="text-xs text-muted-foreground">
                  會使用 Gateway 上的 Traefik 與 Cloudflare DNS Challenge
                  自動處理憑證。
                </div>
              </div>
            </div>
            <Switch
              checked={enableHttps}
              onCheckedChange={setEnableHttps}
              disabled={setupBlocked}
            />
          </div>

          {previewDomain && selectedVmid && (
            <div className="rounded-xl border border-sky-500/20 bg-sky-500/5 p-4 text-sm">
              <div className="font-medium text-foreground">設定預覽</div>
              <div className="mt-2 space-y-1.5 text-muted-foreground">
                <p>
                  當有人訪問
                  <span className="ml-1 font-mono font-semibold text-sky-600 dark:text-sky-400">
                    {scheme}://{previewDomain}
                  </span>
                </p>
                <p>
                  → 系統會自動在 Cloudflare 建立 DNS，並把流量導到
                  <span className="ml-1 font-semibold text-foreground">
                    {selectedResource?.name ?? `VM ${selectedVmid}`}
                  </span>
                  的 Port
                  <span className="ml-1 font-mono font-semibold text-foreground">
                    {effectivePort}
                  </span>
                </p>
              </div>
            </div>
          )}
        </div>

        <DialogFooter className="gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            onClick={handleCreateOrUpdate}
            disabled={
              createOrUpdateRuleMutation.isPending ||
              resources.length === 0 ||
              !selectedVmid ||
              !selectedZoneId ||
              setupBlocked
            }
          >
            {createOrUpdateRuleMutation.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Globe className="mr-2 h-4 w-4" />
            )}
            {isEditMode ? "儲存網域規則" : "建立網域規則"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
