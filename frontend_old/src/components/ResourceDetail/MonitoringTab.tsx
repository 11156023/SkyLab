import { useSuspenseQuery } from "@tanstack/react-query"
import { Cpu, HardDrive, MemoryStick, Network } from "lucide-react"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import { ResourceDetailsService } from "@/client"
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { cn } from "@/lib/utils"

// 與 admin/configuration 頁面共用的色系
const CHART_COLORS = {
  cpu: "#3b82f6",
  mem: "#10b981",
  disk: "#f59e0b",
  netin: "#3b82f6",
  netout: "#ef4444",
}

const TOOLTIP_STYLE = {
  borderRadius: "8px",
  border: "1px solid hsl(var(--border))",
  background: "hsl(var(--card))",
  color: "hsl(var(--card-foreground))",
  fontSize: 12,
}

const AXIS_TICK = { fontSize: 11, fill: "hsl(var(--muted-foreground))" }

/** 根據最大值決定 Y 軸 domain 上限（保留小數精度） */
function pctDomainMax(max: number): number {
  if (max === 0) return 1
  const ceiling = max * 1.3
  // 依量級決定上限精度
  if (ceiling >= 10) return Math.ceil(ceiling)
  if (ceiling >= 1) return Math.ceil(ceiling * 10) / 10 // 一位小數
  return Math.ceil(ceiling * 100) / 100 // 兩位小數
}

/** 根據 domain 上限決定適當的百分比顯示格式 */
function pctFormatter(domainMax: number) {
  return (v: number) => {
    if (domainMax >= 10) return `${v}%`
    if (domainMax >= 1) return `${v.toFixed(1)}%`
    return `${v.toFixed(2)}%`
  }
}

interface MonitoringTabProps {
  vmid: number
}

