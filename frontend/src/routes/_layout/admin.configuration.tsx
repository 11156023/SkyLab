import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import {
  AlertTriangle,
  CheckCircle,
  ChevronRight,
  Cpu,
  Database,
  Edit2,
  HardDrive,
  Layers,
  Lock,
  MemoryStick,
  RefreshCw,
  Save,
  Server,
  Settings2,
  ShieldCheck,
  ShieldOff,
  Trash2,
  User,
  Wifi,
  WifiOff,
  XCircle,
} from "lucide-react"
import { useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import { toast } from "sonner"
import { OpenAPI } from "@/client/core/OpenAPI"
import { request as __request } from "@/client/core/request"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { LoadingButton } from "@/components/ui/loading-button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Slider } from "@/components/ui/slider"
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
import { requireAdminUser } from "@/features/auth/guards"
import { cn } from "@/lib/utils"

// ── Types ─────────────────────────────────────────────────────────────────────

interface ProxmoxConfigPublic {
  host: string
  user: string
  verify_ssl: boolean
  iso_storage: string
  data_storage: string
  api_timeout: number
  task_check_interval: number
  pool_name: string
  gateway_ip: string | null
  local_subnet: string | null
  default_node: string | null
  placement_strategy: string
  cpu_overcommit_ratio: number
  disk_overcommit_ratio: number
  migration_enabled: boolean
  migration_max_per_rebalance: number
  migration_min_interval_minutes: number
  migration_retry_limit: number
  rebalance_migration_cost: number
  rebalance_peak_cpu_margin: number
  rebalance_peak_memory_margin: number
  rebalance_loadavg_warn_per_core: number
  rebalance_loadavg_max_per_core: number
  rebalance_loadavg_penalty_weight: number
  rebalance_cpu_peak_warn_share: number
  rebalance_cpu_peak_high_share: number
  rebalance_memory_peak_warn_share: number
  rebalance_memory_peak_high_share: number
  rebalance_resource_weight_cpu: number
  rebalance_resource_weight_memory: number
  rebalance_resource_weight_disk: number
  migration_lxc_live_enabled: boolean
  scheduled_boot_batch_size: number
  scheduled_boot_batch_interval_seconds: number
  scheduled_boot_lead_time_minutes: number
  window_grace_period_minutes: number
  practice_session_hours: number
  practice_warning_minutes: number
  updated_at: string | null
  is_configured: boolean
  has_ca_cert: boolean
  ca_fingerprint: string | null
}

interface ProxmoxConfigUpdate {
  host: string
  user: string
  password?: string | null
  verify_ssl: boolean
  iso_storage: string
  data_storage: string
  api_timeout: number
  task_check_interval: number
  pool_name: string
  ca_cert?: string | null
  gateway_ip?: string | null
  local_subnet?: string | null
  default_node?: string | null
  placement_strategy: string
  cpu_overcommit_ratio: number
  disk_overcommit_ratio: number
  migration_enabled: boolean
  migration_max_per_rebalance: number
  migration_min_interval_minutes: number
  migration_retry_limit: number
  rebalance_migration_cost: number
  rebalance_peak_cpu_margin: number
  rebalance_peak_memory_margin: number
  rebalance_loadavg_warn_per_core: number
  rebalance_loadavg_max_per_core: number
  rebalance_loadavg_penalty_weight: number
  rebalance_cpu_peak_warn_share: number
  rebalance_cpu_peak_high_share: number
  rebalance_memory_peak_warn_share: number
  rebalance_memory_peak_high_share: number
  rebalance_resource_weight_cpu: number
  rebalance_resource_weight_memory: number
  rebalance_resource_weight_disk: number
  migration_lxc_live_enabled: boolean
  scheduled_boot_batch_size: number
  scheduled_boot_batch_interval_seconds: number
  scheduled_boot_lead_time_minutes: number
  window_grace_period_minutes: number
  practice_session_hours: number
  practice_warning_minutes: number
}

interface ProxmoxNodePublic {
  id?: number | null
  name: string
  host: string
  port: number
  is_primary: boolean
  is_online: boolean
  last_checked?: string | null
  priority: number
}

interface ProxmoxNodeUpdate {
  host: string
  port: number
  priority: number
}

interface ClusterPreviewResult {
  success: boolean
  is_cluster: boolean
  nodes: ProxmoxNodePublic[]
  error?: string | null
}

interface ProxmoxConnectionTestResult {
  success: boolean
  message: string
}

interface CertParseResult {
  valid: boolean
  fingerprint: string | null
  subject: string | null
  issuer: string | null
  not_before: string | null
  not_after: string | null
  error: string | null
}

interface ProxmoxStoragePublic {
  id: number
  node_name: string
  storage: string
  storage_type: string | null
  total_gb: number
  used_gb: number
  avail_gb: number
  can_vm: boolean
  can_lxc: boolean
  can_iso: boolean
  can_backup: boolean
  is_shared: boolean
  active: boolean
  enabled: boolean
  speed_tier: string
  user_priority: number
}

interface SyncNowResult {
  success: boolean
  nodes: ProxmoxNodePublic[]
  storage_count: number
  error?: string | null
}

interface NodeStatsPublic {
  name: string
  status: string
  cpu_usage_pct: number
  cpu_cores: number
  mem_used_gb: number
  mem_total_gb: number
  disk_used_gb: number
  disk_total_gb: number
  vm_count: number
}

interface ClusterStatsPublic {
  nodes: NodeStatsPublic[]
  total_cpu_cores: number
  used_cpu_cores: number
  total_mem_gb: number
  used_mem_gb: number
  total_disk_gb: number
  used_disk_gb: number
  online_count: number
  offline_count: number
  total_vm_count: number
}

// ── API Service ────────────────────────────────────────────────────────────────

const ProxmoxConfigService = {
  getConfig: (): Promise<ProxmoxConfigPublic> =>
    __request(OpenAPI, { method: "GET", url: "/api/v1/proxmox-config/" }),

  updateConfig: (body: ProxmoxConfigUpdate): Promise<ProxmoxConfigPublic> =>
    __request(OpenAPI, {
      method: "PUT",
      url: "/api/v1/proxmox-config/",
      body,
      mediaType: "application/json",
    }),

  previewCluster: (body: ProxmoxConfigUpdate): Promise<ClusterPreviewResult> =>
    __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/proxmox-config/preview",
      body,
      mediaType: "application/json",
    }),

  getNodes: (): Promise<ProxmoxNodePublic[]> =>
    __request(OpenAPI, { method: "GET", url: "/api/v1/proxmox-config/nodes" }),

  updateNode: (
    nodeId: number,
    body: ProxmoxNodeUpdate,
  ): Promise<ProxmoxNodePublic> =>
    __request(OpenAPI, {
      method: "PUT",
      url: `/api/v1/proxmox-config/nodes/${nodeId}`,
      body,
      mediaType: "application/json",
    }),

  syncNow: (): Promise<SyncNowResult> =>
    __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/proxmox-config/sync-now",
    }),

  testConnection: (): Promise<ProxmoxConnectionTestResult> =>
    __request(OpenAPI, { method: "POST", url: "/api/v1/proxmox-config/test" }),

  parseCert: (pem: string): Promise<CertParseResult> =>
    __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/proxmox-config/parse-cert",
      body: { pem },
      mediaType: "application/json",
    }),

  getStorages: (): Promise<ProxmoxStoragePublic[]> =>
    __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/proxmox-config/storages",
    }),

  updateStorage: (
    storageId: number,
    body: { enabled: boolean; speed_tier: string; user_priority: number },
  ): Promise<ProxmoxStoragePublic> =>
    __request(OpenAPI, {
      method: "PUT",
      url: `/api/v1/proxmox-config/storages/${storageId}`,
      body,
      mediaType: "application/json",
    }),

  getClusterStats: (): Promise<ClusterStatsPublic> =>
    __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/proxmox-config/cluster-stats",
    }),
}

// ── Route ─────────────────────────────────────────────────────────────────────

export const Route = createFileRoute("/_layout/admin/configuration")({
  component: AdminConfigPage,
  beforeLoad: () => requireAdminUser(),
  head: () => ({
    meta: [{ title: "系統設定 - Campus Cloud" }],
  }),
})

// ── Form types ────────────────────────────────────────────────────────────────

interface ConfigFormData {
  host: string
  user: string
  password: string
  verify_ssl: boolean
  iso_storage: string
  data_storage: string
  api_timeout: number
  task_check_interval: number
  pool_name: string
  gateway_ip: string
  local_subnet: string
  default_node: string
  placement_strategy: string
  cpu_overcommit_ratio: number
  disk_overcommit_ratio: number
  migration_enabled: boolean
  migration_max_per_rebalance: number
  migration_min_interval_minutes: number
  migration_retry_limit: number
  rebalance_migration_cost: number
  rebalance_peak_cpu_margin: number
  rebalance_peak_memory_margin: number
  rebalance_loadavg_warn_per_core: number
  rebalance_loadavg_max_per_core: number
  rebalance_loadavg_penalty_weight: number
  rebalance_cpu_peak_warn_share: number
  rebalance_cpu_peak_high_share: number
  rebalance_memory_peak_warn_share: number
  rebalance_memory_peak_high_share: number
  rebalance_resource_weight_cpu: number
  rebalance_resource_weight_memory: number
  rebalance_resource_weight_disk: number
  migration_lxc_live_enabled: boolean
  scheduled_boot_batch_size: number
  scheduled_boot_batch_interval_seconds: number
  scheduled_boot_lead_time_minutes: number
  window_grace_period_minutes: number
  practice_session_hours: number
  practice_warning_minutes: number
}

interface NodeFormData {
  host: string
  port: number
  priority: number
}

// ── StorageTab ────────────────────────────────────────────────────────────────

