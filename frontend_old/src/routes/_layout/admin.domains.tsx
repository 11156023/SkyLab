import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Cloud,
  ExternalLink,
  Globe,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
  Wifi,
  WifiOff,
} from "lucide-react"
import {
  startTransition,
  useDeferredValue,
  useEffect,
  useMemo,
  useState,
} from "react"
import { toast } from "sonner"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
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
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
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
import { cn } from "@/lib/utils"
import {
  CloudflareApiService,
  type CloudflareDNSRecordMutation,
  type CloudflareDNSRecordPublic,
  type CloudflareZonePublic,
} from "@/services/cloudflare"

export const Route = createFileRoute("/_layout/admin/domains")({
  component: AdminDomainsPage,
})

const ZONE_PAGE_SIZE = 100
const RECORD_PAGE_SIZE = 200
const ZONE_SKELETON_KEYS = ["zone-a", "zone-b", "zone-c", "zone-d"]
const RECORD_SKELETON_KEYS = [
  "record-a",
  "record-b",
  "record-c",
  "record-d",
  "record-e",
]
const RECORD_TYPES = [
  "A",
  "AAAA",
  "CNAME",
  "TXT",
  "MX",
  "SRV",
  "NS",
  "CAA",
  "PTR",
  "TLSA",
  "URI",
]
const PROXIABLE_TYPES = new Set(["A", "AAAA", "CNAME", "HTTPS", "SVCB"])
const PRIORITY_TYPES = new Set(["MX", "SRV", "URI"])
const DEFAULT_DNS_TARGET_TYPES = ["A", "CNAME"] as const

type RecordEditorMode = "create" | "edit"

type RecordFormState = {
  type: string
  name: string
  content: string
  ttl: number
  proxied: boolean
  comment: string
  priority: string
}

function createDefaultRecordForm(zoneName = ""): RecordFormState {
  return {
    type: "A",
    name: zoneName,
    content: "",
    ttl: 1,
    proxied: true,
    comment: "",
    priority: "",
  }
}

function getErrorMessage(error: unknown): string {
  if (typeof error === "object" && error !== null) {
    const candidate = error as {
      body?: { detail?: string }
      message?: string
    }

    if (candidate.body?.detail) {
      return candidate.body.detail
    }
    if (candidate.message) {
      return candidate.message
    }
  }
  return "發生未預期錯誤"
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "未提供"
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString("zh-TW")
}

function getZoneStatusClass(status: string) {
  switch (status) {
    case "active":
      return "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
    case "pending":
      return "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300"
    case "initializing":
      return "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300"
    case "moved":
      return "border-violet-500/30 bg-violet-500/10 text-violet-700 dark:text-violet-300"
    default:
      return "border-border/80 bg-muted/40 text-muted-foreground"
  }
}

function MetricCard({
  icon: Icon,
  title,
  value,
  description,
}: {
  icon: typeof Cloud
  title: string
  value: string
  description: string
}) {
  return (
    <Card className="border-border/60 bg-linear-to-br from-card via-card to-muted/40">
      <CardContent className="flex items-start justify-between gap-4 p-5">
        <div className="space-y-1">
          <p className="text-xs font-medium uppercase tracking-[0.2em] text-muted-foreground">
            {title}
          </p>
          <p className="text-2xl font-semibold tracking-tight">{value}</p>
          <p className="text-sm text-muted-foreground">{description}</p>
        </div>
        <div className="rounded-2xl border border-border/60 bg-background/70 p-3 shadow-sm">
          <Icon className="h-5 w-5 text-foreground/80" />
        </div>
      </CardContent>
    </Card>
  )
}

function EmptyState({
  title,
  description,
  action,
}: {
  title: string
  description: string
  action?: React.ReactNode
}) {
  return (
    <div className="flex min-h-56 flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-border/70 bg-muted/20 px-6 py-10 text-center">
      <div className="rounded-full border border-border/70 bg-background/80 p-3">
        <Globe className="h-5 w-5 text-muted-foreground" />
      </div>
      <div className="space-y-1">
        <p className="text-base font-medium">{title}</p>
        <p className="max-w-md text-sm text-muted-foreground">{description}</p>
      </div>
      {action}
    </div>
  )
}