export default function MonitoringTab({ vmid }: MonitoringTabProps) {
  const { t } = useTranslation("resourceDetail")
  const [timeframe, setTimeframe] = useState<string>("hour")

  const { data: currentStats } = useSuspenseQuery({
    queryKey: ["currentStats", vmid],
    queryFn: () => ResourceDetailsService.getCurrentStats({ vmid }),
    refetchInterval: 5000,
  })

  const { data: rrdData } = useSuspenseQuery({
    queryKey: ["rrdStats", vmid, timeframe],
    queryFn: () => ResourceDetailsService.getRrdStats({ vmid, timeframe }),
    refetchInterval: 30000,
  })

  const formatBytes = (bytes: number | null | undefined) => {
    if (!bytes) return "0 B"
    const gb = bytes / 1024 / 1024 / 1024
    if (gb >= 1) return `${gb.toFixed(2)} GB`
    const mb = bytes / 1024 / 1024
    return `${mb.toFixed(2)} MB`
  }

  const formatTimestamp = (timestamp: number) => {
    return new Date(timestamp * 1000).toLocaleTimeString("zh-TW", {
      hour: "2-digit",
      minute: "2-digit",
    })
  }

  const cpuPct = currentStats.cpu ? (currentStats.cpu * 100).toFixed(2) : "0.00"
  const memPct =
    currentStats.mem && currentStats.maxmem
      ? ((currentStats.mem / currentStats.maxmem) * 100).toFixed(2)
      : "0.00"
  const diskPct =
    currentStats.disk && currentStats.maxdisk
      ? ((currentStats.disk / currentStats.maxdisk) * 100).toFixed(2)
      : "0.00"

  const cpuPctNum = parseFloat(cpuPct)
  const memPctNum = parseFloat(memPct)
  const diskPctNum = parseFloat(diskPct)

  const chartData = (rrdData?.data ?? []).map((point) => ({
    time: formatTimestamp(point.time),
    timestamp: point.time,
    cpu: point.cpu ? Number((point.cpu * 100).toFixed(2)) : null,
    memory:
      point.mem && point.maxmem
        ? Number(((point.mem / point.maxmem) * 100).toFixed(2))
        : null,
    memoryGB: point.mem
      ? Number((point.mem / 1024 / 1024 / 1024).toFixed(2))
      : null,
    disk:
      point.disk && point.maxdisk
        ? Number(((point.disk / point.maxdisk) * 100).toFixed(2))
        : null,
    diskGB: point.disk
      ? Number((point.disk / 1024 / 1024 / 1024).toFixed(2))
      : null,
    // 存 KB，讓圖表自動縮放；顯示時再決定單位
    netinKB: point.netin ? Number((point.netin / 1024).toFixed(2)) : null,
    netoutKB: point.netout ? Number((point.netout / 1024).toFixed(2)) : null,
  }))

  // 決定網路圖表的顯示單位（KB 或 MB）
  const maxNetKB = Math.max(
    ...chartData.map((d) => Math.max(d.netinKB ?? 0, d.netoutKB ?? 0)),
    0,
  )
  const useNetMB = maxNetKB >= 500
  const netDivisor = useNetMB ? 1024 : 1
  const netUnit = useNetMB ? "MB" : "KB"
  const netChartData = chartData.map((d) => ({
    ...d,
    netin:
      d.netinKB != null ? Number((d.netinKB / netDivisor).toFixed(2)) : null,
    netout:
      d.netoutKB != null ? Number((d.netoutKB / netDivisor).toFixed(2)) : null,
  }))

  // 計算各圖表的 Y 軸上限（自適應精度）
  const cpuMax = pctDomainMax(Math.max(...chartData.map((d) => d.cpu ?? 0), 0))
  const memMax = pctDomainMax(
    Math.max(...chartData.map((d) => d.memory ?? 0), 0),
  )

  return (
    <div className="space-y-6">
      {/* Timeframe Selector */}
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold">{t("monitoring.title")}</h3>
        <Select value={timeframe} onValueChange={setTimeframe}>
          <SelectTrigger className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="hour">{t("monitoring.hour")}</SelectItem>
            <SelectItem value="day">{t("monitoring.day")}</SelectItem>
            <SelectItem value="week">{t("monitoring.week")}</SelectItem>
            <SelectItem value="month">{t("monitoring.month")}</SelectItem>
            <SelectItem value="year">{t("monitoring.year")}</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Current Stats Cards */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {/* CPU */}
        <Card>
          <CardContent className="pt-5">
            <div className="flex items-start justify-between">
              <div>
                <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">
                  CPU {t("monitoring.usage")}
                </p>
                <p className="text-3xl font-bold mt-1 leading-none">
                  {cpuPct}
                  <span className="text-lg text-muted-foreground font-normal">
                    %
                  </span>
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  {currentStats.maxcpu} {t("overview.cores")}
                </p>
              </div>
              <div className="rounded-full p-2 bg-blue-100 dark:bg-blue-900/30">
                <Cpu className="h-4 w-4 text-blue-600" />
              </div>
            </div>
            <div className="mt-3 h-1.5 rounded-full bg-muted overflow-hidden">
              <div
                className={cn(
                  "h-full rounded-full transition-all",
                  cpuPctNum >= 90
                    ? "bg-destructive"
                    : cpuPctNum >= 70
                      ? "bg-amber-500"
                      : "bg-blue-500",
                )}
                style={{ width: `${cpuPct}%` }}
              />
            </div>
          </CardContent>
        </Card>

        {/* Memory */}
        <Card>
          <CardContent className="pt-5">
            <div className="flex items-start justify-between">
              <div>
                <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">
                  {t("overview.memory")}
                </p>
                <p className="text-3xl font-bold mt-1 leading-none">
                  {memPct}
                  <span className="text-lg text-muted-foreground font-normal">
                    %
                  </span>
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  {formatBytes(currentStats.mem)} /{" "}
                  {formatBytes(currentStats.maxmem)}
                </p>
              </div>
              <div className="rounded-full p-2 bg-emerald-100 dark:bg-emerald-900/30">
                <MemoryStick className="h-4 w-4 text-emerald-600" />
              </div>
            </div>
            <div className="mt-3 h-1.5 rounded-full bg-muted overflow-hidden">
              <div
                className={cn(
                  "h-full rounded-full transition-all",
                  memPctNum >= 90
                    ? "bg-destructive"
                    : memPctNum >= 70
                      ? "bg-amber-500"
                      : "bg-emerald-500",
                )}
                style={{ width: `${memPct}%` }}
              />
            </div>
          </CardContent>
        </Card>

        {/* Disk */}
        <Card>
          <CardContent className="pt-5">
            <div className="flex items-start justify-between">
              <div>
                <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">
                  {t("monitoring.disk")}
                </p>
                <p className="text-3xl font-bold mt-1 leading-none">
                  {diskPct}
                  <span className="text-lg text-muted-foreground font-normal">
                    %
                  </span>
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  {formatBytes(currentStats.disk)} /{" "}
                  {formatBytes(currentStats.maxdisk)}
                </p>
              </div>
              <div className="rounded-full p-2 bg-amber-100 dark:bg-amber-900/30">
                <HardDrive className="h-4 w-4 text-amber-600" />
              </div>
            </div>
            <div className="mt-3 h-1.5 rounded-full bg-muted overflow-hidden">
              <div
                className={cn(
                  "h-full rounded-full transition-all",
                  diskPctNum >= 90
                    ? "bg-destructive"
                    : diskPctNum >= 70
                      ? "bg-amber-500"
                      : "bg-amber-400",
                )}
                style={{ width: `${diskPct}%` }}
              />
            </div>
          </CardContent>
        </Card>

        {/* Network */}
        <Card>
          <CardContent className="pt-5">
            <div className="flex items-start justify-between">
              <div>
                <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">
                  {t("monitoring.network")}
                </p>
                <p className="text-sm font-semibold mt-2">
                  ↓ {formatBytes(currentStats.netin)}
                </p>
                <p className="text-sm font-semibold">
                  ↑ {formatBytes(currentStats.netout)}
                </p>
              </div>
              <div className="rounded-full p-2 bg-violet-100 dark:bg-violet-900/30">
                <Network className="h-4 w-4 text-violet-600" />
              </div>
            </div>
            <div className="mt-3 flex gap-1 h-1.5">
              <div className="flex-1 rounded-full bg-blue-500 opacity-60" />
              <div className="flex-1 rounded-full bg-red-500 opacity-60" />
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Historical Charts */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">
            {t("monitoring.historicalData")}
          </CardTitle>
          <CardDescription>
            {t("monitoring.showing")} {rrdData?.data.length || 0}{" "}
            {t("monitoring.dataPoints")}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Tabs defaultValue="cpu" className="space-y-4">
            <TabsList>
              <TabsTrigger value="cpu">CPU</TabsTrigger>
              <TabsTrigger value="memory">{t("overview.memory")}</TabsTrigger>
              <TabsTrigger value="network">
                {t("monitoring.network")}
              </TabsTrigger>
            </TabsList>

            {/* CPU Chart */}
            <TabsContent value="cpu">
              <ResponsiveContainer width="100%" height={260}>
                <AreaChart
                  data={chartData}
                  margin={{ top: 8, right: 16, left: 0, bottom: 0 }}
                >
                  <defs>
                    <linearGradient id="gradCpu" x1="0" y1="0" x2="0" y2="1">
                      <stop
                        offset="5%"
                        stopColor={CHART_COLORS.cpu}
                        stopOpacity={0.3}
                      />
                      <stop
                        offset="95%"
                        stopColor={CHART_COLORS.cpu}
                        stopOpacity={0}
                      />
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
                    domain={[0, cpuMax]}
                    tickFormatter={pctFormatter(cpuMax)}
                    tick={AXIS_TICK}
                    axisLine={false}
                    tickLine={false}
                    width={52}
                  />
                  <Tooltip
                    contentStyle={TOOLTIP_STYLE}
                    formatter={(v) => [`${Number(v).toFixed(2)}%`, "CPU"]}
                    cursor={{
                      stroke: "hsl(var(--border))",
                      strokeDasharray: "4 4",
                    }}
                  />
                  <Area
                    type="monotone"
                    dataKey="cpu"
                    stroke={CHART_COLORS.cpu}
                    strokeWidth={2}
                    fill="url(#gradCpu)"
                    dot={false}
                    activeDot={{ r: 4 }}
                    name="CPU %"
                  />
                </AreaChart>
              </ResponsiveContainer>
            </TabsContent>

            {/* Memory Chart */}
            <TabsContent value="memory">
              <ResponsiveContainer width="100%" height={260}>
                <AreaChart
                  data={chartData}
                  margin={{ top: 8, right: 16, left: 0, bottom: 0 }}
                >
                  <defs>
                    <linearGradient id="gradMem" x1="0" y1="0" x2="0" y2="1">
                      <stop
                        offset="5%"
                        stopColor={CHART_COLORS.mem}
                        stopOpacity={0.3}
                      />
                      <stop
                        offset="95%"
                        stopColor={CHART_COLORS.mem}
                        stopOpacity={0}
                      />
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
                    domain={[0, memMax]}
                    tickFormatter={pctFormatter(memMax)}
                    tick={AXIS_TICK}
                    axisLine={false}
                    tickLine={false}
                    width={52}
                  />
                  <Tooltip
                    contentStyle={TOOLTIP_STYLE}
                    formatter={(v) => [`${Number(v).toFixed(2)}%`, "記憶體"]}
                    cursor={{
                      stroke: "hsl(var(--border))",
                      strokeDasharray: "4 4",
                    }}
                  />
                  <Area
                    type="monotone"
                    dataKey="memory"
                    stroke={CHART_COLORS.mem}
                    strokeWidth={2}
                    fill="url(#gradMem)"
                    dot={false}
                    activeDot={{ r: 4 }}
                    name="記憶體 %"
                  />
                </AreaChart>
              </ResponsiveContainer>
            </TabsContent>

            {/* Network Chart */}
            <TabsContent value="network">
              <ResponsiveContainer width="100%" height={260}>
                <LineChart
                  data={netChartData}
                  margin={{ top: 8, right: 16, left: 0, bottom: 0 }}
                >
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
                    domain={[0, (max: number) => Math.max(max * 1.3, 0.1)]}
                    tickFormatter={(v) => `${v}${netUnit}`}
                    tick={AXIS_TICK}
                    axisLine={false}
                    tickLine={false}
                    width={54}
                  />
                  <Tooltip
                    contentStyle={TOOLTIP_STYLE}
                    formatter={(v, name) => [`${v} ${netUnit}`, name]}
                    cursor={{
                      stroke: "hsl(var(--border))",
                      strokeDasharray: "4 4",
                    }}
                  />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Line
                    type="monotone"
                    dataKey="netin"
                    stroke={CHART_COLORS.netin}
                    strokeWidth={2}
                    name={t("monitoring.in")}
                    dot={false}
                    activeDot={{ r: 4 }}
                  />
                  <Line
                    type="monotone"
                    dataKey="netout"
                    stroke={CHART_COLORS.netout}
                    strokeWidth={2}
                    name={t("monitoring.out")}
                    dot={false}
                    activeDot={{ r: 4 }}
                  />
                </LineChart>
              </ResponsiveContainer>
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>
    </div>
  )
}
