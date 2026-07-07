import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  Bell,
  Camera,
  Clock,
  Layers,
  Loader2,
  MoonStar,
  Pickaxe,
  Save,
  Wand2,
} from "lucide-react"
import { useForm } from "react-hook-form"
import { toast } from "sonner"

import { type GovernanceConfigUpdate, GovernanceService } from "@/client"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
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
import { Switch } from "@/components/ui/switch"

interface GovernanceFormValues {
  alerts_enabled: boolean
  alert_cpu_threshold: number
  alert_memory_threshold: number
  alert_disk_threshold: number
  alert_cooldown_minutes: number
  alert_check_interval_seconds: number
  alert_email_enabled: boolean
  ttl_enabled: boolean
  expiry_warn_days: number
  expiry_grace_delete_days: number
  idle_detection_enabled: boolean
  idle_cpu_threshold_percent: number
  idle_window_hours: number
  idle_grace_hours: number
  idle_scan_batch_size: number
  workload_advisor_enabled: boolean
  mining_detection_enabled: boolean
  mining_cpu_threshold_percent: number
  mining_window_hours: number
  mining_scan_batch_size: number
  mining_auto_suspend: boolean
  provision_max_concurrency: number
  snapshot_cleanup_enabled: boolean
  snapshot_retention_days: number
  student_snapshot_max_count: number
}

function getApiErrorMessage(error: unknown): string {
  if (error && typeof error === "object") {
    const maybe = error as { detail?: string; message?: string }
    if (typeof maybe.detail === "string") return maybe.detail
    if (typeof maybe.message === "string") return maybe.message
  }
  return "未知錯誤"
}

