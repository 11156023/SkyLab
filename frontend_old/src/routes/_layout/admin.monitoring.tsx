import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import {
  Activity,
  AlertTriangle,
  Bell,
  BellOff,
  Check,
  ChevronDown,
  ChevronRight,
  Cpu,
  Gauge,
  HardDrive,
  Loader2,
  MemoryStick,
  Server,
} from "lucide-react"
import { Fragment, useState } from "react"
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import {
  type AlertEventPublic,
  MonitoringService,
  type NodeMetrics,
  type VmTopEntry,
} from "@/client"
import MiningIncidentsPanel from "@/components/Admin/MiningIncidentsPanel"
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
import { requireAdminUser } from "@/features/auth/guards"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/_layout/admin/monitoring")({
  beforeLoad: () => requireAdminUser(),
  component: MonitoringPage,
})

const CHART_COLORS = {
  cpu: "#3b82f6",
  mem: "#10b981",
}

const TOOLTIP_STYLE = {
  borderRadius: "8px",
  border: "1px solid hsl(var(--border))",
  background: "hsl(var(--card))",
  color: "hsl(var(--card-foreground))",
  fontSize: 12,
}

const AXIS_TICK = { fontSize: 11, fill: "hsl(var(--muted-foreground))" }

const METRIC_LABELS: Record<string, string> = {
  cpu: "CPU",
  memory: "記憶體",
  disk: "磁碟",
}

const SCOPE_LABELS: Record<string, string> = {
  cluster: "叢集",
  node: "節點",
  vm: "VM",
}

function formatBytes(bytes: number): string {
  if (!bytes) return "0 B"
  const tb = bytes / 1024 ** 4
  if (tb >= 1) return `${tb.toFixed(2)} TB`
  const gb = bytes / 1024 ** 3
  if (gb >= 1) return `${gb.toFixed(1)} GB`
  const mb = bytes / 1024 ** 2
  return `${mb.toFixed(0)} MB`
}

function formatUptime(seconds: number): string {
  if (!seconds) return "—"
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  if (days > 0) return `${days} 天 ${hours} 小時`
  const minutes = Math.floor((seconds % 3600) / 60)
  return `${hours} 小時 ${minutes} 分`
}

function pctBarColor(pct: number): string {
  if (pct >= 90) return "bg-destructive"
  if (pct >= 70) return "bg-amber-500"
  return "bg-blue-500"
}

function UsageBar({ pct }: { pct: number }) {
  return (
    <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
      <div
        className={cn("h-full rounded-full transition-all", pctBarColor(pct))}
        style={{ width: `${Math.min(pct, 100)}%` }}
      />
    </div>
  )
}

interface OverviewCardProps {
  title: string
  icon: React.ReactNode
  pct: number
  detail: string
}

function OverviewCard({ title, icon, pct, detail }: OverviewCardProps) {
  return (
    <Card>
      <CardContent className="pt-5">
        <div className="flex items-start justify-between">
          <div>
            <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">
              {title}
            </p>
            <p className="text-3xl font-bold mt-1 leading-none">
              {pct.toFixed(1)}
              <span className="text-lg text-muted-foreground font-normal">
                %
              </span>
            </p>
            <p className="text-xs text-muted-foreground mt-1">{detail}</p>
          </div>
          {icon}
        </div>
        <div className="mt-3">
          <UsageBar pct={pct} />
        </div>
      </CardContent>
    </Card>
  )
}