function AdminDomainsPage() {
  const queryClient = useQueryClient()

  const [accountIdInput, setAccountIdInput] = useState("")
  const [apiTokenInput, setApiTokenInput] = useState("")
  const [defaultDnsTargetType, setDefaultDnsTargetType] = useState("A")
  const [defaultDnsTargetValue, setDefaultDnsTargetValue] = useState("")

  const [zoneDialogOpen, setZoneDialogOpen] = useState(false)
  const [zoneNameInput, setZoneNameInput] = useState("")
  const [zoneAccountIdInput, setZoneAccountIdInput] = useState("")
  const [zoneJumpStart, setZoneJumpStart] = useState(false)

  const [zoneSearchInput, setZoneSearchInput] = useState("")
  const [zoneStatusFilter, setZoneStatusFilter] = useState("all")
  const zoneSearch = useDeferredValue(zoneSearchInput.trim())

  const [selectedZoneId, setSelectedZoneId] = useState<string | null>(null)

  const [recordSearchInput, setRecordSearchInput] = useState("")
  const [recordTypeFilter, setRecordTypeFilter] = useState("all")
  const recordSearch = useDeferredValue(recordSearchInput.trim())

  const [recordSheetOpen, setRecordSheetOpen] = useState(false)
  const [recordEditorMode, setRecordEditorMode] =
    useState<RecordEditorMode>("create")
  const [editingRecord, setEditingRecord] =
    useState<CloudflareDNSRecordPublic | null>(null)
  const [recordForm, setRecordForm] = useState<RecordFormState>(
    createDefaultRecordForm(),
  )
  const [recordPendingDelete, setRecordPendingDelete] =
    useState<CloudflareDNSRecordPublic | null>(null)

  const configQuery = useQuery({
    queryKey: ["cloudflare-config"],
    queryFn: CloudflareApiService.getConfig,
  })

  const isConfigured = configQuery.data?.is_configured ?? false

  const zonesQuery = useQuery({
    queryKey: ["cloudflare-zones", zoneSearch, zoneStatusFilter],
    queryFn: () =>
      CloudflareApiService.listZones({
        page: 1,
        per_page: ZONE_PAGE_SIZE,
        search: zoneSearch || undefined,
        status: zoneStatusFilter === "all" ? undefined : zoneStatusFilter,
      }),
    enabled: isConfigured,
  })

  const zoneDetailsQuery = useQuery({
    queryKey: ["cloudflare-zone", selectedZoneId],
    queryFn: () => CloudflareApiService.getZone(selectedZoneId ?? ""),
    enabled: Boolean(isConfigured && selectedZoneId),
  })

  const dnsRecordsQuery = useQuery({
    queryKey: [
      "cloudflare-dns-records",
      selectedZoneId,
      recordSearch,
      recordTypeFilter,
    ],
    queryFn: () =>
      CloudflareApiService.listDnsRecords(selectedZoneId ?? "", {
        page: 1,
        per_page: RECORD_PAGE_SIZE,
        search: recordSearch || undefined,
        type: recordTypeFilter === "all" ? undefined : recordTypeFilter,
      }),
    enabled: Boolean(isConfigured && selectedZoneId),
  })

  useEffect(() => {
    if (!configQuery.data) return
    setAccountIdInput(configQuery.data.account_id ?? "")
    setApiTokenInput("")
    setDefaultDnsTargetType(configQuery.data.default_dns_target_type ?? "A")
    setDefaultDnsTargetValue(configQuery.data.default_dns_target_value ?? "")
  }, [configQuery.data])

  useEffect(() => {
    if (!zoneDialogOpen) return
    setZoneAccountIdInput(configQuery.data?.account_id ?? "")
  }, [zoneDialogOpen, configQuery.data?.account_id])

  useEffect(() => {
    const zones = zonesQuery.data?.items ?? []
    if (zones.length === 0) {
      setSelectedZoneId(null)
      return
    }

    if (!selectedZoneId || !zones.some((zone) => zone.id === selectedZoneId)) {
      startTransition(() => {
        setSelectedZoneId(zones[0]?.id ?? null)
      })
    }
  }, [zonesQuery.data?.items, selectedZoneId])

  useEffect(() => {
    if (!recordSheetOpen) return
    if (!PROXIABLE_TYPES.has(recordForm.type) && recordForm.proxied) {
      setRecordForm((current) => ({ ...current, proxied: false }))
      return
    }
    if (!PRIORITY_TYPES.has(recordForm.type) && recordForm.priority !== "") {
      setRecordForm((current) => ({ ...current, priority: "" }))
    }
  }, [
    recordForm.priority,
    recordForm.proxied,
    recordForm.type,
    recordSheetOpen,
  ])

  const selectedZone = useMemo<CloudflareZonePublic | null>(() => {
    if (zoneDetailsQuery.data) {
      return zoneDetailsQuery.data
    }
    return (
      zonesQuery.data?.items.find((zone) => zone.id === selectedZoneId) ?? null
    )
  }, [selectedZoneId, zoneDetailsQuery.data, zonesQuery.data?.items])

  const saveConfigMutation = useMutation({
    mutationFn: () =>
      CloudflareApiService.updateConfig({
        account_id: accountIdInput.trim() || null,
        api_token: apiTokenInput.trim() || null,
        default_dns_target_type: defaultDnsTargetValue.trim()
          ? defaultDnsTargetType
          : null,
        default_dns_target_value: defaultDnsTargetValue.trim() || null,
      }),
    onSuccess: () => {
      toast.success("Cloudflare 設定已儲存")
      setApiTokenInput("")
      queryClient.invalidateQueries({ queryKey: ["cloudflare-config"] })
      queryClient.invalidateQueries({ queryKey: ["cloudflare-zones"] })
    },
    onError: (error) => {
      toast.error(getErrorMessage(error))
    },
  })

  const testConfigMutation = useMutation({
    mutationFn: CloudflareApiService.testConfig,
    onSuccess: (data) => {
      toast.success(data.message)
      queryClient.invalidateQueries({ queryKey: ["cloudflare-config"] })
    },
    onError: (error) => {
      toast.error(getErrorMessage(error))
    },
  })

  const createZoneMutation = useMutation({
    mutationFn: () =>
      CloudflareApiService.createZone({
        name: zoneNameInput.trim(),
        account_id: zoneAccountIdInput.trim() || null,
        jump_start: zoneJumpStart,
      }),
    onSuccess: (zone) => {
      toast.success(`Zone ${zone.name} 已建立`)
      setZoneDialogOpen(false)
      setZoneNameInput("")
      setZoneJumpStart(false)
      queryClient.invalidateQueries({ queryKey: ["cloudflare-zones"] })
      startTransition(() => {
        setSelectedZoneId(zone.id)
      })
    },
    onError: (error) => {
      toast.error(getErrorMessage(error))
    },
  })

  const createRecordMutation = useMutation({
    mutationFn: (payload: CloudflareDNSRecordMutation) =>
      CloudflareApiService.createDnsRecord(selectedZoneId ?? "", payload),
    onSuccess: (record) => {
      toast.success(`已新增 ${record.type} record`)
      setRecordSheetOpen(false)
      setEditingRecord(null)
      queryClient.invalidateQueries({
        queryKey: ["cloudflare-dns-records", selectedZoneId],
      })
    },
    onError: (error) => {
      toast.error(getErrorMessage(error))
    },
  })

  const updateRecordMutation = useMutation({
    mutationFn: ({
      recordId,
      payload,
    }: {
      recordId: string
      payload: CloudflareDNSRecordMutation
    }) =>
      CloudflareApiService.updateDnsRecord(
        selectedZoneId ?? "",
        recordId,
        payload,
      ),
    onSuccess: (record) => {
      toast.success(`已更新 ${record.type} record`)
      setRecordSheetOpen(false)
      setEditingRecord(null)
      queryClient.invalidateQueries({
        queryKey: ["cloudflare-dns-records", selectedZoneId],
      })
    },
    onError: (error) => {
      toast.error(getErrorMessage(error))
    },
  })

  const deleteRecordMutation = useMutation({
    mutationFn: (record: CloudflareDNSRecordPublic) =>
      CloudflareApiService.deleteDnsRecord(selectedZoneId ?? "", record.id),
    onSuccess: () => {
      toast.success("DNS record 已刪除")
      setRecordPendingDelete(null)
      queryClient.invalidateQueries({
        queryKey: ["cloudflare-dns-records", selectedZoneId],
      })
    },
    onError: (error) => {
      toast.error(getErrorMessage(error))
    },
  })

  const recordMutationPending =
    createRecordMutation.isPending || updateRecordMutation.isPending

  const records = dnsRecordsQuery.data?.items ?? []
  const zones = zonesQuery.data?.items ?? []
  const totalZones = zonesQuery.data?.page_info.total_count ?? zones.length
  const totalRecords =
    dnsRecordsQuery.data?.page_info.total_count ?? records.length

  function openCreateRecord() {
    setRecordEditorMode("create")
    setEditingRecord(null)
    setRecordForm(createDefaultRecordForm(selectedZone?.name ?? ""))
    setRecordSheetOpen(true)
  }

  function openEditRecord(record: CloudflareDNSRecordPublic) {
    setRecordEditorMode("edit")
    setEditingRecord(record)
    setRecordForm({
      type: record.type,
      name: record.name,
      content: record.content,
      ttl: record.ttl,
      proxied: record.proxied ?? false,
      comment: record.comment ?? "",
      priority: record.priority?.toString() ?? "",
    })
    setRecordSheetOpen(true)
  }

  function buildRecordPayload(): CloudflareDNSRecordMutation | null {
    const type = recordForm.type.trim().toUpperCase()
    const name = recordForm.name.trim()
    const content = recordForm.content.trim()

    if (!selectedZoneId) {
      toast.error("請先選擇一個 Zone")
      return null
    }
    if (!type || !name || !content) {
      toast.error("請完整填寫 record type、name 與 content")
      return null
    }

    if (PRIORITY_TYPES.has(type) && recordForm.priority.trim() === "") {
      toast.error(`${type} record 需要 priority`)
      return null
    }

    const payload: CloudflareDNSRecordMutation = {
      type,
      name,
      content,
      ttl: Number.isFinite(recordForm.ttl) ? recordForm.ttl : 1,
    }

    if (PROXIABLE_TYPES.has(type)) {
      payload.proxied = recordForm.proxied
    }

    if (recordForm.comment.trim()) {
      payload.comment = recordForm.comment.trim()
    }

    if (PRIORITY_TYPES.has(type) && recordForm.priority.trim()) {
      payload.priority = Number.parseInt(recordForm.priority, 10)
    }

    return payload
  }

  function handleSaveRecord() {
    const payload = buildRecordPayload()
    if (!payload) return

    if (recordEditorMode === "create") {
      createRecordMutation.mutate(payload)
      return
    }

    if (!editingRecord) {
      toast.error("找不到要編輯的 record")
      return
    }

    updateRecordMutation.mutate({ recordId: editingRecord.id, payload })
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-2">
          <Badge
            variant="outline"
            className="border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300"
          >
            Admin Only
          </Badge>
          <div>
            <h1 className="text-3xl font-semibold tracking-tight">網域管理</h1>
            <p className="max-w-2xl text-sm text-muted-foreground sm:text-base">
              用同一個工作台完成 Cloudflare 供應商連線、Zone 檢視，以及 DNS
              record 的新增、調整與刪除。
            </p>
          </div>
        </div>
        <Button asChild variant="outline" className="gap-2">
          <a
            href="https://dash.cloudflare.com/"
            target="_blank"
            rel="noreferrer"
          >
            <ExternalLink className="h-4 w-4" />
            開啟 Cloudflare Dashboard
          </a>
        </Button>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <MetricCard
          icon={Cloud}
          title="Provider"
          value={isConfigured ? "已連線" : "未設定"}
          description={
            configQuery.data?.last_verified_at
              ? `最後驗證：${formatDate(configQuery.data.last_verified_at)}`
              : "儲存 API Token 後即可測試 Cloudflare 連線"
          }
        />
        <MetricCard
          icon={ShieldCheck}
          title="Zones"
          value={totalZones.toString()}
          description={
            isConfigured
              ? "可直接挑選 Zone 進入 DNS 工作流"
              : "完成供應商設定後會自動載入"
          }
        />
        <MetricCard
          icon={Activity}
          title="Records"
          value={selectedZone ? totalRecords.toString() : "-"}
          description={
            selectedZone
              ? `目前選擇：${selectedZone.name}`
              : "先從左側選一個 Zone 再管理 DNS records"
          }
        />
      </div>

      <div className="grid gap-6 xl:grid-cols-[360px_minmax(0,1fr)]">
        <div className="space-y-6">
          <Card className="border-border/60 shadow-sm">
            <CardHeader className="space-y-2">
              <CardTitle className="flex items-center gap-2 text-lg">
                <Cloud className="h-5 w-5" />
                Cloudflare 供應商設定
              </CardTitle>
              <CardDescription>
                建議使用 API Token。若只想管理 DNS，至少需要 Zone:Read 與
                DNS:Edit 權限；若要新增 Zone，再補上 Zone:Edit。
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <Alert>
                {isConfigured ? (
                  <CheckCircle2 className="h-4 w-4" />
                ) : (
                  <AlertTriangle className="h-4 w-4" />
                )}
                <AlertTitle>
                  {isConfigured ? "設定已存在" : "尚未完成設定"}
                </AlertTitle>
                <AlertDescription>
                  {isConfigured
                    ? "API Token 會以加密方式儲存。若欄位留空，系統會保留目前的 Token。"
                    : "完成設定後，Zones 與 DNS records 區塊才會啟用。"}
                </AlertDescription>
              </Alert>

              <div className="space-y-2">
                <Label htmlFor="cloudflare-account-id">
                  Cloudflare account_id
                </Label>
                <Input
                  id="cloudflare-account-id"
                  value={accountIdInput}
                  onChange={(event) => setAccountIdInput(event.target.value)}
                  placeholder="可留空；建立 Zone 時也能單次覆蓋"
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="cloudflare-api-token">API Token</Label>
                <Input
                  id="cloudflare-api-token"
                  type="password"
                  value={apiTokenInput}
                  onChange={(event) => setApiTokenInput(event.target.value)}
                  placeholder={
                    configQuery.data?.has_api_token
                      ? "留空則保留既有 Token"
                      : "貼上 Cloudflare API Token"
                  }
                />
              </div>

              <div className="rounded-2xl border border-border/60 bg-muted/20 p-4">
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <p className="text-sm font-medium">反向代理預設 DNS 指向</p>
                    <Badge variant="outline">
                      {configQuery.data?.has_default_dns_target
                        ? "已設定"
                        : "未設定"}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    當使用者在反向代理頁建立網址時，系統會自動在 Cloudflare 建立
                    A 或 CNAME record，並指向這裡設定的目標。
                  </p>
                </div>

                <div className="mt-4 grid gap-3 sm:grid-cols-[140px_minmax(0,1fr)]">
                  <div className="space-y-2">
                    <Label>類型</Label>
                    <Select
                      value={defaultDnsTargetType}
                      onValueChange={setDefaultDnsTargetType}
                    >
                      <SelectTrigger>
                        <SelectValue placeholder="選擇類型" />
                      </SelectTrigger>
                      <SelectContent>
                        {DEFAULT_DNS_TARGET_TYPES.map((type) => (
                          <SelectItem key={type} value={type}>
                            {type}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="default-dns-target-value">目標</Label>
                    <Input
                      id="default-dns-target-value"
                      value={defaultDnsTargetValue}
                      onChange={(event) =>
                        setDefaultDnsTargetValue(event.target.value)
                      }
                      placeholder={
                        defaultDnsTargetType === "A"
                          ? "203.0.113.10"
                          : "gateway.example.com"
                      }
                    />
                  </div>
                </div>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <Button
                  onClick={() => saveConfigMutation.mutate()}
                  disabled={saveConfigMutation.isPending}
                  className="gap-2"
                >
                  {saveConfigMutation.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <ShieldCheck className="h-4 w-4" />
                  )}
                  儲存設定
                </Button>
                <Button
                  variant="outline"
                  onClick={() => testConfigMutation.mutate()}
                  disabled={testConfigMutation.isPending || !isConfigured}
                  className="gap-2"
                >
                  {testConfigMutation.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : testConfigMutation.data?.success ? (
                    <Wifi className="h-4 w-4 text-emerald-500" />
                  ) : isConfigured ? (
                    <WifiOff className="h-4 w-4 text-amber-500" />
                  ) : (
                    <Activity className="h-4 w-4" />
                  )}
                  測試連線
                </Button>
              </div>
            </CardContent>
          </Card>

          <Card className="border-border/60 shadow-sm">
            <CardHeader className="space-y-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <CardTitle className="text-lg">Zone 清單</CardTitle>
                  <CardDescription>
                    先選擇要操作的網域，右側會切換到該 Zone 的 DNS 工作區。
                  </CardDescription>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() =>
                    queryClient.invalidateQueries({
                      queryKey: ["cloudflare-zones"],
                    })
                  }
                  disabled={!isConfigured || zonesQuery.isFetching}
                  className="gap-2"
                >
                  <RefreshCw
                    className={cn(
                      "h-4 w-4",
                      zonesQuery.isFetching && "animate-spin",
                    )}
                  />
                  重新整理
                </Button>
              </div>

              <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_120px]">
                <div className="relative">
                  <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    value={zoneSearchInput}
                    onChange={(event) => setZoneSearchInput(event.target.value)}
                    placeholder="搜尋 Zone 名稱"
                    className="pl-9"
                    disabled={!isConfigured}
                  />
                </div>
                <Select
                  value={zoneStatusFilter}
                  onValueChange={setZoneStatusFilter}
                  disabled={!isConfigured}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="全部狀態" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部狀態</SelectItem>
                    <SelectItem value="active">active</SelectItem>
                    <SelectItem value="pending">pending</SelectItem>
                    <SelectItem value="initializing">initializing</SelectItem>
                    <SelectItem value="moved">moved</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </CardHeader>
            <CardContent>
              {!isConfigured ? (
                <EmptyState
                  title="先完成 Cloudflare 設定"
                  description="左上方儲存 API Token 後，系統才會開始抓取可操作的 Zones。"
                />
              ) : zonesQuery.isLoading ? (
                <div className="space-y-3">
                  {ZONE_SKELETON_KEYS.map((key) => (
                    <Skeleton key={key} className="h-20 rounded-2xl" />
                  ))}
                </div>
              ) : zones.length === 0 ? (
                <EmptyState
                  title="目前沒有可用 Zone"
                  description="可以直接建立新的 Zone，或確認 API Token 是否對應到正確的 Cloudflare 帳號。"
                  action={
                    <Button
                      onClick={() => setZoneDialogOpen(true)}
                      className="gap-2"
                    >
                      <Plus className="h-4 w-4" />
                      建立第一個 Zone
                    </Button>
                  }
                />
              ) : (
                <div className="space-y-3">
                  {zones.map((zone) => {
                    const selected = zone.id === selectedZoneId

                    return (
                      <button
                        key={zone.id}
                        type="button"
                        onClick={() => {
                          startTransition(() => {
                            setSelectedZoneId(zone.id)
                          })
                        }}
                        className={cn(
                          "w-full rounded-2xl border px-4 py-3 text-left transition-all",
                          "hover:border-primary/40 hover:bg-accent/30",
                          selected
                            ? "border-primary/50 bg-primary/5 shadow-sm"
                            : "border-border/60 bg-card/80",
                        )}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0 space-y-1">
                            <div className="flex items-center gap-2">
                              <p className="truncate font-medium">
                                {zone.name}
                              </p>
                              <Badge
                                variant="outline"
                                className={cn(
                                  "capitalize",
                                  getZoneStatusClass(zone.status),
                                )}
                              >
                                {zone.status}
                              </Badge>
                            </div>
                            <p className="truncate text-xs text-muted-foreground">
                              {zone.name_servers.length > 0
                                ? zone.name_servers.join(" / ")
                                : "尚未取得 nameservers"}
                            </p>
                          </div>
                          <div className="text-right text-xs text-muted-foreground">
                            <p>{zone.paused ? "Paused" : "Live"}</p>
                            <p>{formatDate(zone.modified_on)}</p>
                          </div>
                        </div>
                      </button>
                    )
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        <Card className="border-border/60 shadow-sm">
          <CardHeader className="gap-4 border-b border-border/60 pb-5">
            <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
              <div className="space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <CardTitle className="text-xl">
                    {selectedZone ? selectedZone.name : "選擇一個 Zone"}
                  </CardTitle>
                  {selectedZone && (
                    <Badge
                      variant="outline"
                      className={cn(
                        "capitalize",
                        getZoneStatusClass(selectedZone.status),
                      )}
                    >
                      {selectedZone.status}
                    </Badge>
                  )}
                </div>
                <CardDescription>
                  {selectedZone
                    ? "DNS records 預設以操作效率優先，建立與編輯都在同一個側邊表單完成。"
                    : "先從左側選擇一個 Zone，這裡才會顯示對應的 DNS 工作區。"}
                </CardDescription>
              </div>

              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  onClick={() => setZoneDialogOpen(true)}
                  className="gap-2"
                  disabled={!isConfigured}
                >
                  <Plus className="h-4 w-4" />
                  新增 Zone
                </Button>
                <Button
                  onClick={openCreateRecord}
                  disabled={!selectedZoneId}
                  className="gap-2"
                >
                  <Plus className="h-4 w-4" />
                  新增 DNS Record
                </Button>
              </div>
            </div>
          </CardHeader>

          <CardContent className="pt-6">
            {!selectedZone ? (
              <EmptyState
                title="尚未選擇 Zone"
                description="左側清單支援搜尋與狀態篩選，選定後右側會切成 Zone 概覽與 DNS records 兩個工作頁籤。"
              />
            ) : (
              <Tabs defaultValue="records" className="space-y-5">
                <TabsList>
                  <TabsTrigger value="records">DNS Records</TabsTrigger>
                  <TabsTrigger value="overview">Zone Overview</TabsTrigger>
                </TabsList>

                <TabsContent value="records" className="space-y-5">
                  <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_180px_auto]">
                    <div className="relative">
                      <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                      <Input
                        value={recordSearchInput}
                        onChange={(event) =>
                          setRecordSearchInput(event.target.value)
                        }
                        placeholder="搜尋 record name"
                        className="pl-9"
                      />
                    </div>
                    <Select
                      value={recordTypeFilter}
                      onValueChange={setRecordTypeFilter}
                    >
                      <SelectTrigger>
                        <SelectValue placeholder="全部類型" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">全部類型</SelectItem>
                        {RECORD_TYPES.map((type) => (
                          <SelectItem key={type} value={type}>
                            {type}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Button
                      variant="outline"
                      onClick={() =>
                        queryClient.invalidateQueries({
                          queryKey: ["cloudflare-dns-records", selectedZoneId],
                        })
                      }
                      disabled={dnsRecordsQuery.isFetching}
                      className="gap-2"
                    >
                      <RefreshCw
                        className={cn(
                          "h-4 w-4",
                          dnsRecordsQuery.isFetching && "animate-spin",
                        )}
                      />
                      更新
                    </Button>
                  </div>

                  {dnsRecordsQuery.isLoading ? (
                    <div className="space-y-3">
                      {RECORD_SKELETON_KEYS.map((key) => (
                        <Skeleton key={key} className="h-12 rounded-xl" />
                      ))}
                    </div>
                  ) : records.length === 0 ? (
                    <EmptyState
                      title="這個 Zone 目前沒有符合條件的 records"
                      description="可以建立新的 A、CNAME、TXT、MX 等 DNS records，或調整上方篩選條件。"
                      action={
                        <Button onClick={openCreateRecord} className="gap-2">
                          <Plus className="h-4 w-4" />
                          新增第一筆 Record
                        </Button>
                      }
                    />
                  ) : (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Type</TableHead>
                          <TableHead>Name</TableHead>
                          <TableHead>Content</TableHead>
                          <TableHead>TTL</TableHead>
                          <TableHead>Proxy</TableHead>
                          <TableHead>Updated</TableHead>
                          <TableHead className="text-right">Actions</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {records.map((record) => (
                          <TableRow key={record.id}>
                            <TableCell>
                              <Badge variant="outline">{record.type}</Badge>
                            </TableCell>
                            <TableCell className="max-w-[16rem] truncate font-medium">
                              {record.name}
                            </TableCell>
                            <TableCell className="max-w-88 truncate text-muted-foreground">
                              {record.content}
                            </TableCell>
                            <TableCell>
                              {record.ttl === 1 ? "Auto" : record.ttl}
                            </TableCell>
                            <TableCell>
                              <Badge
                                variant="outline"
                                className={cn(
                                  record.proxied
                                    ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                                    : "border-border/70 bg-muted/30 text-muted-foreground",
                                )}
                              >
                                {record.proxied
                                  ? "Proxied"
                                  : record.proxiable
                                    ? "DNS only"
                                    : "N/A"}
                              </Badge>
                            </TableCell>
                            <TableCell className="text-muted-foreground">
                              {formatDate(record.modified_on)}
                            </TableCell>
                            <TableCell>
                              <div className="flex justify-end gap-2">
                                <Button
                                  size="icon-sm"
                                  variant="outline"
                                  onClick={() => openEditRecord(record)}
                                  title="編輯 DNS record"
                                >
                                  <Pencil className="h-4 w-4" />
                                </Button>
                                <Button
                                  size="icon-sm"
                                  variant="outline"
                                  onClick={() => setRecordPendingDelete(record)}
                                  title="刪除 DNS record"
                                >
                                  <Trash2 className="h-4 w-4" />
                                </Button>
                              </div>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  )}
                </TabsContent>

                <TabsContent value="overview" className="space-y-5">
                  {selectedZone.status !== "active" && (
                    <Alert>
                      <AlertTriangle className="h-4 w-4" />
                      <AlertTitle>Zone 尚未完全啟用</AlertTitle>
                      <AlertDescription>
                        這個 Zone 目前狀態為 {selectedZone.status}。請確認網域的
                        nameserver 是否已改指向 Cloudflare，並等待 Cloudflare
                        完成偵測與接管。
                      </AlertDescription>
                    </Alert>
                  )}

                  <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                    <Card className="border-border/60">
                      <CardHeader className="pb-3">
                        <CardDescription>Zone ID</CardDescription>
                        <CardTitle className="break-all text-base font-medium">
                          {selectedZone.id}
                        </CardTitle>
                      </CardHeader>
                    </Card>
                    <Card className="border-border/60">
                      <CardHeader className="pb-3">
                        <CardDescription>建立時間</CardDescription>
                        <CardTitle className="text-base font-medium">
                          {formatDate(selectedZone.created_on)}
                        </CardTitle>
                      </CardHeader>
                    </Card>
                    <Card className="border-border/60">
                      <CardHeader className="pb-3">
                        <CardDescription>最後更新</CardDescription>
                        <CardTitle className="text-base font-medium">
                          {formatDate(selectedZone.modified_on)}
                        </CardTitle>
                      </CardHeader>
                    </Card>
                  </div>

                  <Card className="border-border/60">
                    <CardHeader>
                      <CardTitle className="text-lg">Nameservers</CardTitle>
                      <CardDescription>
                        將你的網域註冊商 nameservers 指向這裡，Cloudflare
                        才能完成接管。
                      </CardDescription>
                    </CardHeader>
                    <CardContent>
                      {selectedZone.name_servers.length === 0 ? (
                        <p className="text-sm text-muted-foreground">
                          Cloudflare 尚未回傳 nameservers。
                        </p>
                      ) : (
                        <div className="flex flex-wrap gap-2">
                          {selectedZone.name_servers.map((nameServer) => (
                            <Badge
                              key={nameServer}
                              variant="outline"
                              className="px-3 py-1 text-sm"
                            >
                              {nameServer}
                            </Badge>
                          ))}
                        </div>
                      )}
                    </CardContent>
                  </Card>
                </TabsContent>
              </Tabs>
            )}
          </CardContent>
        </Card>
      </div>

      <Dialog open={zoneDialogOpen} onOpenChange={setZoneDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>建立新的 Cloudflare Zone</DialogTitle>
            <DialogDescription>
              支援先建立 Zone，再讓 Cloudflare 自動匯入既有 DNS
              records。若你已在供應商設定存好 account_id，這裡會自動帶入。
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="zone-name">Zone 名稱</Label>
              <Input
                id="zone-name"
                value={zoneNameInput}
                onChange={(event) => setZoneNameInput(event.target.value)}
                placeholder="example.com"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="zone-account-id">account_id</Label>
              <Input
                id="zone-account-id"
                value={zoneAccountIdInput}
                onChange={(event) => setZoneAccountIdInput(event.target.value)}
                placeholder="建立 Zone 需要 account_id"
              />
            </div>

            <div className="flex items-center justify-between rounded-xl border border-border/60 bg-muted/20 px-4 py-3">
              <div className="space-y-1">
                <p className="text-sm font-medium">Jump Start</p>
                <p className="text-xs text-muted-foreground">
                  讓 Cloudflare 嘗試掃描既有 DNS records，通常適合從其他 DNS
                  供應商遷移時使用。
                </p>
              </div>
              <Switch
                checked={zoneJumpStart}
                onCheckedChange={setZoneJumpStart}
              />
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setZoneDialogOpen(false)}>
              取消
            </Button>
            <Button
              onClick={() => createZoneMutation.mutate()}
              disabled={createZoneMutation.isPending || !zoneNameInput.trim()}
              className="gap-2"
            >
              {createZoneMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Plus className="h-4 w-4" />
              )}
              建立 Zone
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Sheet open={recordSheetOpen} onOpenChange={setRecordSheetOpen}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
          <SheetHeader>
            <SheetTitle>
              {recordEditorMode === "create"
                ? "新增 DNS Record"
                : "編輯 DNS Record"}
            </SheetTitle>
            <SheetDescription>
              {selectedZone
                ? `目前操作的 Zone：${selectedZone.name}`
                : "請先選擇一個 Zone"}
            </SheetDescription>
          </SheetHeader>

          <div className="space-y-5 px-4 pb-6">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label>Record Type</Label>
                <Select
                  value={recordForm.type}
                  onValueChange={(value) =>
                    setRecordForm((current) => ({ ...current, type: value }))
                  }
                >
                  <SelectTrigger>
                    <SelectValue placeholder="選擇 Record Type" />
                  </SelectTrigger>
                  <SelectContent>
                    {RECORD_TYPES.map((type) => (
                      <SelectItem key={type} value={type}>
                        {type}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label>TTL</Label>
                <Input
                  type="number"
                  min={1}
                  max={86400}
                  value={recordForm.ttl}
                  onChange={(event) =>
                    setRecordForm((current) => ({
                      ...current,
                      ttl: Number.parseInt(event.target.value, 10) || 1,
                    }))
                  }
                />
              </div>
            </div>

            <div className="space-y-2">
              <Label>Name</Label>
              <Input
                value={recordForm.name}
                onChange={(event) =>
                  setRecordForm((current) => ({
                    ...current,
                    name: event.target.value,
                  }))
                }
                placeholder="例如 app 或 example.com"
              />
            </div>

            <div className="space-y-2">
              <Label>Content</Label>
              <Textarea
                value={recordForm.content}
                onChange={(event) =>
                  setRecordForm((current) => ({
                    ...current,
                    content: event.target.value,
                  }))
                }
                placeholder="例如 203.0.113.10、target.example.com 或 TXT token"
                rows={4}
              />
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label>Priority</Label>
                <Input
                  type="number"
                  min={0}
                  value={recordForm.priority}
                  onChange={(event) =>
                    setRecordForm((current) => ({
                      ...current,
                      priority: event.target.value,
                    }))
                  }
                  placeholder={
                    PRIORITY_TYPES.has(recordForm.type)
                      ? `${recordForm.type} record 需要 priority`
                      : "此類型通常不需要"
                  }
                  disabled={!PRIORITY_TYPES.has(recordForm.type)}
                />
              </div>

              <div className="space-y-2">
                <Label>Proxy through Cloudflare</Label>
                <div className="flex h-10 items-center justify-between rounded-xl border border-border/60 bg-muted/20 px-4">
                  <div className="text-sm text-muted-foreground">
                    {PROXIABLE_TYPES.has(recordForm.type)
                      ? "A / AAAA / CNAME 才能啟用 proxied"
                      : "目前 record type 不支援 proxied"}
                  </div>
                  <Switch
                    checked={recordForm.proxied}
                    onCheckedChange={(checked) =>
                      setRecordForm((current) => ({
                        ...current,
                        proxied: checked,
                      }))
                    }
                    disabled={!PROXIABLE_TYPES.has(recordForm.type)}
                  />
                </div>
              </div>
            </div>

            <div className="space-y-2">
              <Label>Comment</Label>
              <Textarea
                value={recordForm.comment}
                onChange={(event) =>
                  setRecordForm((current) => ({
                    ...current,
                    comment: event.target.value,
                  }))
                }
                placeholder="可選，用來註記用途或負責人"
                rows={3}
              />
            </div>
          </div>

          <SheetFooter>
            <Button variant="outline" onClick={() => setRecordSheetOpen(false)}>
              取消
            </Button>
            <Button
              onClick={handleSaveRecord}
              disabled={recordMutationPending}
              className="gap-2"
            >
              {recordMutationPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : recordEditorMode === "create" ? (
                <Plus className="h-4 w-4" />
              ) : (
                <Pencil className="h-4 w-4" />
              )}
              {recordEditorMode === "create" ? "建立 Record" : "儲存變更"}
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>

      <AlertDialog
        open={recordPendingDelete !== null}
        onOpenChange={(open) => {
          if (!open) {
            setRecordPendingDelete(null)
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>刪除 DNS Record</AlertDialogTitle>
            <AlertDialogDescription>
              {recordPendingDelete
                ? `確定要刪除 ${recordPendingDelete.type} ${recordPendingDelete.name} 嗎？這個動作無法復原。`
                : ""}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (recordPendingDelete) {
                  deleteRecordMutation.mutate(recordPendingDelete)
                }
              }}
              className="bg-destructive text-white hover:bg-destructive/90"
            >
              {deleteRecordMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Trash2 className="h-4 w-4" />
              )}
              刪除 Record
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