export default function GovernanceConfigTab() {
  const queryClient = useQueryClient()

  const { data: config, isLoading } = useQuery({
    queryKey: ["governanceConfig"],
    queryFn: () => GovernanceService.getConfig(),
  })

  const form = useForm<GovernanceFormValues>({
    values: config
      ? {
          alerts_enabled: config.alerts_enabled,
          alert_cpu_threshold: config.alert_cpu_threshold,
          alert_memory_threshold: config.alert_memory_threshold,
          alert_disk_threshold: config.alert_disk_threshold,
          alert_cooldown_minutes: config.alert_cooldown_minutes,
          alert_check_interval_seconds: config.alert_check_interval_seconds,
          alert_email_enabled: config.alert_email_enabled,
          ttl_enabled: config.ttl_enabled,
          expiry_warn_days: config.expiry_warn_days,
          expiry_grace_delete_days: config.expiry_grace_delete_days,
          idle_detection_enabled: config.idle_detection_enabled,
          idle_cpu_threshold_percent: config.idle_cpu_threshold_percent,
          idle_window_hours: config.idle_window_hours,
          idle_grace_hours: config.idle_grace_hours,
          idle_scan_batch_size: config.idle_scan_batch_size,
          workload_advisor_enabled: config.workload_advisor_enabled,
          mining_detection_enabled: config.mining_detection_enabled,
          mining_cpu_threshold_percent: config.mining_cpu_threshold_percent,
          mining_window_hours: config.mining_window_hours,
          mining_scan_batch_size: config.mining_scan_batch_size,
          mining_auto_suspend: config.mining_auto_suspend,
          provision_max_concurrency: config.provision_max_concurrency,
          snapshot_cleanup_enabled: config.snapshot_cleanup_enabled,
          snapshot_retention_days: config.snapshot_retention_days,
          student_snapshot_max_count: config.student_snapshot_max_count,
        }
      : undefined,
  })

  const saveMutation = useMutation({
    mutationFn: (data: GovernanceConfigUpdate) =>
      GovernanceService.updateConfig({ requestBody: data }),
    onSuccess: () => {
      toast.success("治理設定已儲存")
      queryClient.invalidateQueries({ queryKey: ["governanceConfig"] })
    },
    onError: (error) => {
      toast.error(`儲存失敗：${getApiErrorMessage(error)}`)
    },
  })

  const onSave = (values: GovernanceFormValues) => {
    saveMutation.mutate(values)
  }

  if (isLoading) {
    return (
      <div className="flex h-40 items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    )
  }

  const numberField = (
    name: keyof GovernanceFormValues,
    label: string,
    opts: { min: number; max: number; step?: number; description?: string },
  ) => (
    <FormField
      control={form.control}
      name={name}
      rules={{
        required: "必填",
        min: { value: opts.min, message: `最小 ${opts.min}` },
        max: { value: opts.max, message: `最大 ${opts.max}` },
      }}
      render={({ field }) => (
        <FormItem>
          <FormLabel>{label}</FormLabel>
          <FormControl>
            <Input
              type="number"
              min={opts.min}
              max={opts.max}
              step={opts.step ?? 1}
              {...field}
              value={field.value as number}
              onChange={(e) => field.onChange(e.target.valueAsNumber)}
            />
          </FormControl>
          {opts.description && (
            <FormDescription>{opts.description}</FormDescription>
          )}
          <FormMessage />
        </FormItem>
      )}
    />
  )

  const switchField = (
    name: keyof GovernanceFormValues,
    label: string,
    description: string,
  ) => (
    <FormField
      control={form.control}
      name={name}
      render={({ field }) => (
        <FormItem className="flex flex-row items-center justify-between rounded-lg border p-3">
          <div className="space-y-0.5">
            <FormLabel>{label}</FormLabel>
            <FormDescription>{description}</FormDescription>
          </div>
          <FormControl>
            <Switch
              checked={field.value as boolean}
              onCheckedChange={field.onChange}
            />
          </FormControl>
        </FormItem>
      )}
    />
  )

  return (
    <Form {...form}>
      <div className="space-y-5">
        {/* 告警 */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <Bell className="h-4 w-4" />
              資源告警
            </CardTitle>
            <CardDescription>
              超過閾值時建立告警事件並通知管理員（站內 + Email）。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {switchField(
              "alerts_enabled",
              "啟用告警",
              "定期檢查叢集/節點/VM 資源使用率",
            )}
            <div className="grid gap-4 md:grid-cols-3">
              {numberField("alert_cpu_threshold", "CPU 閾值（%）", {
                min: 50,
                max: 100,
                step: 0.5,
              })}
              {numberField("alert_memory_threshold", "記憶體閾值（%）", {
                min: 50,
                max: 100,
                step: 0.5,
              })}
              {numberField("alert_disk_threshold", "磁碟閾值（%）", {
                min: 50,
                max: 100,
                step: 0.5,
              })}
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              {numberField("alert_cooldown_minutes", "冷卻期（分鐘）", {
                min: 1,
                max: 1440,
                description: "同一目標同一指標在冷卻期內不重發告警",
              })}
              {numberField("alert_check_interval_seconds", "檢查間隔（秒）", {
                min: 15,
                max: 3600,
              })}
            </div>
            {switchField(
              "alert_email_enabled",
              "Email 通知",
              "告警建立時寄送 Email 給管理員",
            )}
          </CardContent>
        </Card>

        {/* TTL */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <Clock className="h-4 w-4" />
              TTL 生命週期
            </CardTitle>
            <CardDescription>
              資源到期後漸進回收：到期前通知 → 到期關機 → 寬限期滿進入刪除佇列。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {switchField(
              "ttl_enabled",
              "啟用 TTL 回收",
              "依資源到期日自動通知、關機與排入刪除",
            )}
            <div className="grid gap-4 md:grid-cols-2">
              {numberField("expiry_warn_days", "到期前通知（天）", {
                min: 1,
                max: 30,
              })}
              {numberField("expiry_grace_delete_days", "刪除寬限期（天）", {
                min: 0,
                max: 90,
                description: "到期關機後保留幾天才排入刪除佇列",
              })}
            </div>
          </CardContent>
        </Card>

        {/* 閒置偵測 */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <MoonStar className="h-4 w-4" />
              閒置偵測
            </CardTitle>
            <CardDescription>
              CPU 長期低於閾值的資源先通知擁有者，寬限期滿自動關機（不刪除）。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {switchField(
              "idle_detection_enabled",
              "啟用閒置偵測",
              "偵測長期低 CPU 的運行中資源",
            )}
            <div className="grid gap-4 md:grid-cols-2">
              {numberField("idle_cpu_threshold_percent", "閒置 CPU 閾值（%）", {
                min: 0.1,
                max: 20,
                step: 0.1,
              })}
              {numberField("idle_window_hours", "觀察視窗（小時）", {
                min: 1,
                max: 720,
              })}
              {numberField("idle_grace_hours", "關機寬限期（小時）", {
                min: 1,
                max: 720,
                description: "通知後仍閒置達此時數才自動關機",
              })}
              {numberField("idle_scan_batch_size", "每輪掃描台數", {
                min: 1,
                max: 200,
              })}
            </div>
          </CardContent>
        </Card>

        {/* Auto 判斷 */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <Wand2 className="h-4 w-4" />
              VM / LXC 自動判斷
            </CardTitle>
            <CardDescription>
              申請資源時提供「自動判斷」模式，由規則引擎依工作負載建議 VM 或
              LXC。
            </CardDescription>
          </CardHeader>
          <CardContent>
            {switchField(
              "workload_advisor_enabled",
              "啟用自動判斷",
              "停用後申請表單僅能手動選擇資源類型",
            )}
          </CardContent>
        </Card>

        {/* 反挖礦 */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <Pickaxe className="h-4 w-4" />
              反挖礦偵測
            </CardTitle>
            <CardDescription>
              CPU 長期滿載的資源自動存證快照、暫停並通知；帳號停權由管理員在
              「資源監控 → 挖礦事件」人工確認。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {switchField(
              "mining_detection_enabled",
              "啟用挖礦偵測",
              "定期掃描運行中資源的 CPU 特徵",
            )}
            <div className="grid gap-4 md:grid-cols-3">
              {numberField("mining_cpu_threshold_percent", "CPU 閾值（%）", {
                min: 50,
                max: 100,
                step: 0.5,
              })}
              {numberField("mining_window_hours", "觀察視窗（小時）", {
                min: 1,
                max: 72,
                description: "平均 CPU 持續高於閾值達此時數才判定",
              })}
              {numberField("mining_scan_batch_size", "每輪掃描台數", {
                min: 1,
                max: 200,
              })}
            </div>
            {switchField(
              "mining_auto_suspend",
              "自動存證並暫停",
              "關閉後僅建立事件與通知，暫停由管理員手動執行",
            )}
          </CardContent>
        </Card>

        {/* 快照治理 */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <Camera className="h-4 w-4" />
              快照治理
            </CardTitle>
            <CardDescription>
              定期清理過期的學生快照（skylab-init 初始快照永不清理），並限制
              每台 VM 的學生快照數量。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {switchField(
              "snapshot_cleanup_enabled",
              "啟用快照自動清理",
              "超過保留天數的非保護快照將被排程刪除",
            )}
            <div className="grid gap-4 md:grid-cols-2">
              {numberField("snapshot_retention_days", "保留天數", {
                min: 1,
                max: 90,
              })}
              {numberField("student_snapshot_max_count", "學生快照上限", {
                min: 1,
                max: 10,
                description: "不含 skylab-init；達上限需先刪舊快照",
              })}
            </div>
          </CardContent>
        </Card>

        {/* 克隆併發 */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <Layers className="h-4 w-4" />
              克隆併發
            </CardTitle>
            <CardDescription>
              同時執行的 VM/LXC 克隆數上限（克隆為 PVE 磁碟 I/O
              重活，過高會拖垮儲存）。變更於下一個排程週期生效。
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="md:w-1/3">
              {numberField("provision_max_concurrency", "併發上限", {
                min: 1,
                max: 16,
              })}
            </div>
          </CardContent>
        </Card>

        <div className="flex justify-end">
          <LoadingButton
            type="button"
            loading={saveMutation.isPending}
            disabled={saveMutation.isPending}
            onClick={form.handleSubmit(onSave)}
          >
            <Save className="mr-2 h-4 w-4" />
            儲存治理設定
          </LoadingButton>
        </div>
      </div>
    </Form>
  )
}