function NodeRrdChart({
  node,
  timeframe,
}: {
  node: string
  timeframe: string
}) {
  const { data: rrd, isLoading } = useQuery({
    queryKey: ["nodeRrd", node, timeframe],
    queryFn: () => MonitoringService.getNodeRrd({ node, timeframe }),
    refetchInterval: 60000,
  })

  if (isLoading) {
    return (
      <div className="flex h-52 items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    )
  }

  const chartData = (rrd ?? [])
    .filter((p) => typeof p.time === "number")
    .map((p) => ({
      time: new Date((p.time as number) * 1000).toLocaleTimeString("zh-TW", {
        hour: "2-digit",
        minute: "2-digit",
      }),
      cpu: typeof p.cpu === "number" ? Number((p.cpu * 100).toFixed(2)) : null,
      memory:
        typeof p.memused === "number" &&
        typeof p.memtotal === "number" &&
        p.memtotal > 0
          ? Number(((p.memused / p.memtotal) * 100).toFixed(2))
          : null,
    }))

  return (
    <div className="grid gap-4 md:grid-cols-2">
      {(
        [
          { key: "cpu", label: "CPU %", color: CHART_COLORS.cpu },
          { key: "memory", label: "記憶體 %", color: CHART_COLORS.mem },
        ] as const
      ).map(({ key, label, color }) => (
        <div key={key}>
          <p className="mb-1 text-xs font-medium text-muted-foreground">
            {label}
          </p>
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart
              data={chartData}
              margin={{ top: 8, right: 16, left: 0, bottom: 0 }}
            >
              <defs>
                <linearGradient
                  id={`grad-${node}-${key}`}
                  x1="0"
                  y1="0"
                  x2="0"
                  y2="1"
                >
                  <stop offset="5%" stopColor={color} stopOpacity={0.3} />
                  <stop offset="95%" stopColor={color} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid
                strokeDasharray="3 3"
                stroke="hsl(var(--border))"
                vertical={false}
              />
              <XAxis
                dataKey="time"
                tick={AXIS_TICK}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                domain={[0, (max: number) => Math.max(Math.ceil(max * 1.3), 1)]}
                tickFormatter={(v) => `${v}%`}
                tick={AXIS_TICK}
                axisLine={false}
                tickLine={false}
                width={44}
              />
              <Tooltip
                contentStyle={TOOLTIP_STYLE}
                formatter={(v) => [`${Number(v).toFixed(2)}%`, label]}
                cursor={{
                  stroke: "hsl(var(--border))",
                  strokeDasharray: "4 4",
                }}
              />
              <Area
                type="monotone"
                dataKey={key}
                stroke={color}
                strokeWidth={2}
                fill={`url(#grad-${node}-${key})`}
                dot={false}
                activeDot={{ r: 4 }}
                name={label}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      ))}
    </div>
  )
}

function NodeRow({
  node,
  expanded,
  onToggle,
  timeframe,
}: {
  node: NodeMetrics
  expanded: boolean
  onToggle: () => void
  timeframe: string
}) {
  const online = node.status === "online"
  const cpuPct = node.maxcpu > 0 ? node.cpu * 100 : 0
  const memPct = node.maxmem > 0 ? (node.mem / node.maxmem) * 100 : 0
  const diskPct = node.maxdisk > 0 ? (node.disk / node.maxdisk) * 100 : 0

  return (
    <Fragment>
      <TableRow className="cursor-pointer hover:bg-muted/50" onClick={onToggle}>
        <TableCell>
          <div className="flex items-center gap-2">
            {expanded ? (
              <ChevronDown className="h-4 w-4 text-muted-foreground" />
            ) : (
              <ChevronRight className="h-4 w-4 text-muted-foreground" />
            )}
            <Server className="h-4 w-4 text-muted-foreground" />
            <span className="font-medium">{node.node}</span>
          </div>
        </TableCell>
        <TableCell>
          <Badge variant={online ? "default" : "destructive"}>
            {online ? "在線" : node.status}
          </Badge>
        </TableCell>
        <TableCell>
          <div className="space-y-1">
            <div className="flex justify-between text-xs">
              <span>{cpuPct.toFixed(1)}%</span>
              <span className="text-muted-foreground">{node.maxcpu} 核心</span>
            </div>
            <UsageBar pct={cpuPct} />
          </div>
        </TableCell>
        <TableCell>
          <div className="space-y-1">
            <div className="flex justify-between text-xs">
              <span>{memPct.toFixed(1)}%</span>
              <span className="text-muted-foreground">
                {formatBytes(node.mem)} / {formatBytes(node.maxmem)}
              </span>
            </div>
            <UsageBar pct={memPct} />
          </div>
        </TableCell>
        <TableCell>
          <div className="space-y-1">
            <div className="flex justify-between text-xs">
              <span>{diskPct.toFixed(1)}%</span>
              <span className="text-muted-foreground">
                {formatBytes(node.disk)} / {formatBytes(node.maxdisk)}
              </span>
            </div>
            <UsageBar pct={diskPct} />
          </div>
        </TableCell>
        <TableCell className="text-sm text-muted-foreground">
          {formatUptime(node.uptime)}
        </TableCell>
      </TableRow>
      {expanded && (
        <TableRow>
          <TableCell colSpan={6} className="bg-muted/30 p-4">
            <NodeRrdChart node={node.node} timeframe={timeframe} />
          </TableCell>
        </TableRow>
      )}
    </Fragment>
  )
}

function TopVmTable({
  title,
  entries,
  metric,
}: {
  title: string
  entries: VmTopEntry[]
  metric: "cpu" | "mem"
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {entries.length === 0 ? (
          <p className="py-4 text-center text-sm text-muted-foreground">
            無運行中的資源
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>VMID</TableHead>
                <TableHead>名稱</TableHead>
                <TableHead>節點</TableHead>
                <TableHead>類型</TableHead>
                <TableHead className="text-right">
                  {metric === "cpu" ? "CPU" : "記憶體"}
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {entries.map((vm) => (
                <TableRow key={vm.vmid}>
                  <TableCell className="font-mono text-sm">{vm.vmid}</TableCell>
                  <TableCell className="max-w-40 truncate">{vm.name}</TableCell>
                  <TableCell className="text-muted-foreground">
                    {vm.node}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline">
                      {vm.type === "qemu" ? "VM" : "LXC"}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right font-mono text-sm">
                    {metric === "cpu"
                      ? `${(vm.cpu * 100).toFixed(1)}%`
                      : formatBytes(vm.mem)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}

function AlertsPanel() {
  const queryClient = useQueryClient()

  const { data: alerts, isLoading } = useQuery({
    queryKey: ["monitoringAlerts"],
    queryFn: () => MonitoringService.listAlerts({ active: true }),
    refetchInterval: 30000,
  })

  const ackMutation = useMutation({
    mutationFn: (alertId: string) =>
      MonitoringService.acknowledgeAlert({ alertId }),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["monitoringAlerts"] })
    },
  })

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-base flex items-center gap-2">
              <Bell className="h-4 w-4" />
              活動告警
            </CardTitle>
            <CardDescription>
              超過閾值的資源使用告警（每 30 秒更新）
            </CardDescription>
          </div>
          {alerts && alerts.length > 0 && (
            <Badge variant="destructive">{alerts.length}</Badge>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="flex justify-center py-6">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : !alerts || alerts.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-6 text-muted-foreground">
            <BellOff className="h-6 w-6" />
            <p className="text-sm">目前沒有活動告警</p>
          </div>
        ) : (
          <div className="space-y-2">
            {alerts.map((alert: AlertEventPublic) => (
              <div
                key={alert.id}
                className="flex items-center justify-between gap-3 rounded-lg border p-3"
              >
                <div className="flex items-start gap-3">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
                  <div>
                    <div className="flex flex-wrap items-center gap-1.5 text-sm font-medium">
                      <Badge variant="outline">
                        {SCOPE_LABELS[alert.scope] ?? alert.scope}
                      </Badge>
                      <span>{alert.target}</span>
                      <span className="text-muted-foreground">·</span>
                      <span>
                        {METRIC_LABELS[alert.metric] ?? alert.metric}{" "}
                        {alert.value.toFixed(0)}%
                      </span>
                      <span className="text-xs text-muted-foreground">
                        （閾值 {alert.threshold.toFixed(0)}%）
                      </span>
                    </div>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {new Date(alert.created_at).toLocaleString("zh-TW")}
                      {alert.acknowledged_at && " · 已確認"}
                    </p>
                  </div>
                </div>
                {!alert.acknowledged_at && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={ackMutation.isPending}
                    onClick={() => ackMutation.mutate(alert.id)}
                  >
                    <Check className="mr-1 h-3.5 w-3.5" />
                    確認
                  </Button>
                )}
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function MonitoringPage() {
  const [timeframe, setTimeframe] = useState("hour")
  const [expandedNode, setExpandedNode] = useState<string | null>(null)

  const {
    data: overview,
    isLoading,
    isError,
  } = useQuery({
    queryKey: ["monitoringOverview"],
    queryFn: () => MonitoringService.getOverview(),
    refetchInterval: 30000,
  })

  if (isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (isError || !overview) {
    return (
      <div className="container mx-auto py-6">
        <Card>
          <CardContent className="flex flex-col items-center gap-2 py-10 text-muted-foreground">
            <AlertTriangle className="h-6 w-6" />
            <p>無法取得監控資料，請確認 Proxmox 連線狀態。</p>
          </CardContent>
        </Card>
      </div>
    )
  }

  const cpuPct =
    overview.cpu_total > 0 ? (overview.cpu_used / overview.cpu_total) * 100 : 0
  const memPct =
    overview.mem_total > 0 ? (overview.mem_used / overview.mem_total) * 100 : 0
  const diskPct =
    overview.disk_total > 0
      ? (overview.disk_used / overview.disk_total) * 100
      : 0

  return (
    <div className="container mx-auto space-y-6 py-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold">
            <Gauge className="h-6 w-6" />
            資源監控
          </h1>
          <p className="text-sm text-muted-foreground">
            叢集資源使用、節點趨勢與閾值告警
          </p>
        </div>
        <Select value={timeframe} onValueChange={setTimeframe}>
          <SelectTrigger className="w-36">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="hour">最近 1 小時</SelectItem>
            <SelectItem value="day">最近 1 天</SelectItem>
            <SelectItem value="week">最近 1 週</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Overview cards */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <OverviewCard
          title="CPU 用量"
          pct={cpuPct}
          detail={`${overview.cpu_used.toFixed(1)} / ${overview.cpu_total} 核心`}
          icon={
            <div className="rounded-full bg-blue-100 p-2 dark:bg-blue-900/30">
              <Cpu className="h-4 w-4 text-blue-600" />
            </div>
          }
        />
        <OverviewCard
          title="記憶體用量"
          pct={memPct}
          detail={`${formatBytes(overview.mem_used)} / ${formatBytes(overview.mem_total)}`}
          icon={
            <div className="rounded-full bg-emerald-100 p-2 dark:bg-emerald-900/30">
              <MemoryStick className="h-4 w-4 text-emerald-600" />
            </div>
          }
        />
        <OverviewCard
          title="磁碟用量"
          pct={diskPct}
          detail={`${formatBytes(overview.disk_used)} / ${formatBytes(overview.disk_total)}`}
          icon={
            <div className="rounded-full bg-amber-100 p-2 dark:bg-amber-900/30">
              <HardDrive className="h-4 w-4 text-amber-600" />
            </div>
          }
        />
        <Card>
          <CardContent className="pt-5">
            <div className="flex items-start justify-between">
              <div>
                <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">
                  運行狀態
                </p>
                <p className="mt-2 text-sm">
                  節點在線{" "}
                  <span className="font-semibold">
                    {overview.nodes_online}/{overview.nodes_total}
                  </span>
                </p>
                <p className="text-sm">
                  VM 運行{" "}
                  <span className="font-semibold">{overview.vms_running}</span>
                  <span className="text-muted-foreground">
                    /{overview.vms_running + overview.vms_stopped}
                  </span>
                </p>
                <p className="text-sm">
                  LXC 運行{" "}
                  <span className="font-semibold">{overview.lxc_running}</span>
                  <span className="text-muted-foreground">
                    /{overview.lxc_running + overview.lxc_stopped}
                  </span>
                </p>
              </div>
              <div className="rounded-full bg-violet-100 p-2 dark:bg-violet-900/30">
                <Activity className="h-4 w-4 text-violet-600" />
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Alerts */}
      <AlertsPanel />

      {/* Mining incidents */}
      <MiningIncidentsPanel />

      {/* Node table */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">節點用量</CardTitle>
          <CardDescription>點擊節點列展開使用趨勢圖</CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>節點</TableHead>
                <TableHead>狀態</TableHead>
                <TableHead className="w-[18%]">CPU</TableHead>
                <TableHead className="w-[22%]">記憶體</TableHead>
                <TableHead className="w-[22%]">磁碟</TableHead>
                <TableHead>運行時間</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {overview.nodes.map((node) => (
                <NodeRow
                  key={node.node}
                  node={node}
                  timeframe={timeframe}
                  expanded={expandedNode === node.node}
                  onToggle={() =>
                    setExpandedNode(
                      expandedNode === node.node ? null : node.node,
                    )
                  }
                />
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Top VMs */}
      <div className="grid gap-4 lg:grid-cols-2">
        <TopVmTable
          title="CPU 用量 Top 5"
          entries={overview.top_cpu}
          metric="cpu"
        />
        <TopVmTable
          title="記憶體用量 Top 5"
          entries={overview.top_mem}
          metric="mem"
        />
      </div>
    </div>
  )
}