function StorageTab() {
  const queryClient = useQueryClient()

  const { data: storages, isLoading } = useQuery({
    queryKey: ["proxmoxStorages"],
    queryFn: ProxmoxConfigService.getStorages,
  })

  const updateMutation = useMutation({
    mutationFn: ({
      id,
      body,
    }: {
      id: number
      body: { enabled: boolean; speed_tier: string; user_priority: number }
    }) => ProxmoxConfigService.updateStorage(id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["proxmoxStorages"] })
    },
    onError: () => toast.error("Storage 更新失敗"),
  })

  const sharedByName = storages
    ? storages
        .filter((s) => s.is_shared)
        .reduce<Record<string, ProxmoxStoragePublic[]>>((acc, s) => {
          acc[s.storage] ??= []
          acc[s.storage].push(s)
          return acc
        }, {})
    : {}

  const displayStorages: ProxmoxStoragePublic[] = storages
    ? [
        ...Object.values(sharedByName).map((group) => group[0]),
        ...storages.filter((s) => !s.is_shared),
      ].sort((a, b) => a.storage.localeCompare(b.storage))
    : []

  const updateStorage = (
    s: ProxmoxStoragePublic,
    body: { enabled: boolean; speed_tier: string; user_priority: number },
  ) => {
    if (s.is_shared) {
      const group = sharedByName[s.storage] ?? []
      group.forEach((entry) => {
        updateMutation.mutate({ id: entry.id, body })
      })
    } else {
      updateMutation.mutate({ id: s.id, body })
    }
  }

  const speedTierLabel: Record<string, string> = {
    nvme: "NVMe",
    ssd: "SSD",
    hdd: "HDD",
    unknown: "未知",
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12 text-muted-foreground">
        <RefreshCw className="mr-2 h-4 w-4 animate-spin" />
        載入中...
      </div>
    )
  }

  if (!storages || storages.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 gap-2 text-muted-foreground">
        <Database className="h-8 w-8" />
        <p>尚無 Storage 資料，請先在「節點管理」點擊「同步節點」。</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center">
        <Badge variant="outline" className="ml-auto">
          {displayStorages.length} 個 Storage
        </Badge>
      </div>
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Storage 名稱</TableHead>
              <TableHead>節點</TableHead>
              <TableHead>類型</TableHead>
              <TableHead>容量</TableHead>
              <TableHead>用途</TableHead>
              <TableHead>速度分級</TableHead>
              <TableHead className="w-24">優先級</TableHead>
              <TableHead className="w-16">啟用</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {displayStorages.map((s) => (
              <TableRow
                key={
                  s.is_shared
                    ? `shared-${s.storage}`
                    : `${s.node_name}-${s.storage}`
                }
                className={!s.enabled ? "opacity-50" : ""}
              >
                <TableCell className="font-medium">
                  {s.storage}
                  {s.is_shared && (
                    <Badge variant="outline" className="ml-2 text-xs">
                      共享
                    </Badge>
                  )}
                </TableCell>
                <TableCell className="text-muted-foreground text-sm">
                  {s.is_shared ? "—" : s.node_name}
                </TableCell>
                <TableCell className="text-muted-foreground text-sm">
                  {s.storage_type ?? "-"}
                </TableCell>
                <TableCell className="text-sm">
                  <span>{s.avail_gb.toFixed(1)} GB</span>
                  <span className="text-muted-foreground">
                    {" "}
                    / {s.total_gb.toFixed(1)} GB
                  </span>
                </TableCell>
                <TableCell>
                  <div className="flex gap-1 flex-wrap">
                    {s.can_vm && (
                      <Badge variant="secondary" className="text-xs">
                        VM
                      </Badge>
                    )}
                    {s.can_lxc && (
                      <Badge variant="secondary" className="text-xs">
                        LXC
                      </Badge>
                    )}
                    {s.can_iso && (
                      <Badge variant="secondary" className="text-xs">
                        ISO
                      </Badge>
                    )}
                    {s.can_backup && (
                      <Badge variant="secondary" className="text-xs">
                        備份
                      </Badge>
                    )}
                  </div>
                </TableCell>
                <TableCell>
                  <Select
                    value={s.speed_tier}
                    onValueChange={(val) =>
                      updateStorage(s, {
                        enabled: s.enabled,
                        speed_tier: val,
                        user_priority: s.user_priority,
                      })
                    }
                  >
                    <SelectTrigger className="h-7 w-24 text-xs">
                      <SelectValue>
                        {speedTierLabel[s.speed_tier] ?? s.speed_tier}
                      </SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="nvme">NVMe</SelectItem>
                      <SelectItem value="ssd">SSD</SelectItem>
                      <SelectItem value="hdd">HDD</SelectItem>
                      <SelectItem value="unknown">未知</SelectItem>
                    </SelectContent>
                  </Select>
                </TableCell>
                <TableCell>
                  <Input
                    type="number"
                    min={1}
                    max={10}
                    className="h-7 w-16 text-xs"
                    defaultValue={s.user_priority}
                    onBlur={(e) => {
                      const val = Math.min(
                        10,
                        Math.max(1, Number(e.target.value)),
                      )
                      if (val !== s.user_priority) {
                        updateStorage(s, {
                          enabled: s.enabled,
                          speed_tier: s.speed_tier,
                          user_priority: val,
                        })
                      }
                    }}
                  />
                </TableCell>
                <TableCell>
                  <Switch
                    checked={s.enabled}
                    onCheckedChange={(checked) =>
                      updateStorage(s, {
                        enabled: checked,
                        speed_tier: s.speed_tier,
                        user_priority: s.user_priority,
                      })
                    }
                  />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}

// ── ClusterOverviewTab ────────────────────────────────────────────────────────

const CHART_COLORS = {
  cpu: "#3b82f6",
  mem: "#10b981",
  disk: "#f59e0b",
  free: "hsl(var(--muted))",
}

function UsageRingChart({
  label,
  used,
  total,
  unit,
  color,
  icon: Icon,
}: {
  label: string
  used: number
  total: number
  unit: string
  color: string
  icon: React.ElementType
}) {
  const pct = total > 0 ? Math.round((used / total) * 100) : 0
  const data = [
    { name: "使用中", value: used },
    { name: "閒置", value: Math.max(total - used, 0) },
  ]

  return (
    <div className="flex flex-col items-center gap-2">
      <div className="relative">
        <ResponsiveContainer width={140} height={140}>
          <PieChart>
            <Pie
              data={data}
              cx="50%"
              cy="50%"
              innerRadius={48}
              outerRadius={64}
              startAngle={90}
              endAngle={-270}
              dataKey="value"
              strokeWidth={0}
            >
              <Cell fill={color} />
              <Cell fill={CHART_COLORS.free} />
            </Pie>
          </PieChart>
        </ResponsiveContainer>
        <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
          <Icon className="h-4 w-4 text-muted-foreground mb-0.5" />
          <span className="text-xl font-bold leading-none">{pct}%</span>
        </div>
      </div>
      <div className="text-center">
        <p className="text-sm font-medium">{label}</p>
        <p className="text-xs text-muted-foreground">
          {used.toFixed(1)} / {total.toFixed(1)} {unit}
        </p>
      </div>
    </div>
  )
}

type NodeSortKey = "name" | "status" | "cpu" | "mem" | "disk" | "vm"
type SortDir = "asc" | "desc"

function ClusterOverviewTab() {
  const {
    data: stats,
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ["clusterStats"],
    queryFn: ProxmoxConfigService.getClusterStats,
    refetchInterval: 30_000,
  })

  const [sortKey, setSortKey] = useState<NodeSortKey>("name")
  const [sortDir, setSortDir] = useState<SortDir>("asc")

  function toggleSort(key: NodeSortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"))
    } else {
      setSortKey(key)
      setSortDir("asc")
    }
  }

  const sortedNodes = [...(stats?.nodes ?? [])].sort((a, b) => {
    const memPctA = a.mem_total_gb > 0 ? a.mem_used_gb / a.mem_total_gb : 0
    const memPctB = b.mem_total_gb > 0 ? b.mem_used_gb / b.mem_total_gb : 0
    const diskPctA = a.disk_total_gb > 0 ? a.disk_used_gb / a.disk_total_gb : 0
    const diskPctB = b.disk_total_gb > 0 ? b.disk_used_gb / b.disk_total_gb : 0
    let cmp = 0
    if (sortKey === "name") cmp = a.name.localeCompare(b.name)
    else if (sortKey === "status") cmp = a.status.localeCompare(b.status)
    else if (sortKey === "cpu") cmp = a.cpu_usage_pct - b.cpu_usage_pct
    else if (sortKey === "mem") cmp = memPctA - memPctB
    else if (sortKey === "disk") cmp = diskPctA - diskPctB
    else if (sortKey === "vm") cmp = a.vm_count - b.vm_count
    return sortDir === "asc" ? cmp : -cmp
  })

  const barData = sortedNodes.map((n) => ({
    name: n.name,
    CPU: n.cpu_usage_pct,
    RAM:
      n.mem_total_gb > 0
        ? Math.round((n.mem_used_gb / n.mem_total_gb) * 100)
        : 0,
    Disk:
      n.disk_total_gb > 0
        ? Math.round((n.disk_used_gb / n.disk_total_gb) * 100)
        : 0,
  }))

  const onlineCount = stats?.online_count ?? 0
  const totalNodes = (stats?.online_count ?? 0) + (stats?.offline_count ?? 0)
  const avgCpuPct =
    stats && stats.nodes.length > 0
      ? Math.round(
          stats.nodes.reduce((a, n) => a + n.cpu_usage_pct, 0) /
            stats.nodes.length,
        )
      : 0

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20 text-muted-foreground gap-2">
        <RefreshCw className="h-4 w-4 animate-spin" />
        載入叢集狀態中...
      </div>
    )
  }

  if (isError) {
    return (
      <div className="flex flex-col items-center justify-center py-20 gap-3 text-muted-foreground">
        <XCircle className="h-8 w-8 text-destructive" />
        <p>無法取得叢集資料，請確認 PVE 連線設定。</p>
        <Button variant="outline" size="sm" onClick={() => refetch()}>
          <RefreshCw className="mr-2 h-3.5 w-3.5" />
          重試
        </Button>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5">
      {/* Summary metric cards */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Card>
          <CardContent className="pt-5">
            <div className="flex items-start justify-between">
              <div>
                <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">
                  節點狀態
                </p>
                <p className="text-3xl font-bold mt-1 leading-none">
                  {onlineCount}
                  <span className="text-lg text-muted-foreground font-normal">
                    /{totalNodes}
                  </span>
                </p>
                <p className="text-xs text-muted-foreground mt-1">節點在線</p>
              </div>
              <div
                className={cn(
                  "rounded-full p-2",
                  onlineCount === totalNodes && totalNodes > 0
                    ? "bg-green-100 dark:bg-green-900/30"
                    : "bg-amber-100 dark:bg-amber-900/30",
                )}
              >
                <Server
                  className={cn(
                    "h-4 w-4",
                    onlineCount === totalNodes && totalNodes > 0
                      ? "text-green-600"
                      : "text-amber-600",
                  )}
                />
              </div>
            </div>
            <div className="mt-3 flex gap-1">
              {stats?.nodes.map((n) => (
                <div
                  key={n.name}
                  title={`${n.name}: ${n.status}`}
                  className={cn(
                    "h-1.5 flex-1 rounded-full",
                    n.status === "online" ? "bg-green-500" : "bg-destructive",
                  )}
                />
              ))}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="pt-5">
            <div className="flex items-start justify-between">
              <div>
                <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">
                  CPU 平均
                </p>
                <p className="text-3xl font-bold mt-1 leading-none">
                  {avgCpuPct}
                  <span className="text-lg text-muted-foreground font-normal">
                    %
                  </span>
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  {stats?.total_cpu_cores ?? 0} 核心總計
                </p>
              </div>
              <div className="rounded-full p-2 bg-blue-100 dark:bg-blue-900/30">
                <Cpu className="h-4 w-4 text-blue-600" />
              </div>
            </div>
            <div className="mt-3 h-1.5 rounded-full bg-muted overflow-hidden">
              <div
                className="h-full rounded-full bg-blue-500 transition-all"
                style={{ width: `${avgCpuPct}%` }}
              />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="pt-5">
            <div className="flex items-start justify-between">
              <div>
                <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">
                  記憶體
                </p>
                <p className="text-3xl font-bold mt-1 leading-none">
                  {stats
                    ? Math.round(
                        (stats.used_mem_gb / (stats.total_mem_gb || 1)) * 100,
                      )
                    : 0}
                  <span className="text-lg text-muted-foreground font-normal">
                    %
                  </span>
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  {stats?.used_mem_gb.toFixed(1)} /{" "}
                  {stats?.total_mem_gb.toFixed(1)} GB
                </p>
              </div>
              <div className="rounded-full p-2 bg-emerald-100 dark:bg-emerald-900/30">
                <MemoryStick className="h-4 w-4 text-emerald-600" />
              </div>
            </div>
            <div className="mt-3 h-1.5 rounded-full bg-muted overflow-hidden">
              <div
                className="h-full rounded-full bg-emerald-500 transition-all"
                style={{
                  width: `${stats ? Math.round((stats.used_mem_gb / (stats.total_mem_gb || 1)) * 100) : 0}%`,
                }}
              />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="pt-5">
            <div className="flex items-start justify-between">
              <div>
                <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">
                  虛擬機
                </p>
                <p className="text-3xl font-bold mt-1 leading-none">
                  {stats?.total_vm_count ?? 0}
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  VM / LXC 總數
                </p>
              </div>
              <div className="rounded-full p-2 bg-purple-100 dark:bg-purple-900/30">
                <Layers className="h-4 w-4 text-purple-600" />
              </div>
            </div>
            <div className="mt-3 flex gap-1">
              {stats?.nodes.map((n) => (
                <div
                  key={n.name}
                  title={`${n.name}: ${n.vm_count} VMs`}
                  className="flex-1"
                >
                  <div
                    className="h-1.5 rounded-full bg-purple-400"
                    style={{
                      opacity:
                        stats.total_vm_count > 0
                          ? 0.3 + (n.vm_count / stats.total_vm_count) * 0.7
                          : 0.3,
                    }}
                  />
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Grouped bar chart */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">各節點資源使用率</CardTitle>
          <CardDescription>CPU、記憶體、磁碟使用百分比</CardDescription>
        </CardHeader>
        <CardContent>
          {barData.length === 0 ? (
            <div className="flex items-center justify-center h-48 text-muted-foreground text-sm">
              無節點資料
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart
                data={barData}
                margin={{ top: 4, right: 16, left: -8, bottom: 0 }}
                barGap={4}
                barCategoryGap="30%"
              >
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="hsl(var(--border))"
                  vertical={false}
                />
                <XAxis
                  dataKey="name"
                  tick={{ fontSize: 12, fill: "hsl(var(--muted-foreground))" }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  domain={[0, 100]}
                  tickFormatter={(v) => `${v}%`}
                  tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
                  axisLine={false}
                  tickLine={false}
                  width={36}
                />
                <Tooltip
                  // eslint-disable-next-line @typescript-eslint/no-explicit-any
                  formatter={
                    ((value: number | undefined, name: string) => [
                      `${value ?? 0}%`,
                      name === "CPU"
                        ? "CPU"
                        : name === "RAM"
                          ? "記憶體"
                          : "磁碟",
                    ]) as any
                  }
                  contentStyle={{
                    borderRadius: "8px",
                    border: "1px solid hsl(var(--border))",
                    background: "hsl(var(--card))",
                    color: "hsl(var(--card-foreground))",
                    fontSize: 12,
                  }}
                  cursor={{ fill: "hsl(var(--muted))", opacity: 0.4 }}
                />
                <Legend
                  formatter={(value) =>
                    value === "CPU"
                      ? "CPU"
                      : value === "RAM"
                        ? "記憶體"
                        : "磁碟"
                  }
                  wrapperStyle={{ fontSize: 12 }}
                />
                <Bar
                  dataKey="CPU"
                  fill={CHART_COLORS.cpu}
                  radius={[3, 3, 0, 0]}
                />
                <Bar
                  dataKey="RAM"
                  fill={CHART_COLORS.mem}
                  radius={[3, 3, 0, 0]}
                />
                <Bar
                  dataKey="Disk"
                  fill={CHART_COLORS.disk}
                  radius={[3, 3, 0, 0]}
                />
              </BarChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      {/* Ring charts row */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Card>
          <CardContent className="flex items-center justify-center pt-6 pb-4">
            <UsageRingChart
              label="CPU 容量"
              used={stats?.used_cpu_cores ?? 0}
              total={stats?.total_cpu_cores ?? 0}
              unit="核心"
              color={CHART_COLORS.cpu}
              icon={Cpu}
            />
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center justify-center pt-6 pb-4">
            <UsageRingChart
              label="記憶體"
              used={stats?.used_mem_gb ?? 0}
              total={stats?.total_mem_gb ?? 0}
              unit="GB"
              color={CHART_COLORS.mem}
              icon={MemoryStick}
            />
          </CardContent>
        </Card>
        <Card>
          <CardContent className="flex items-center justify-center pt-6 pb-4">
            <UsageRingChart
              label="磁碟"
              used={stats?.used_disk_gb ?? 0}
              total={stats?.total_disk_gb ?? 0}
              unit="GB"
              color={CHART_COLORS.disk}
              icon={HardDrive}
            />
          </CardContent>
        </Card>
      </div>

      {/* Per-node detail table */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">節點詳細</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                {(
                  [
                    { key: "name", label: "節點", className: "" },
                    { key: "status", label: "狀態", className: "" },
                    { key: "cpu", label: "CPU", className: "" },
                    { key: "mem", label: "記憶體", className: "" },
                    { key: "disk", label: "磁碟", className: "" },
                    { key: "vm", label: "VM 數", className: "text-right" },
                  ] as { key: NodeSortKey; label: string; className: string }[]
                ).map(({ key, label, className }) => (
                  <TableHead key={key} className={className}>
                    <button
                      type="button"
                      onClick={() => toggleSort(key)}
                      className={cn(
                        "flex items-center gap-1 hover:text-foreground transition-colors select-none",
                        key === "vm" && "ml-auto",
                        sortKey === key
                          ? "text-foreground font-semibold"
                          : "text-muted-foreground",
                      )}
                    >
                      {label}
                      <span className="text-[10px] w-3 inline-block">
                        {sortKey === key ? (sortDir === "asc" ? "↑" : "↓") : ""}
                      </span>
                    </button>
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {sortedNodes.map((n) => {
                const memPct =
                  n.mem_total_gb > 0
                    ? Math.round((n.mem_used_gb / n.mem_total_gb) * 100)
                    : 0
                const diskPct =
                  n.disk_total_gb > 0
                    ? Math.round((n.disk_used_gb / n.disk_total_gb) * 100)
                    : 0
                return (
                  <TableRow key={n.name}>
                    <TableCell className="font-medium">{n.name}</TableCell>
                    <TableCell>
                      <Badge
                        variant={
                          n.status === "online" ? "default" : "destructive"
                        }
                        className={cn(
                          "text-xs gap-1",
                          n.status === "online" &&
                            "bg-green-500 hover:bg-green-600",
                        )}
                      >
                        {n.status === "online" ? (
                          <Wifi className="h-3 w-3" />
                        ) : (
                          <WifiOff className="h-3 w-3" />
                        )}
                        {n.status === "online" ? "在線" : "離線"}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <div className="h-1.5 w-20 rounded-full bg-muted overflow-hidden">
                          <div
                            className="h-full rounded-full bg-blue-500"
                            style={{ width: `${n.cpu_usage_pct}%` }}
                          />
                        </div>
                        <span className="text-xs tabular-nums">
                          {n.cpu_usage_pct}%
                        </span>
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <div className="h-1.5 w-20 rounded-full bg-muted overflow-hidden">
                          <div
                            className="h-full rounded-full bg-emerald-500"
                            style={{ width: `${memPct}%` }}
                          />
                        </div>
                        <span className="text-xs tabular-nums">{memPct}%</span>
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <div className="h-1.5 w-20 rounded-full bg-muted overflow-hidden">
                          <div
                            className="h-full rounded-full bg-amber-500"
                            style={{ width: `${diskPct}%` }}
                          />
                        </div>
                        <span className="text-xs tabular-nums">{diskPct}%</span>
                      </div>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {n.vm_count}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────

function AdminConfigPage() {
  const queryClient = useQueryClient()
  const [testResult, setTestResult] =
    useState<ProxmoxConnectionTestResult | null>(null)
  const [caCertInput, setCaCertInput] = useState("")
  const [certInfo, setCertInfo] = useState<CertParseResult | null>(null)
  const [isParsing, setIsParsing] = useState(false)
  const [caCertAction, setCaCertAction] = useState<
    "keep" | "clear" | "replace"
  >("keep")
  const [dialogOpen, setDialogOpen] = useState(false)
  const [pendingFormData, setPendingFormData] = useState<ConfigFormData | null>(
    null,
  )
  const [previewResult, setPreviewResult] =
    useState<ClusterPreviewResult | null>(null)
  const [isPreviewing, setIsPreviewing] = useState(false)
  const [editingNode, setEditingNode] = useState<ProxmoxNodePublic | null>(null)
  const [showAdvanced, setShowAdvanced] = useState(false)

  const nodeForm = useForm<NodeFormData>({
    defaultValues: { host: "", port: 8006, priority: 5 },
  })

  const { data: config, isLoading: configLoading } = useQuery({
    queryKey: ["proxmoxConfig"],
    queryFn: ProxmoxConfigService.getConfig,
  })

  const { data: nodes, isLoading: nodesLoading } = useQuery({
    queryKey: ["proxmoxNodes"],
    queryFn: ProxmoxConfigService.getNodes,
  })

  const form = useForm<ConfigFormData>({
    defaultValues: {
      host: "",
      user: "",
      password: "",
      verify_ssl: false,
      iso_storage: "local",
      data_storage: "local-lvm",
      api_timeout: 30,
      task_check_interval: 2,
      pool_name: "CampusCloud",
      gateway_ip: "",
      local_subnet: "",
      default_node: "",
      placement_strategy: "priority_dominant_share",
      cpu_overcommit_ratio: 2.0,
      disk_overcommit_ratio: 1.0,
      migration_enabled: true,
      migration_max_per_rebalance: 2,
      migration_min_interval_minutes: 60,
      migration_retry_limit: 3,
      rebalance_migration_cost: 0.15,
      rebalance_peak_cpu_margin: 1.1,
      rebalance_peak_memory_margin: 1.05,
      rebalance_loadavg_warn_per_core: 0.8,
      rebalance_loadavg_max_per_core: 1.5,
      rebalance_loadavg_penalty_weight: 0.9,
      rebalance_cpu_peak_warn_share: 0.7,
      rebalance_cpu_peak_high_share: 1.2,
      rebalance_memory_peak_warn_share: 0.8,
      rebalance_memory_peak_high_share: 0.85,
      rebalance_resource_weight_cpu: 1.0,
      rebalance_resource_weight_memory: 1.0,
      rebalance_resource_weight_disk: 1.0,
      migration_lxc_live_enabled: false,
      scheduled_boot_batch_size: 5,
      scheduled_boot_batch_interval_seconds: 10,
      scheduled_boot_lead_time_minutes: 5,
      window_grace_period_minutes: 30,
      practice_session_hours: 3,
      practice_warning_minutes: 30,
    },
  })

  useEffect(() => {
    if (config) {
      form.reset({
        host: config.host,
        user: config.user,
        password: "",
        verify_ssl: config.verify_ssl,
        iso_storage: config.iso_storage,
        data_storage: config.data_storage,
        api_timeout: config.api_timeout,
        task_check_interval: config.task_check_interval,
        pool_name: config.pool_name,
        gateway_ip: config.gateway_ip ?? "",
        local_subnet: config.local_subnet ?? "",
        default_node: config.default_node ?? "",
        placement_strategy: "priority_dominant_share",
        cpu_overcommit_ratio: config.cpu_overcommit_ratio ?? 2.0,
        disk_overcommit_ratio: config.disk_overcommit_ratio ?? 1.0,
        migration_enabled: config.migration_enabled ?? true,
        migration_max_per_rebalance: config.migration_max_per_rebalance ?? 2,
        migration_min_interval_minutes:
          config.migration_min_interval_minutes ?? 60,
        migration_retry_limit: config.migration_retry_limit ?? 3,
        rebalance_migration_cost: config.rebalance_migration_cost ?? 0.15,
        rebalance_peak_cpu_margin: config.rebalance_peak_cpu_margin ?? 1.1,
        rebalance_peak_memory_margin:
          config.rebalance_peak_memory_margin ?? 1.05,
        rebalance_loadavg_warn_per_core:
          config.rebalance_loadavg_warn_per_core ?? 0.8,
        rebalance_loadavg_max_per_core:
          config.rebalance_loadavg_max_per_core ?? 1.5,
        rebalance_loadavg_penalty_weight:
          config.rebalance_loadavg_penalty_weight ?? 0.9,
        rebalance_cpu_peak_warn_share:
          config.rebalance_cpu_peak_warn_share ?? 0.7,
        rebalance_cpu_peak_high_share:
          config.rebalance_cpu_peak_high_share ?? 1.2,
        rebalance_memory_peak_warn_share:
          config.rebalance_memory_peak_warn_share ?? 0.8,
        rebalance_memory_peak_high_share:
          config.rebalance_memory_peak_high_share ?? 0.85,
        rebalance_resource_weight_cpu:
          config.rebalance_resource_weight_cpu ?? 1.0,
        rebalance_resource_weight_memory:
          config.rebalance_resource_weight_memory ?? 1.0,
        rebalance_resource_weight_disk:
          config.rebalance_resource_weight_disk ?? 1.0,
        migration_lxc_live_enabled: config.migration_lxc_live_enabled ?? false,
        scheduled_boot_batch_size: config.scheduled_boot_batch_size ?? 5,
        scheduled_boot_batch_interval_seconds:
          config.scheduled_boot_batch_interval_seconds ?? 10,
        scheduled_boot_lead_time_minutes:
          config.scheduled_boot_lead_time_minutes ?? 5,
        window_grace_period_minutes: config.window_grace_period_minutes ?? 30,
        practice_session_hours: config.practice_session_hours ?? 3,
        practice_warning_minutes: config.practice_warning_minutes ?? 30,
      })
    }
  }, [config, form])

  const handleCertInput = async (value: string) => {
    setCaCertInput(value)
    setCertInfo(null)
    setCaCertAction("replace")
    if (!value.trim()) {
      setCaCertAction("keep")
      return
    }
    if (!value.includes("BEGIN CERTIFICATE")) return
    setIsParsing(true)
    try {
      const result = await ProxmoxConfigService.parseCert(value.trim())
      setCertInfo(result)
    } catch {
      setCertInfo({
        valid: false,
        fingerprint: null,
        subject: null,
        issuer: null,
        not_before: null,
        not_after: null,
        error: "解析失敗",
      })
    } finally {
      setIsParsing(false)
    }
  }

  const handleClearCert = () => {
    setCaCertInput("")
    setCertInfo(null)
    setCaCertAction("clear")
  }

  const buildConfigPayload = (data: ConfigFormData): ProxmoxConfigUpdate => {
    let ca_cert: string | null | undefined
    if (caCertAction === "replace") {
      ca_cert = caCertInput.trim() || null
    } else if (caCertAction === "clear") {
      ca_cert = ""
    } else {
      ca_cert = null
    }
    return {
      host: data.host,
      user: data.user,
      password: data.password || null,
      verify_ssl: data.verify_ssl,
      iso_storage: data.iso_storage,
      data_storage: data.data_storage,
      api_timeout: data.api_timeout,
      task_check_interval: data.task_check_interval,
      pool_name: data.pool_name,
      ca_cert,
      gateway_ip: data.gateway_ip || null,
      local_subnet: data.local_subnet || null,
      default_node: data.default_node || null,
      placement_strategy: "priority_dominant_share",
      cpu_overcommit_ratio: data.cpu_overcommit_ratio,
      disk_overcommit_ratio: data.disk_overcommit_ratio,
      migration_enabled: data.migration_enabled,
      migration_max_per_rebalance: data.migration_max_per_rebalance,
      migration_min_interval_minutes: data.migration_min_interval_minutes,
      migration_retry_limit: data.migration_retry_limit,
      rebalance_migration_cost: data.rebalance_migration_cost,
      rebalance_peak_cpu_margin: data.rebalance_peak_cpu_margin,
      rebalance_peak_memory_margin: data.rebalance_peak_memory_margin,
      rebalance_loadavg_warn_per_core: data.rebalance_loadavg_warn_per_core,
      rebalance_loadavg_max_per_core: data.rebalance_loadavg_max_per_core,
      rebalance_loadavg_penalty_weight: data.rebalance_loadavg_penalty_weight,
      rebalance_cpu_peak_warn_share: data.rebalance_cpu_peak_warn_share,
      rebalance_cpu_peak_high_share: data.rebalance_cpu_peak_high_share,
      rebalance_memory_peak_warn_share: data.rebalance_memory_peak_warn_share,
      rebalance_memory_peak_high_share: data.rebalance_memory_peak_high_share,
      rebalance_resource_weight_cpu: data.rebalance_resource_weight_cpu,
      rebalance_resource_weight_memory: data.rebalance_resource_weight_memory,
      rebalance_resource_weight_disk: data.rebalance_resource_weight_disk,
      migration_lxc_live_enabled: data.migration_lxc_live_enabled,
      scheduled_boot_batch_size: data.scheduled_boot_batch_size,
      scheduled_boot_batch_interval_seconds:
        data.scheduled_boot_batch_interval_seconds,
      scheduled_boot_lead_time_minutes: data.scheduled_boot_lead_time_minutes,
      window_grace_period_minutes: data.window_grace_period_minutes,
      practice_session_hours: data.practice_session_hours,
      practice_warning_minutes: data.practice_warning_minutes,
    }
  }

  const saveMutation = useMutation({
    mutationFn: async ({ data }: { data: ConfigFormData }) => {
      await ProxmoxConfigService.updateConfig(buildConfigPayload(data))
    },
    onSuccess: () => {
      toast.success("設定已儲存")
      setTestResult(null)
      setCaCertInput("")
      setCertInfo(null)
      setCaCertAction("keep")
      setDialogOpen(false)
      setPendingFormData(null)
      setPreviewResult(null)
      queryClient.invalidateQueries({ queryKey: ["proxmoxConfig"] })
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "儲存失敗，請稍後再試"
      toast.error(msg)
    },
  })

  const testMutation = useMutation({
    mutationFn: ProxmoxConfigService.testConnection,
    onSuccess: (result) => {
      setTestResult(result)
      if (result.success) toast.success(result.message)
      else toast.error(result.message)
    },
    onError: () => toast.error("測試請求失敗"),
  })

  const syncNowMutation = useMutation({
    mutationFn: ProxmoxConfigService.syncNow,
    onSuccess: (result) => {
      if (result.success) {
        toast.success(
          `同步完成：${result.nodes.length} 個節點、${result.storage_count} 個 Storage`,
        )
        queryClient.invalidateQueries({ queryKey: ["proxmoxNodes"] })
        queryClient.invalidateQueries({ queryKey: ["proxmoxStorages"] })
        queryClient.invalidateQueries({ queryKey: ["clusterStats"] })
      } else {
        toast.error(result.error ?? "同步失敗")
      }
    },
    onError: () => toast.error("同步請求失敗"),
  })

  const updateNodeMutation = useMutation({
    mutationFn: ({ id, body }: { id: number; body: ProxmoxNodeUpdate }) =>
      ProxmoxConfigService.updateNode(id, body),
    onSuccess: () => {
      toast.success("節點設定已更新")
      setEditingNode(null)
      queryClient.invalidateQueries({ queryKey: ["proxmoxNodes"] })
    },
    onError: () => toast.error("節點更新失敗"),
  })

  const onSubmit = async (data: ConfigFormData) => {
    setIsPreviewing(true)
    try {
      const preview = await ProxmoxConfigService.previewCluster(
        buildConfigPayload(data),
      )
      if (!preview.success) {
        toast.error(`無法連線偵測節點：${preview.error}`)
        return
      }
      if (preview.is_cluster) {
        setPendingFormData(data)
        setPreviewResult(preview)
        setDialogOpen(true)
      } else {
        saveMutation.mutate({ data })
      }
    } catch {
      toast.error("偵測節點失敗，請確認設定後再試")
    } finally {
      setIsPreviewing(false)
    }
  }

  const openEditNode = (node: ProxmoxNodePublic) => {
    setEditingNode(node)
    nodeForm.reset({
      host: node.host,
      port: node.port,
      priority: node.priority,
    })
  }

  const isSaving = saveMutation.isPending
  const isSubmitting = isPreviewing || isSaving
  const cpuVal = form.watch("cpu_overcommit_ratio")
  const diskVal = form.watch("disk_overcommit_ratio")
  const strategyVal = form.watch("placement_strategy")
  const migrationEnabledVal = form.watch("migration_enabled")

  const SaveButton = () => (
    <div className="flex justify-end pt-2">
      <LoadingButton
        type="submit"
        loading={isSubmitting}
        disabled={isSubmitting}
      >
        <Save className="mr-2 h-4 w-4" />
        {isPreviewing ? "偵測節點中..." : isSaving ? "儲存中..." : "儲存設定"}
      </LoadingButton>
    </div>
  )

  return (
    <div className="flex flex-col gap-6">
      {/* Page header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">系統設定</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            管理 Proxmox VE 連線、節點、Storage 與資源排程設定。
          </p>
        </div>
        {!configLoading &&
          (config?.is_configured ? (
            <Badge className="bg-green-500 hover:bg-green-600 gap-1.5 shrink-0 mt-1">
              <CheckCircle className="h-3.5 w-3.5" />
              {config.host}
            </Badge>
          ) : (
            <Badge variant="destructive" className="gap-1.5 shrink-0 mt-1">
              <XCircle className="h-3.5 w-3.5" />
              未設定
            </Badge>
          ))}
      </div>

      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)}>
          <Tabs defaultValue="overview">
            <TabsList className="flex-wrap h-auto gap-1">
              <TabsTrigger value="overview" className="gap-1.5">
                <Layers className="h-3.5 w-3.5" />
                叢集概覽
              </TabsTrigger>
              <TabsTrigger value="connection" className="gap-1.5">
                <Server className="h-3.5 w-3.5" />
                PVE 連線
              </TabsTrigger>
              <TabsTrigger value="scheduler" className="gap-1.5">
                <Cpu className="h-3.5 w-3.5" />
                資源排程
              </TabsTrigger>
              <TabsTrigger value="nodes" className="gap-1.5">
                <HardDrive className="h-3.5 w-3.5" />
                節點管理
              </TabsTrigger>
              <TabsTrigger value="storage" className="gap-1.5">
                <Database className="h-3.5 w-3.5" />
                Storage
              </TabsTrigger>
            </TabsList>

            {/* ═══ 叢集概覽 ═══ */}
            <TabsContent value="overview" className="mt-5">
              <ClusterOverviewTab />
            </TabsContent>

            {/* ═══ PVE 連線 ═══ */}
            <TabsContent value="connection" className="mt-5">
              <div className="grid gap-5 lg:grid-cols-3">
                {/* Left: status card */}
                <div className="space-y-4">
                  <Card>
                    <CardHeader className="pb-2">
                      <CardTitle className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
                        連線狀態
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      {configLoading ? (
                        <Badge variant="outline">載入中...</Badge>
                      ) : config?.is_configured ? (
                        <Badge className="bg-green-500 hover:bg-green-600">
                          已設定
                        </Badge>
                      ) : (
                        <Badge variant="destructive">未設定</Badge>
                      )}

                      {config?.is_configured && (
                        <div className="space-y-2 text-sm">
                          <div className="flex items-center gap-2 text-muted-foreground">
                            <Server className="h-3.5 w-3.5 shrink-0" />
                            <span className="truncate font-mono text-xs">
                              {config.host}
                            </span>
                          </div>
                          <div className="flex items-center gap-2 text-muted-foreground">
                            <User className="h-3.5 w-3.5 shrink-0" />
                            <span className="text-xs">{config.user}</span>
                          </div>
                          <div className="flex items-center gap-2 text-muted-foreground">
                            {config.verify_ssl ? (
                              <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-green-500" />
                            ) : (
                              <ShieldOff className="h-3.5 w-3.5 shrink-0" />
                            )}
                            <span className="text-xs">
                              {config.verify_ssl
                                ? "SSL 驗證啟用"
                                : "SSL 驗證停用"}
                            </span>
                          </div>
                          <div className="flex items-center gap-2 text-muted-foreground">
                            <Lock
                              className={cn(
                                "h-3.5 w-3.5 shrink-0",
                                config.has_ca_cert
                                  ? "text-blue-500"
                                  : "opacity-40",
                              )}
                            />
                            <span className="text-xs">
                              {config.has_ca_cert
                                ? "已設定 CA 憑證"
                                : "未設定 CA 憑證"}
                            </span>
                          </div>
                          {config.ca_fingerprint && (
                            <div className="rounded bg-muted px-2 py-1.5">
                              <p className="font-mono text-[10px] text-muted-foreground break-all leading-relaxed">
                                {config.ca_fingerprint}
                              </p>
                            </div>
                          )}
                          {config.updated_at && (
                            <p className="text-[11px] text-muted-foreground pt-1 border-t">
                              更新於{" "}
                              {new Date(config.updated_at).toLocaleString(
                                "zh-TW",
                              )}
                            </p>
                          )}
                        </div>
                      )}

                      <Separator />

                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="w-full"
                        disabled={
                          !config?.is_configured || testMutation.isPending
                        }
                        onClick={() => testMutation.mutate()}
                      >
                        {testMutation.isPending ? (
                          <>
                            <RefreshCw className="mr-2 h-3.5 w-3.5 animate-spin" />
                            測試中...
                          </>
                        ) : (
                          <>
                            <ShieldCheck className="mr-2 h-3.5 w-3.5" />
                            測試連線
                          </>
                        )}
                      </Button>

                      {testResult && (
                        <div
                          className={cn(
                            "flex items-start gap-2 rounded-md p-2.5 text-xs",
                            testResult.success
                              ? "bg-green-50 text-green-800 dark:bg-green-950 dark:text-green-200"
                              : "bg-red-50 text-red-800 dark:bg-red-950 dark:text-red-200",
                          )}
                        >
                          {testResult.success ? (
                            <CheckCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                          ) : (
                            <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                          )}
                          <span>{testResult.message}</span>
                        </div>
                      )}
                    </CardContent>
                  </Card>
                </div>

                {/* Right: form */}
                <div className="lg:col-span-2 flex flex-col gap-4">
                  <Card>
                    <CardHeader className="pb-3">
                      <CardTitle className="text-base">連線資訊</CardTitle>
                      <CardDescription>
                        {config?.is_configured
                          ? "更新 Proxmox 連線資訊。密碼留空代表不更改。"
                          : "填寫 Proxmox VE 主機連線資訊以完成初始設定。"}
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                      <FormField
                        control={form.control}
                        name="host"
                        rules={{ required: "請輸入主機位址" }}
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>
                              主機位址{" "}
                              <span className="text-destructive">*</span>
                            </FormLabel>
                            <FormControl>
                              <Input
                                placeholder="192.168.1.100 或 pve.example.com"
                                {...field}
                              />
                            </FormControl>
                            <FormDescription>
                              Proxmox VE 主機的 IP 或網域名稱（初始節點）
                            </FormDescription>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                      <div className="grid grid-cols-2 gap-4">
                        <FormField
                          control={form.control}
                          name="user"
                          rules={{ required: "請輸入 API 用戶" }}
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>
                                API 用戶{" "}
                                <span className="text-destructive">*</span>
                              </FormLabel>
                              <FormControl>
                                <Input placeholder="root@pam" {...field} />
                              </FormControl>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                        <FormField
                          control={form.control}
                          name="password"
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>
                                密碼{" "}
                                {!config?.is_configured && (
                                  <span className="text-destructive">*</span>
                                )}
                              </FormLabel>
                              <FormControl>
                                <Input
                                  type="password"
                                  placeholder={
                                    config?.is_configured
                                      ? "留空表示不更改"
                                      : "請輸入密碼"
                                  }
                                  {...field}
                                />
                              </FormControl>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                      </div>
                      <FormField
                        control={form.control}
                        name="verify_ssl"
                        render={({ field }) => (
                          <FormItem className="flex items-start gap-3 space-y-0 rounded-lg border p-3">
                            <FormControl>
                              <Checkbox
                                checked={field.value}
                                onCheckedChange={field.onChange}
                                className="mt-0.5"
                              />
                            </FormControl>
                            <div>
                              <FormLabel className="font-medium cursor-pointer">
                                驗證 SSL 憑證
                              </FormLabel>
                              <FormDescription className="text-xs mt-0.5">
                                建議在生產環境啟用；自簽憑證請搭配下方 CA
                                憑證設定
                              </FormDescription>
                            </div>
                          </FormItem>
                        )}
                      />
                    </CardContent>
                  </Card>

                  {/* CA Cert */}
                  <Card>
                    <CardHeader className="pb-3">
                      <div className="flex items-start justify-between">
                        <div>
                          <CardTitle className="text-base">CA 憑證</CardTitle>
                          <CardDescription className="mt-0.5">
                            用於自簽憑證的 TLS 信任錨點，選填。
                          </CardDescription>
                        </div>
                        {config?.has_ca_cert && caCertAction === "keep" && (
                          <Badge variant="secondary" className="gap-1 text-xs">
                            <ShieldCheck className="h-3 w-3 text-green-500" />
                            已設定
                          </Badge>
                        )}
                      </div>
                    </CardHeader>
                    <CardContent>
                      {config?.has_ca_cert && caCertAction === "keep" ? (
                        <div className="flex items-center justify-between rounded-md border p-3">
                          <div className="flex items-center gap-2">
                            <ShieldCheck className="h-4 w-4 text-green-500" />
                            <div>
                              <p className="text-sm font-medium">
                                已設定 CA 憑證
                              </p>
                              {config.ca_fingerprint && (
                                <p className="font-mono text-[11px] text-muted-foreground">
                                  SHA-256: {config.ca_fingerprint.slice(0, 29)}
                                  ...
                                </p>
                              )}
                            </div>
                          </div>
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={handleClearCert}
                          >
                            <Trash2 className="h-3.5 w-3.5 mr-1" />
                            清除
                          </Button>
                        </div>
                      ) : !config?.has_ca_cert && caCertAction === "keep" ? (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={() => setCaCertAction("replace")}
                        >
                          <Lock className="mr-2 h-3.5 w-3.5" />
                          設定 CA 憑證
                        </Button>
                      ) : (
                        <div className="space-y-2">
                          <Textarea
                            placeholder={
                              "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----"
                            }
                            className="font-mono text-xs"
                            rows={6}
                            value={caCertInput}
                            onChange={(e) => handleCertInput(e.target.value)}
                          />
                          {isParsing && (
                            <p className="text-xs text-muted-foreground">
                              解析中...
                            </p>
                          )}
                          {certInfo && (
                            <div
                              className={cn(
                                "rounded-md p-3 text-xs space-y-1",
                                certInfo.valid
                                  ? "bg-green-50 dark:bg-green-950"
                                  : "bg-red-50 dark:bg-red-950",
                              )}
                            >
                              {certInfo.valid ? (
                                <>
                                  <p>
                                    <span className="font-medium">指紋：</span>
                                    {certInfo.fingerprint}
                                  </p>
                                  <p>
                                    <span className="font-medium">主體：</span>
                                    {certInfo.subject}
                                  </p>
                                  <p>
                                    <span className="font-medium">
                                      有效期：
                                    </span>
                                    {certInfo.not_before} ~ {certInfo.not_after}
                                  </p>
                                </>
                              ) : (
                                <p className="text-red-700 dark:text-red-300">
                                  <AlertTriangle className="inline h-3 w-3 mr-1" />
                                  {certInfo.error}
                                </p>
                              )}
                            </div>
                          )}
                          {caCertAction === "clear" && !caCertInput && (
                            <p className="text-xs text-amber-600">
                              儲存後將清除現有 CA 憑證
                            </p>
                          )}
                          {caCertAction === "replace" && (
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              onClick={() => setCaCertAction("keep")}
                            >
                              取消
                            </Button>
                          )}
                        </div>
                      )}
                    </CardContent>
                  </Card>

                  <SaveButton />
                </div>
              </div>
            </TabsContent>

            {/* ═══ 資源排程 ═══ */}
            <TabsContent value="scheduler" className="mt-5">
              <div className="flex flex-col gap-4">
                <div className="grid gap-4 lg:grid-cols-2">
                  {/* VM 資源預設 */}
                  <Card>
                    <CardHeader className="pb-3">
                      <CardTitle className="text-base flex items-center gap-2">
                        <HardDrive className="h-4 w-4 text-muted-foreground" />
                        VM 資源預設
                      </CardTitle>
                      <CardDescription>
                        新建 VM 時的預設 Storage 與集區
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="grid grid-cols-2 gap-4">
                      <FormField
                        control={form.control}
                        name="iso_storage"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>ISO Storage</FormLabel>
                            <FormControl>
                              <Input placeholder="local" {...field} />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                      <FormField
                        control={form.control}
                        name="data_storage"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>資料 Storage</FormLabel>
                            <FormControl>
                              <Input placeholder="local-lvm" {...field} />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                      <FormField
                        control={form.control}
                        name="pool_name"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>集區名稱</FormLabel>
                            <FormControl>
                              <Input placeholder="CampusCloud" {...field} />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                      <FormField
                        control={form.control}
                        name="default_node"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>預設建立節點</FormLabel>
                            <FormControl>
                              <Input placeholder="pve" {...field} />
                            </FormControl>
                            <FormDescription className="text-xs">
                              留空則自動排程
                            </FormDescription>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    </CardContent>
                  </Card>

                  {/* 網路與進階 */}
                  <div className="flex flex-col gap-4">
                    <Card>
                      <CardHeader className="pb-3">
                        <CardTitle className="text-base flex items-center gap-2">
                          <Wifi className="h-4 w-4 text-muted-foreground" />
                          網路設定
                        </CardTitle>
                      </CardHeader>
                      <CardContent className="grid grid-cols-2 gap-4">
                        <FormField
                          control={form.control}
                          name="gateway_ip"
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>Gateway IP</FormLabel>
                              <FormControl>
                                <Input placeholder="192.168.1.1" {...field} />
                              </FormControl>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                        <FormField
                          control={form.control}
                          name="local_subnet"
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>Local Subnet</FormLabel>
                              <FormControl>
                                <Input
                                  placeholder="192.168.1.0/24"
                                  {...field}
                                />
                              </FormControl>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                      </CardContent>
                    </Card>

                    <Card>
                      <CardHeader className="pb-3">
                        <CardTitle className="text-base">進階設定</CardTitle>
                      </CardHeader>
                      <CardContent className="grid grid-cols-2 gap-4">
                        <FormField
                          control={form.control}
                          name="api_timeout"
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>API 逾時（秒）</FormLabel>
                              <FormControl>
                                <Input
                                  type="number"
                                  min={1}
                                  max={300}
                                  {...field}
                                  onChange={(e) =>
                                    field.onChange(Number(e.target.value))
                                  }
                                />
                              </FormControl>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                        <FormField
                          control={form.control}
                          name="task_check_interval"
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>輪詢間隔（秒）</FormLabel>
                              <FormControl>
                                <Input
                                  type="number"
                                  min={1}
                                  max={60}
                                  {...field}
                                  onChange={(e) =>
                                    field.onChange(Number(e.target.value))
                                  }
                                />
                              </FormControl>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                      </CardContent>
                    </Card>
                  </div>
                </div>

                {/* 排程策略 */}
                <Card>
                  <CardHeader className="pb-3">
                    <CardTitle className="text-base flex items-center gap-2">
                      <Layers className="h-4 w-4 text-muted-foreground" />
                      排程策略
                    </CardTitle>
                    <CardDescription>
                      控制 VM 自動放置的算法與資源超配比例
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-6">
                    <div className="hidden">
                      {/* Strategy radio cards */}
                      <FormField
                        control={form.control}
                        name="placement_strategy"
                        render={() => (
                          <FormItem>
                            <FormLabel>放置策略</FormLabel>
                            <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
                              {[
                                {
                                  value: "dominant_share_min",
                                  title: "Dominant Share Min",
                                  desc: "每次選擇主要資源份額最低的節點，讓 CPU / RAM / Disk 負載平均分散於整個叢集。",
                                },
                                {
                                  value: "priority_dominant_share",
                                  title: "Priority Dominant Share",
                                  desc: "先按節點優先級篩選候選節點，相同優先級內再以 Dominant Share 排序，適合多區域場景。",
                                },
                              ].map((opt) => (
                                <button
                                  key={opt.value}
                                  type="button"
                                  onClick={() =>
                                    form.setValue(
                                      "placement_strategy",
                                      opt.value,
                                    )
                                  }
                                  className={cn(
                                    "rounded-lg border-2 p-4 text-left transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                                    strategyVal === opt.value
                                      ? "border-primary bg-primary/5"
                                      : "border-border hover:border-muted-foreground/50",
                                  )}
                                >
                                  <div className="flex items-center gap-2 mb-1.5">
                                    <div
                                      className={cn(
                                        "h-3.5 w-3.5 rounded-full border-2",
                                        strategyVal === opt.value
                                          ? "border-primary bg-primary"
                                          : "border-muted-foreground",
                                      )}
                                    />
                                    <p className="font-medium text-sm">
                                      {opt.title}
                                    </p>
                                  </div>
                                  <p className="text-xs text-muted-foreground leading-relaxed">
                                    {opt.desc}
                                  </p>
                                </button>
                              ))}
                            </div>
                          </FormItem>
                        )}
                      />

                      <Separator />
                    </div>

                    <div className="rounded-lg border border-border/70 bg-muted/20 p-4">
                      <div className="flex items-center justify-between gap-3">
                        <div>
                          <p className="text-sm font-medium">Priority 排程</p>
                          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                            系統固定先依節點 Priority 排序；若 Priority
                            相同，會自動平均分配到同級節點。
                          </p>
                        </div>
                        <Badge variant="secondary" className="shrink-0">
                          固定啟用
                        </Badge>
                      </div>
                    </div>

                    {/* Overcommit sliders */}
                    <div className="grid gap-6 sm:grid-cols-2">
                      <FormField
                        control={form.control}
                        name="cpu_overcommit_ratio"
                        render={({ field }) => (
                          <FormItem>
                            <div className="flex items-center justify-between mb-2">
                              <FormLabel>CPU Overcommit</FormLabel>
                              <span className="text-sm font-mono font-semibold tabular-nums">
                                {cpuVal.toFixed(1)}×
                              </span>
                            </div>
                            <FormControl>
                              <Slider
                                min={1.0}
                                max={8.0}
                                step={0.1}
                                value={[field.value]}
                                onValueChange={([val]) => field.onChange(val)}
                                className="my-1"
                              />
                            </FormControl>
                            <div className="flex justify-between text-[10px] text-muted-foreground mt-1">
                              <span>1×</span>
                              <span>8×</span>
                            </div>
                            <p className="text-xs text-muted-foreground mt-1">
                              {cpuVal <= 1.05
                                ? "不允許 CPU 超配"
                                : `允許配置 ${cpuVal.toFixed(1)} 倍實體核心數；超配僅在無法正常放置時啟用`}
                            </p>
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={form.control}
                        name="disk_overcommit_ratio"
                        render={({ field }) => (
                          <FormItem>
                            <div className="flex items-center justify-between mb-2">
                              <FormLabel>Disk Overcommit</FormLabel>
                              <span className="text-sm font-mono font-semibold tabular-nums">
                                {diskVal.toFixed(1)}×
                              </span>
                            </div>
                            <FormControl>
                              <Slider
                                min={1.0}
                                max={5.0}
                                step={0.1}
                                value={[field.value]}
                                onValueChange={([val]) => field.onChange(val)}
                                className="my-1"
                              />
                            </FormControl>
                            <div className="flex justify-between text-[10px] text-muted-foreground mt-1">
                              <span>1×</span>
                              <span>5×</span>
                            </div>
                            <p className="text-xs text-muted-foreground mt-1">
                              {diskVal <= 1.05
                                ? "不超額：僅使用實際可用磁碟空間"
                                : `允許超配至 ${Math.round(diskVal * 100)}%；先塞實際空間，不足才啟用`}
                            </p>
                          </FormItem>
                        )}
                      />
                    </div>

                    <Separator />

                    <div className="grid gap-4 lg:grid-cols-[1.2fr_1fr_1fr]">
                      <FormField
                        control={form.control}
                        name="migration_enabled"
                        render={({ field }) => (
                          <FormItem className="flex items-start justify-between rounded-lg border p-4">
                            <div className="space-y-1">
                              <FormLabel>自動搬移</FormLabel>
                              <FormDescription>
                                僅在時段開始重排時，自動搬移已存在 VM/LXC
                                以追求更平衡的分配。
                              </FormDescription>
                            </div>
                            <FormControl>
                              <Switch
                                checked={field.value}
                                onCheckedChange={field.onChange}
                              />
                            </FormControl>
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={form.control}
                        name="migration_max_per_rebalance"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>每輪最多搬移</FormLabel>
                            <FormControl>
                              <Input
                                type="number"
                                min={0}
                                max={20}
                                {...field}
                                disabled={!migrationEnabledVal}
                                onChange={(e) =>
                                  field.onChange(Number(e.target.value))
                                }
                              />
                            </FormControl>
                            <FormDescription>
                              0 代表只重排規劃，不執行搬移。
                            </FormDescription>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={form.control}
                        name="migration_min_interval_minutes"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>最短搬移間隔</FormLabel>
                            <FormControl>
                              <Input
                                type="number"
                                min={0}
                                max={10080}
                                {...field}
                                disabled={!migrationEnabledVal}
                                onChange={(e) =>
                                  field.onChange(Number(e.target.value))
                                }
                              />
                            </FormControl>
                            <FormDescription>
                              同一台 request 在這段時間內不會被再次搬移。
                            </FormDescription>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    </div>

                    <Separator />

                    <div className="space-y-1">
                      <h4 className="text-sm font-medium">Peak Share 門檻</h4>
                      <p className="text-xs text-muted-foreground">
                        控制尖峰負載懲罰的觸發門檻。超過 Warn 開始加罰，達到
                        High 時罰到最大值。
                      </p>
                    </div>
                    <div className="grid gap-4 lg:grid-cols-4">
                      <FormField
                        control={form.control}
                        name="rebalance_cpu_peak_warn_share"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>CPU Peak Warn</FormLabel>
                            <FormControl>
                              <Input
                                type="number"
                                min={0}
                                max={2}
                                step={0.05}
                                {...field}
                                onChange={(e) =>
                                  field.onChange(Number(e.target.value))
                                }
                              />
                            </FormControl>
                            <FormDescription>
                              CPU 佔比超過此值開始加 peak penalty。
                            </FormDescription>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={form.control}
                        name="rebalance_cpu_peak_high_share"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>CPU Peak High</FormLabel>
                            <FormControl>
                              <Input
                                type="number"
                                min={0.1}
                                max={3}
                                step={0.05}
                                {...field}
                                onChange={(e) =>
                                  field.onChange(Number(e.target.value))
                                }
                              />
                            </FormControl>
                            <FormDescription>
                              CPU 佔比達到此值時 penalty 視為滿額。
                            </FormDescription>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={form.control}
                        name="rebalance_memory_peak_warn_share"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Memory Peak Warn</FormLabel>
                            <FormControl>
                              <Input
                                type="number"
                                min={0}
                                max={2}
                                step={0.05}
                                {...field}
                                onChange={(e) =>
                                  field.onChange(Number(e.target.value))
                                }
                              />
                            </FormControl>
                            <FormDescription>
                              RAM 佔比超過此值開始加 peak penalty。
                            </FormDescription>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={form.control}
                        name="rebalance_memory_peak_high_share"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Memory Peak High</FormLabel>
                            <FormControl>
                              <Input
                                type="number"
                                min={0.1}
                                max={3}
                                step={0.05}
                                {...field}
                                onChange={(e) =>
                                  field.onChange(Number(e.target.value))
                                }
                              />
                            </FormControl>
                            <FormDescription>
                              RAM 佔比達到此值時 penalty 視為滿額。
                            </FormDescription>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    </div>

                    <Separator />

                    <div className="space-y-1">
                      <h4 className="text-sm font-medium">
                        資源權重 (Resource Weights)
                      </h4>
                      <p className="text-xs text-muted-foreground">
                        調整 CPU、RAM、Disk
                        在平衡評分中的相對權重。數值越高該資源影響越大。
                      </p>
                    </div>
                    <div className="grid gap-4 lg:grid-cols-3">
                      <FormField
                        control={form.control}
                        name="rebalance_resource_weight_cpu"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>CPU Weight</FormLabel>
                            <FormControl>
                              <Input
                                type="number"
                                min={0}
                                max={10}
                                step={0.1}
                                {...field}
                                onChange={(e) =>
                                  field.onChange(Number(e.target.value))
                                }
                              />
                            </FormControl>
                            <FormDescription>
                              預設 1.0。提高後演算法更偏重 CPU 均衡。
                            </FormDescription>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={form.control}
                        name="rebalance_resource_weight_memory"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Memory Weight</FormLabel>
                            <FormControl>
                              <Input
                                type="number"
                                min={0}
                                max={10}
                                step={0.1}
                                {...field}
                                onChange={(e) =>
                                  field.onChange(Number(e.target.value))
                                }
                              />
                            </FormControl>
                            <FormDescription>
                              預設 1.0。提高後演算法更偏重 RAM 均衡。
                            </FormDescription>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={form.control}
                        name="rebalance_resource_weight_disk"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Disk Weight</FormLabel>
                            <FormControl>
                              <Input
                                type="number"
                                min={0}
                                max={10}
                                step={0.1}
                                {...field}
                                onChange={(e) =>
                                  field.onChange(Number(e.target.value))
                                }
                              />
                            </FormControl>
                            <FormDescription>
                              預設 1.0。提高後演算法更偏重磁碟均衡。
                            </FormDescription>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    </div>

                    <Separator />

                    {/* ── 排程開機 / 自動關機設定 ── */}
                    <div className="space-y-4">
                      <div>
                        <h3 className="text-base font-semibold">
                          排程開機 / 自動關機
                        </h3>
                        <p className="text-sm text-muted-foreground">
                          老師的群組週期排程會用這組參數決定提早開機節奏與自動關機行為。
                        </p>
                      </div>
                      <div className="grid gap-4 md:grid-cols-3">
                        <FormField
                          control={form.control}
                          name="scheduled_boot_batch_size"
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>每批開機台數</FormLabel>
                              <FormControl>
                                <Input
                                  type="number"
                                  min={1}
                                  max={100}
                                  {...field}
                                  onChange={(e) =>
                                    field.onChange(Number(e.target.value))
                                  }
                                />
                              </FormControl>
                              <FormDescription>預設 5。</FormDescription>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                        <FormField
                          control={form.control}
                          name="scheduled_boot_batch_interval_seconds"
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>每批間隔 (秒)</FormLabel>
                              <FormControl>
                                <Input
                                  type="number"
                                  min={0}
                                  max={600}
                                  {...field}
                                  onChange={(e) =>
                                    field.onChange(Number(e.target.value))
                                  }
                                />
                              </FormControl>
                              <FormDescription>預設 10。</FormDescription>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                        <FormField
                          control={form.control}
                          name="scheduled_boot_lead_time_minutes"
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>提早開機 (分)</FormLabel>
                              <FormControl>
                                <Input
                                  type="number"
                                  min={0}
                                  max={120}
                                  {...field}
                                  onChange={(e) =>
                                    field.onChange(Number(e.target.value))
                                  }
                                />
                              </FormControl>
                              <FormDescription>
                                預設 5；給 OS boot 預留時間。
                              </FormDescription>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                        <FormField
                          control={form.control}
                          name="window_grace_period_minutes"
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>窗口結束緩衝 (分)</FormLabel>
                              <FormControl>
                                <Input
                                  type="number"
                                  min={0}
                                  max={240}
                                  {...field}
                                  onChange={(e) =>
                                    field.onChange(Number(e.target.value))
                                  }
                                />
                              </FormControl>
                              <FormDescription>
                                預設 30；課程結束多久後自動關機。
                              </FormDescription>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                        <FormField
                          control={form.control}
                          name="practice_session_hours"
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>學生練習配額 (小時)</FormLabel>
                              <FormControl>
                                <Input
                                  type="number"
                                  min={1}
                                  max={24}
                                  {...field}
                                  onChange={(e) =>
                                    field.onChange(Number(e.target.value))
                                  }
                                />
                              </FormControl>
                              <FormDescription>
                                預設 3；窗口外手動開機最多撐多久。
                              </FormDescription>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                        <FormField
                          control={form.control}
                          name="practice_warning_minutes"
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>關機前提示 (分)</FormLabel>
                              <FormControl>
                                <Input
                                  type="number"
                                  min={1}
                                  max={120}
                                  {...field}
                                  onChange={(e) =>
                                    field.onChange(Number(e.target.value))
                                  }
                                />
                              </FormControl>
                              <FormDescription>
                                預設 30；學生提前多久看到延長提示。
                              </FormDescription>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                      </div>
                    </div>

                    <Separator />

                    {/* ── 進階內部參數（Tier 2）── */}
                    <button
                      type="button"
                      onClick={() => setShowAdvanced(!showAdvanced)}
                      className="flex w-full items-center gap-2 rounded-lg border border-border/50 bg-muted/30 px-4 py-3 text-sm text-muted-foreground transition-colors hover:bg-muted/50"
                    >
                      <ChevronRight
                        className={cn(
                          "h-4 w-4 transition-transform",
                          showAdvanced && "rotate-90",
                        )}
                      />
                      <Settings2 className="h-4 w-4" />
                      <span>進階內部參數</span>
                      <span className="ml-auto text-xs opacity-60">
                        不建議輕易修改
                      </span>
                    </button>

                    {showAdvanced && (
                      <div className="mt-4 space-y-6">
                        <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-400/90">
                          <AlertTriangle className="mr-1.5 inline h-3.5 w-3.5" />
                          這些參數影響排程演算法的內部行為。不正確的設定可能導致分配品質下降。
                        </div>

                        {/* LXC Live Migration */}
                        <FormField
                          control={form.control}
                          name="migration_lxc_live_enabled"
                          render={({ field }) => (
                            <FormItem className="flex items-start justify-between rounded-lg border p-4">
                              <div className="space-y-1">
                                <FormLabel>LXC Live Migration</FormLabel>
                                <FormDescription>
                                  允許對正在運行的 LXC 容器執行 live
                                  migration。關閉時會等待容器停止或以 relocate
                                  方式搬移。
                                </FormDescription>
                              </div>
                              <FormControl>
                                <Switch
                                  checked={field.value}
                                  onCheckedChange={field.onChange}
                                  disabled={!migrationEnabledVal}
                                />
                              </FormControl>
                            </FormItem>
                          )}
                        />

                        {/* Migration Tuning */}
                        <div className="grid gap-4 lg:grid-cols-3">
                          <FormField
                            control={form.control}
                            name="migration_retry_limit"
                            render={({ field }) => (
                              <FormItem>
                                <FormLabel>Migration Retry Limit</FormLabel>
                                <FormControl>
                                  <Input
                                    type="number"
                                    min={0}
                                    max={10}
                                    {...field}
                                    disabled={!migrationEnabledVal}
                                    onChange={(e) =>
                                      field.onChange(Number(e.target.value))
                                    }
                                  />
                                </FormControl>
                                <FormDescription>
                                  0 代表失敗後持續留在 queue，等待下一輪再試。
                                </FormDescription>
                                <FormMessage />
                              </FormItem>
                            )}
                          />

                          <FormField
                            control={form.control}
                            name="rebalance_migration_cost"
                            render={({ field }) => (
                              <FormItem>
                                <FormLabel>Migration Cost</FormLabel>
                                <FormControl>
                                  <Input
                                    type="number"
                                    min={0}
                                    max={5}
                                    step={0.01}
                                    {...field}
                                    onChange={(e) =>
                                      field.onChange(Number(e.target.value))
                                    }
                                  />
                                </FormControl>
                                <FormDescription>
                                  數值越高，演算法越傾向維持既有 VM
                                  位置，降低重排搬移次數。
                                </FormDescription>
                                <FormMessage />
                              </FormItem>
                            )}
                          />

                          <FormField
                            control={form.control}
                            name="rebalance_loadavg_penalty_weight"
                            render={({ field }) => (
                              <FormItem>
                                <FormLabel>Loadavg Penalty Weight</FormLabel>
                                <FormControl>
                                  <Input
                                    type="number"
                                    min={0}
                                    max={5}
                                    step={0.1}
                                    {...field}
                                    onChange={(e) =>
                                      field.onChange(Number(e.target.value))
                                    }
                                  />
                                </FormControl>
                                <FormDescription>
                                  提高後會更積極避開近期負載偏高的節點。
                                </FormDescription>
                                <FormMessage />
                              </FormItem>
                            )}
                          />
                        </div>

                        {/* Peak / Loadavg Margins */}
                        <div className="grid gap-4 lg:grid-cols-4">
                          <FormField
                            control={form.control}
                            name="rebalance_peak_cpu_margin"
                            render={({ field }) => (
                              <FormItem>
                                <FormLabel>Peak CPU Margin</FormLabel>
                                <FormControl>
                                  <Input
                                    type="number"
                                    min={1}
                                    max={2}
                                    step={0.01}
                                    {...field}
                                    onChange={(e) =>
                                      field.onChange(Number(e.target.value))
                                    }
                                  />
                                </FormControl>
                                <FormDescription>
                                  估算尖峰 CPU 風險時套用的保守倍率。
                                </FormDescription>
                                <FormMessage />
                              </FormItem>
                            )}
                          />

                          <FormField
                            control={form.control}
                            name="rebalance_peak_memory_margin"
                            render={({ field }) => (
                              <FormItem>
                                <FormLabel>Peak Memory Margin</FormLabel>
                                <FormControl>
                                  <Input
                                    type="number"
                                    min={1}
                                    max={2}
                                    step={0.01}
                                    {...field}
                                    onChange={(e) =>
                                      field.onChange(Number(e.target.value))
                                    }
                                  />
                                </FormControl>
                                <FormDescription>
                                  估算尖峰 RAM 風險時套用的保守倍率。
                                </FormDescription>
                                <FormMessage />
                              </FormItem>
                            )}
                          />

                          <FormField
                            control={form.control}
                            name="rebalance_loadavg_warn_per_core"
                            render={({ field }) => (
                              <FormItem>
                                <FormLabel>Loadavg Warn/Core</FormLabel>
                                <FormControl>
                                  <Input
                                    type="number"
                                    min={0}
                                    max={4}
                                    step={0.05}
                                    {...field}
                                    onChange={(e) =>
                                      field.onChange(Number(e.target.value))
                                    }
                                  />
                                </FormControl>
                                <FormDescription>
                                  每核心 loadavg 超過此值後開始降權。
                                </FormDescription>
                                <FormMessage />
                              </FormItem>
                            )}
                          />

                          <FormField
                            control={form.control}
                            name="rebalance_loadavg_max_per_core"
                            render={({ field }) => (
                              <FormItem>
                                <FormLabel>Loadavg Max/Core</FormLabel>
                                <FormControl>
                                  <Input
                                    type="number"
                                    min={0.1}
                                    max={8}
                                    step={0.05}
                                    {...field}
                                    onChange={(e) =>
                                      field.onChange(Number(e.target.value))
                                    }
                                  />
                                </FormControl>
                                <FormDescription>
                                  達到此值時 loadavg penalty 視為滿額。
                                </FormDescription>
                                <FormMessage />
                              </FormItem>
                            )}
                          />
                        </div>
                      </div>
                    )}
                  </CardContent>
                </Card>

                <SaveButton />
              </div>
            </TabsContent>

            {/* ═══ 節點管理 ═══ */}
            <TabsContent value="nodes" className="mt-5">
              <Card>
                <CardHeader>
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <CardTitle className="flex items-center gap-2 text-base">
                        <Server className="h-4 w-4" />
                        節點管理
                        {!nodesLoading && nodes && (
                          <Badge variant="outline" className="ml-1">
                            {nodes.length} 台
                          </Badge>
                        )}
                      </CardTitle>
                      <CardDescription className="mt-1">
                        管理 Proxmox 叢集節點連線設定。點擊「同步節點」自動從
                        Proxmox 偵測所有節點與 Storage。
                      </CardDescription>
                    </div>
                    <div className="flex gap-2 shrink-0">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={
                          !config?.is_configured || testMutation.isPending
                        }
                        onClick={() => testMutation.mutate()}
                      >
                        {testMutation.isPending ? (
                          <RefreshCw className="mr-2 h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <ShieldCheck className="mr-2 h-3.5 w-3.5" />
                        )}
                        測試連線
                      </Button>
                      <Button
                        type="button"
                        disabled={
                          !config?.is_configured || syncNowMutation.isPending
                        }
                        size="sm"
                        onClick={() => syncNowMutation.mutate()}
                      >
                        {syncNowMutation.isPending ? (
                          <RefreshCw className="mr-2 h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <RefreshCw className="mr-2 h-3.5 w-3.5" />
                        )}
                        同步節點
                      </Button>
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="flex flex-col gap-4">
                  {testResult && (
                    <div
                      className={cn(
                        "flex items-start gap-2 rounded-md p-3 text-sm",
                        testResult.success
                          ? "bg-green-50 text-green-800 dark:bg-green-950 dark:text-green-200"
                          : "bg-red-50 text-red-800 dark:bg-red-950 dark:text-red-200",
                      )}
                    >
                      {testResult.success ? (
                        <CheckCircle className="mt-0.5 h-4 w-4 shrink-0" />
                      ) : (
                        <XCircle className="mt-0.5 h-4 w-4 shrink-0" />
                      )}
                      <span>{testResult.message}</span>
                    </div>
                  )}

                  {nodesLoading ? (
                    <div className="flex items-center gap-2 text-muted-foreground py-4">
                      <RefreshCw className="h-4 w-4 animate-spin" />
                      載入中...
                    </div>
                  ) : !nodes || nodes.length === 0 ? (
                    <div className="flex flex-col items-center justify-center py-8 gap-2 text-muted-foreground">
                      <Server className="h-8 w-8" />
                      <p>尚無節點資料，請點擊「同步節點」。</p>
                    </div>
                  ) : (
                    <div className="rounded-md border">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>節點名稱</TableHead>
                            <TableHead>主機位址</TableHead>
                            <TableHead>角色</TableHead>
                            <TableHead>狀態</TableHead>
                            <TableHead className="w-24">優先級</TableHead>
                            <TableHead className="w-16">操作</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {nodes.map((node) => (
                            <TableRow key={node.id ?? node.name}>
                              <TableCell className="font-medium">
                                {node.name}
                              </TableCell>
                              <TableCell className="text-muted-foreground font-mono text-sm">
                                {node.host}:{node.port}
                              </TableCell>
                              <TableCell>
                                {node.is_primary ? (
                                  <Badge variant="outline">主節點</Badge>
                                ) : (
                                  <span className="text-muted-foreground text-sm">
                                    副節點
                                  </span>
                                )}
                              </TableCell>
                              <TableCell>
                                <div className="flex items-center gap-1.5">
                                  {node.is_online ? (
                                    <Wifi className="h-3.5 w-3.5 text-green-500" />
                                  ) : (
                                    <WifiOff className="h-3.5 w-3.5 text-red-500" />
                                  )}
                                  <Badge
                                    variant={
                                      node.is_online ? "default" : "destructive"
                                    }
                                    className={cn(
                                      "text-xs",
                                      node.is_online &&
                                        "bg-green-500 hover:bg-green-600",
                                    )}
                                  >
                                    {node.is_online ? "在線" : "離線"}
                                  </Badge>
                                </div>
                              </TableCell>
                              <TableCell>
                                <Badge variant="secondary">
                                  {node.priority}
                                </Badge>
                              </TableCell>
                              <TableCell>
                                <Button
                                  type="button"
                                  variant="ghost"
                                  size="sm"
                                  onClick={() => openEditNode(node)}
                                >
                                  <Edit2 className="h-3.5 w-3.5" />
                                </Button>
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  )}
                </CardContent>
              </Card>
            </TabsContent>

            {/* ═══ Storage ═══ */}
            <TabsContent value="storage" className="mt-5">
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Database className="h-4 w-4" />
                    Storage 設定
                  </CardTitle>
                  <CardDescription>
                    設定各 Storage 的速度分級與優先級，供 VM 放置算法使用。
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <StorageTab />
                </CardContent>
              </Card>
            </TabsContent>
          </Tabs>
        </form>
      </Form>

      {/* Cluster confirm dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>偵測到叢集環境</DialogTitle>
            <DialogDescription>
              連線後偵測到以下 {previewResult?.nodes.length}{" "}
              個節點，確認後將儲存設定：
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 max-h-60 overflow-y-auto">
            {previewResult?.nodes.map((n) => (
              <div
                key={n.name}
                className="flex items-center justify-between rounded-md border p-2.5 text-sm"
              >
                <div>
                  <span className="font-medium">{n.name}</span>
                  {n.is_primary && (
                    <Badge variant="outline" className="ml-2 text-xs">
                      主
                    </Badge>
                  )}
                </div>
                <span className="font-mono text-muted-foreground">
                  {n.host}:{n.port}
                </span>
              </div>
            ))}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              取消
            </Button>
            <LoadingButton
              loading={isSaving}
              onClick={() =>
                pendingFormData &&
                saveMutation.mutate({ data: pendingFormData })
              }
            >
              確認儲存
            </LoadingButton>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Node edit sheet */}
      <Sheet
        open={!!editingNode}
        onOpenChange={(open) => !open && setEditingNode(null)}
      >
        <SheetContent>
          <SheetHeader>
            <SheetTitle>編輯節點：{editingNode?.name}</SheetTitle>
            <SheetDescription>
              修改節點的連線資訊與優先級設定。節點名稱由 Proxmox
              決定，無法修改。
            </SheetDescription>
          </SheetHeader>
          <Form {...nodeForm}>
            <form
              onSubmit={nodeForm.handleSubmit((data) => {
                if (!editingNode?.id) return
                updateNodeMutation.mutate({
                  id: editingNode.id,
                  body: {
                    host: data.host,
                    port: data.port,
                    priority: data.priority,
                  },
                })
              })}
              className="flex flex-col gap-4 mt-6"
            >
              <FormField
                control={nodeForm.control}
                name="host"
                rules={{ required: "請輸入主機位址" }}
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>主機位址</FormLabel>
                    <FormControl>
                      <Input placeholder="192.168.1.100" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={nodeForm.control}
                name="port"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>API 埠號</FormLabel>
                    <FormControl>
                      <Input
                        type="number"
                        min={1}
                        max={65535}
                        {...field}
                        onChange={(e) => field.onChange(Number(e.target.value))}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={nodeForm.control}
                name="priority"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>優先級（1=最高，10=最低）</FormLabel>
                    <FormControl>
                      <Input
                        type="number"
                        min={1}
                        max={10}
                        {...field}
                        onChange={(e) => field.onChange(Number(e.target.value))}
                      />
                    </FormControl>
                    <FormDescription>
                      VM 放置算法會優先考慮優先級數字較小的節點
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <SheetFooter className="mt-4">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setEditingNode(null)}
                >
                  取消
                </Button>
                <LoadingButton
                  type="submit"
                  loading={updateNodeMutation.isPending}
                >
                  儲存
                </LoadingButton>
              </SheetFooter>
            </form>
          </Form>
        </SheetContent>
      </Sheet>
    </div>
  )
}
