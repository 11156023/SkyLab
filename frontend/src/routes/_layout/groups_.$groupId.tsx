import {
  useMutation,
  useQuery,
  useQueryClient,
  useSuspenseQuery,
} from "@tanstack/react-query"

import { createFileRoute, Link } from "@tanstack/react-router"
import {
  ArrowLeft,
  Brain,
  CheckCircle2,
  Circle,
  Loader2,
  MessageSquare,
  Monitor,
  Plus,
  Power,
  PowerOff,
  ServerCog,
  Upload,
  UserMinus,
  Users,
  XCircle,
} from "lucide-react"
import { Suspense, useRef, useState } from "react"
import { useForm } from "react-hook-form"

import {
  type GroupMemberPublic,
  GroupsService,
  LxcService,
  VmService,
} from "@/client"
import {
  RecurrenceSchedulePicker,
  type RecurrenceValue,
} from "@/components/Schedule/RecurrenceSchedulePicker"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { LoadingButton } from "@/components/ui/loading-button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Slider } from "@/components/ui/slider"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { AiJudgeContent } from "@/features/ai-judge/components/AiJudgeContent"
import { AiPveMessageContent } from "@/features/ai-pve-log/components/AiPveMessageContent"
import { requireGroupManagerUser } from "@/features/auth/guards"
import { GroupFeatureService, type TaskStatus } from "@/features/groups/api"
import { groupDetailQueryOptions } from "@/features/groups/queryOptions"
import useAuth from "@/hooks/useAuth"
import useCustomToast from "@/hooks/useCustomToast"
import { queryKeys } from "@/lib/queryKeys"

// ─── Types ────────────────────────────────────────────────────────────────────

type CsvImportResult = {
  created: string[]
  already_existed: string[]
  added_to_group: number
  errors: string[]
}

// BatchTask / TaskStatus are imported from @/features/groups/api so the local
// shape matches the API response (especially the wider BatchJobStatus union
// that includes pending_review/approved/rejected/cancelled).

type GroupMemberWithRuntimeStats = GroupMemberPublic & {
  vm_cpu_usage_pct?: number | null
  vm_ram_usage_pct?: number | null
  vm_disk_usage_pct?: number | null
}

// ─── Route ────────────────────────────────────────────────────────────────────

export const Route = createFileRoute("/_layout/groups_/$groupId")({
  component: GroupDetailPage,
  beforeLoad: () => requireGroupManagerUser(),
  head: () => ({
    meta: [{ title: "群組詳情 - Campus Cloud" }],
  }),
})

// ─── Helpers ──────────────────────────────────────────────────────────────────

function TaskStatusIcon({ status }: { status: TaskStatus }) {
  if (status === "completed")
    return <CheckCircle2 className="h-4 w-4 text-green-500" />
  if (status === "failed")
    return <XCircle className="h-4 w-4 text-destructive" />
  if (status === "running")
    return <Loader2 className="h-4 w-4 animate-spin text-blue-500" />
  return <Circle className="h-4 w-4 text-muted-foreground" />
}

function ProgressBar({ done, total }: { done: number; total: number }) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0
  return (
    <div className="w-full rounded-full bg-muted h-2 overflow-hidden">
      <div
        className="h-full bg-primary transition-all duration-300"
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}

function VmStatusBadge({
  vmid,
  status,
  detailView,
}: {
  vmid?: number | null
  status?: string | null
  detailView?: "admin" | "my" | null
}) {
  if (!vmid) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
        <XCircle className="h-3.5 w-3.5" />
        未建立
      </span>
    )
  }

  const inner =
    status === "running" ? (
      <span className="inline-flex items-center gap-1 text-xs text-green-600">
        <Power className="h-3.5 w-3.5" />
        運行中
      </span>
    ) : status === "stopped" ? (
      <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
        <PowerOff className="h-3.5 w-3.5" />
        已關機
      </span>
    ) : (
      <span className="inline-flex items-center gap-1 text-xs text-yellow-600">
        <Monitor className="h-3.5 w-3.5" />
        {status ?? "未知"}
      </span>
    )

  if (detailView === "admin") {
    return (
      <Link
        to="/resources/$vmid"
        params={{ vmid: String(vmid) }}
        className="hover:opacity-70 transition-opacity"
      >
        {inner}
      </Link>
    )
  }

  if (detailView === "my") {
    return (
      <Link
        to="/my-resources/$vmid"
        params={{ vmid: String(vmid) }}
        className="hover:opacity-70 transition-opacity"
      >
        {inner}
      </Link>
    )
  }

  return (
    <span
      className="inline-flex items-center"
      title="教師檢視模式：僅顯示狀態摘要"
    >
      {inner}
    </span>
  )
}

function clampUsagePct(value?: number | null): number | null {
  if (typeof value !== "number" || Number.isNaN(value)) return null
  return Math.max(0, Math.min(value, 100))
}

function UsageCircle({
  label,
  value,
  color,
}: {
  label: string
  value?: number | null
  color: string
}) {
  const pct = clampUsagePct(value)
  const radius = 14
  const circumference = 2 * Math.PI * radius
  const dashOffset =
    pct === null ? circumference : circumference * (1 - pct / 100)

  return (
    <div className="flex flex-col items-center gap-0.5 min-w-10">
      <div className="relative h-9 w-9">
        <svg className="h-9 w-9 -rotate-90" viewBox="0 0 36 36">
          <title>{`${label} 使用率`}</title>
          <circle
            cx="18"
            cy="18"
            r={radius}
            fill="none"
            stroke="hsl(var(--muted))"
            strokeWidth="3"
          />
          <circle
            cx="18"
            cy="18"
            r={radius}
            fill="none"
            stroke={color}
            strokeWidth="3"
            strokeLinecap="round"
            strokeDasharray={`${circumference} ${circumference}`}
            strokeDashoffset={dashOffset}
            className="transition-[stroke-dashoffset] duration-500"
          />
        </svg>
        <span className="absolute inset-0 flex items-center justify-center text-[10px] font-semibold text-foreground">
          {pct === null ? "--" : Math.round(pct)}
        </span>
      </div>
      <span className="text-[10px] leading-none text-muted-foreground">
        {label}
      </span>
    </div>
  )
}

function VmRuntimeIndicators({
  member,
}: {
  member: GroupMemberWithRuntimeStats
}) {
  if (!member.vmid || member.vm_status !== "running") return null

  return (
    <div className="flex items-center gap-2 rounded-md border border-border/60 bg-muted/20 px-2 py-1">
      <UsageCircle
        label="CPU"
        value={member.vm_cpu_usage_pct}
        color="#3b82f6"
      />
      <UsageCircle
        label="RAM"
        value={member.vm_ram_usage_pct}
        color="#10b981"
      />
      <UsageCircle
        label="碟"
        value={member.vm_disk_usage_pct}
        color="#f59e0b"
      />
    </div>
  )
}

// ─── Import CSV Dialog ────────────────────────────────────────────────────────

function ImportCsvDialog({ groupId }: { groupId: string }) {
  const [open, setOpen] = useState(false)
  const [result, setResult] = useState<CsvImportResult | null>(null)
  const [loading, setLoading] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const handleImport = async () => {
    const file = fileRef.current?.files?.[0]
    if (!file) return
    setLoading(true)
    setResult(null)
    try {
      const data = await GroupFeatureService.importCsv({ groupId, file })
      setResult(data)
      queryClient.invalidateQueries({
        queryKey: queryKeys.groups.detail(groupId),
      })
      showSuccessToast(
        `匯入完成：新建 ${data.created.length} 人，加入群組 ${data.added_to_group} 人`,
      )
    } catch (err: any) {
      showErrorToast(err?.body?.detail ?? "匯入失敗")
    } finally {
      setLoading(false)
    }
  }

  const handleOpenChange = (v: boolean) => {
    setOpen(v)
    if (!v) {
      setResult(null)
      if (fileRef.current) fileRef.current.value = ""
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <Upload className="mr-1 h-4 w-4" />
          匯入 CSV
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>從 CSV 大量匯入學生</DialogTitle>
        </DialogHeader>
        <div className="grid gap-4 py-4">
          <p className="text-sm text-muted-foreground">
            CSV 格式：學號, 姓名, 班級（支援 Big5 / UTF-8）。
            帳號不存在時自動建立，email 為 <code>學號@ntub.edu.tw</code>
            ，系統將寄送通知信。
          </p>
          <div className="grid gap-2">
            <Label htmlFor="csv-file">選擇 CSV 檔案</Label>
            <input
              id="csv-file"
              type="file"
              accept=".csv"
              ref={fileRef}
              className="text-sm file:mr-2 file:rounded file:border-0 file:bg-muted file:px-3 file:py-1 file:text-sm"
            />
          </div>
          {result && (
            <div className="rounded border bg-muted/40 p-3 text-sm space-y-1">
              <p>
                ✅ 新建帳號：<strong>{result.created.length}</strong> 人
              </p>
              <p>
                ℹ️ 帳號已存在：<strong>{result.already_existed.length}</strong>{" "}
                人
              </p>
              <p>
                👥 加入群組：<strong>{result.added_to_group}</strong> 人
              </p>
              {result.errors.length > 0 && (
                <div className="text-destructive">
                  <p>❌ 錯誤（{result.errors.length}）：</p>
                  <ul className="ml-4 list-disc text-xs">
                    {result.errors.map((e, i) => (
                      <li key={i}>{e}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline" disabled={loading}>
              關閉
            </Button>
          </DialogClose>
          <LoadingButton loading={loading} onClick={handleImport}>
            匯入
          </LoadingButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ─── Add Members Dialog ───────────────────────────────────────────────────────

function AddMembersDialog({ groupId }: { groupId: string }) {
  const [open, setOpen] = useState(false)
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const { register, handleSubmit, reset } = useForm<{ emailsText: string }>()

  const mutation = useMutation({
    mutationFn: (emails: string[]) =>
      GroupsService.addMembers({ groupId, requestBody: { emails } }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.groups.detail(groupId),
      })
      if (data.message.includes("Not found:")) {
        showErrorToast(data.message)
      } else {
        showSuccessToast(data.message)
        reset()
        setOpen(false)
      }
    },
    onError: (err: any) => showErrorToast(err?.body?.detail ?? "加入成員失敗"),
  })

  const onSubmit = ({ emailsText }: { emailsText: string }) => {
    const emails = emailsText
      .split(/[\n,]/)
      .map((e) => e.trim())
      .filter(Boolean)
    mutation.mutate(emails)
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <Plus className="mr-1 h-4 w-4" />
          加入成員
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>加入成員</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit(onSubmit)}>
          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <Label>Email 列表（每行一個或逗號分隔）</Label>
              <textarea
                {...register("emailsText", { required: true })}
                placeholder={"student1@example.com\nstudent2@example.com"}
                rows={6}
                className="flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
              />
            </div>
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline" disabled={mutation.isPending}>
                取消
              </Button>
            </DialogClose>
            <LoadingButton type="submit" loading={mutation.isPending}>
              加入
            </LoadingButton>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

// ─── Batch Provision Dialog ───────────────────────────────────────────────────

function BatchProvisionDialog({
  groupId,
  memberCount,
}: {
  groupId: string
  memberCount: number
}) {
  const [open, setOpen] = useState(false)
  const [resourceType, setResourceType] = useState<"lxc" | "qemu">("lxc")
  const [jobId, setJobId] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const { showErrorToast } = useCustomToast()

  // form state
  const [hostnamePrefix, setHostnamePrefix] = useState("")
  const [password, setPassword] = useState("")
  const [cores, setCores] = useState(2)
  const [memory, setMemory] = useState(2048)
  const [rootfsSize, setRootfsSize] = useState(8)
  const [diskSize, setDiskSize] = useState(20)
  const [ostemplate, setOstemplate] = useState("")
  const [templateId, setTemplateId] = useState<number | null>(null)
  const [username, setUsername] = useState("")
  const [expiryDate, setExpiryDate] = useState("")
  const [schedule, setSchedule] = useState<RecurrenceValue>({
    recurrence_rule: null,
    recurrence_duration_minutes: null,
    schedule_timezone: null,
  })

  const { data: lxcTemplates } = useQuery({
    queryKey: queryKeys.resources.templates.lxc,
    queryFn: () => LxcService.getTemplates(),
    enabled: open && resourceType === "lxc",
  })

  const { data: vmTemplates } = useQuery({
    queryKey: queryKeys.resources.templates.vm,
    queryFn: () => VmService.getVmTemplates(),
    enabled: open && resourceType === "qemu",
  })

  // 輪詢進度
  const { data: jobStatus } = useQuery({
    queryKey: queryKeys.groups.batchJob(jobId ?? "pending"),
    queryFn: () =>
      GroupFeatureService.getBatchProvisionStatus({ jobId: jobId! }),
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      // Terminal states — stop polling.
      if (
        status === "completed" ||
        status === "failed" ||
        status === "rejected" ||
        status === "cancelled"
      ) {
        return false
      }
      // Pending review can take a while; poll every 5s to catch admin decision.
      if (status === "pending_review") return 5000
      // Active build phases — poll fast.
      return 2000
    },
  })

  // Active states block dialog close; "pending_review" is treated as active so
  // that the teacher doesn't accidentally close mid-submission. After review
  // (approved/rejected/cancelled) we're in a settled UX state.
  const isRunning =
    jobStatus?.status === "pending_review" ||
    jobStatus?.status === "approved" ||
    jobStatus?.status === "pending" ||
    jobStatus?.status === "running"

  const handleSubmit = async () => {
    if (!hostnamePrefix.trim() || !password) return
    if (resourceType === "lxc" && !ostemplate) return
    if (resourceType === "qemu" && (!templateId || !username.trim())) return

    setSubmitting(true)
    try {
      const body: Parameters<
        typeof GroupFeatureService.createBatchProvisionJob
      >[0]["requestBody"] = {
        resource_type: resourceType,
        hostname_prefix: hostnamePrefix.trim(),
        password,
        cores,
        memory,
        environment_type: "批量建立",
      }
      if (expiryDate) body.expiry_date = expiryDate
      if (resourceType === "lxc") {
        body.ostemplate = ostemplate
        body.rootfs_size = rootfsSize
      } else {
        body.template_id = templateId ?? undefined
        body.username = username.trim()
        body.disk_size = diskSize
      }
      if (schedule.recurrence_rule) {
        body.recurrence_rule = schedule.recurrence_rule
        body.recurrence_duration_minutes =
          schedule.recurrence_duration_minutes ?? undefined
        body.schedule_timezone = schedule.schedule_timezone ?? undefined
      }

      const job = await GroupFeatureService.createBatchProvisionJob({
        groupId,
        requestBody: body,
      })
      setJobId(job.id)
    } catch (err: any) {
      showErrorToast(err?.body?.detail ?? "批量建立失敗")
    } finally {
      setSubmitting(false)
    }
  }

  const handleOpenChange = (v: boolean) => {
    if (isRunning) return // 執行中不允許關閉
    setOpen(v)
    if (!v) {
      setJobId(null)
      setHostnamePrefix("")
      setPassword("")
      setCores(2)
      setMemory(2048)
      setOstemplate("")
      setTemplateId(null)
      setUsername("")
      setExpiryDate("")
      setSchedule({
        recurrence_rule: null,
        recurrence_duration_minutes: null,
        schedule_timezone: null,
      })
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button size="sm">
          <ServerCog className="mr-1 h-4 w-4" />
          批量建立資源
        </Button>
      </DialogTrigger>

      <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>批量建立資源</DialogTitle>
        </DialogHeader>

        {!jobId ? (
          /* ── 表單 ── */
          <div className="space-y-5 py-2">
            <p className="text-sm text-muted-foreground">
              將為群組 <strong>{memberCount}</strong>{" "}
              位成員各自建立一台資源，並自動分配給對應帳號。 Hostname
              會自動加上流水號（例如 <code>webdev-1</code>、
              <code>webdev-2</code>…）。
            </p>
            <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-200">
              <p className="font-medium">送出後需經管理員審核</p>
              <p className="mt-0.5 text-xs">
                此申請會先進入「待審核」，管理員通過後才會開始建立每位成員的資源。送出後可關閉視窗，建立進度可在群組頁面或
                Approvals 查看。
              </p>
            </div>

            <Tabs
              value={resourceType}
              onValueChange={(v) => setResourceType(v as "lxc" | "qemu")}
            >
              <TabsList className="grid w-full grid-cols-2">
                <TabsTrigger value="lxc">LXC 容器</TabsTrigger>
                <TabsTrigger value="qemu">VM 虛擬機</TabsTrigger>
              </TabsList>

              {/* ── LXC ── */}
              <TabsContent value="lxc" className="mt-4 space-y-4">
                <div className="grid gap-2">
                  <Label>
                    Hostname 前綴 <span className="text-destructive">*</span>
                  </Label>
                  <Input
                    placeholder="webdev"
                    value={hostnamePrefix}
                    onChange={(e) => setHostnamePrefix(e.target.value)}
                  />
                  <p className="text-xs text-muted-foreground">
                    實際 hostname：{hostnamePrefix || "prefix"}-1、
                    {hostnamePrefix || "prefix"}-2…
                  </p>
                </div>

                <div className="grid gap-2">
                  <Label>
                    OS Template <span className="text-destructive">*</span>
                  </Label>
                  <Select value={ostemplate} onValueChange={setOstemplate}>
                    <SelectTrigger>
                      <SelectValue placeholder="選擇 OS 模板" />
                    </SelectTrigger>
                    <SelectContent>
                      {lxcTemplates?.map((t) => (
                        <SelectItem key={t.volid} value={t.volid}>
                          {t.volid.split("/").pop()?.replace(".tar.zst", "")}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="grid gap-2">
                  <Label>
                    Root 密碼 <span className="text-destructive">*</span>
                  </Label>
                  <Input
                    type="password"
                    placeholder="統一密碼"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                </div>

                <div className="grid gap-2">
                  <Label>到期日</Label>
                  <Input
                    type="date"
                    value={expiryDate}
                    onChange={(e) => setExpiryDate(e.target.value)}
                  />
                </div>

                <div className="rounded-xl border bg-muted/20 p-4 space-y-4">
                  <p className="text-sm font-medium">硬體規格</p>
                  <div className="space-y-1">
                    <div className="flex justify-between text-sm">
                      <span>CPU</span>
                      <span className="font-semibold text-primary">
                        {cores} Cores
                      </span>
                    </div>
                    <Slider
                      min={1}
                      max={8}
                      step={1}
                      value={[cores]}
                      onValueChange={([v]) => setCores(v)}
                    />
                  </div>
                  <div className="space-y-1">
                    <div className="flex justify-between text-sm">
                      <span>記憶體</span>
                      <span className="font-semibold text-primary">
                        {(memory / 1024).toFixed(1)} GB
                      </span>
                    </div>
                    <Slider
                      min={512}
                      max={32768}
                      step={512}
                      value={[memory]}
                      onValueChange={([v]) => setMemory(v)}
                    />
                  </div>
                  <div className="space-y-1">
                    <div className="flex justify-between text-sm">
                      <span>磁碟</span>
                      <span className="font-semibold text-primary">
                        {rootfsSize} GB
                      </span>
                    </div>
                    <Slider
                      min={8}
                      max={500}
                      step={1}
                      value={[rootfsSize]}
                      onValueChange={([v]) => setRootfsSize(v)}
                    />
                  </div>
                </div>
              </TabsContent>

              {/* ── VM ── */}
              <TabsContent value="qemu" className="mt-4 space-y-4">
                <div className="grid gap-2">
                  <Label>
                    Hostname 前綴 <span className="text-destructive">*</span>
                  </Label>
                  <Input
                    placeholder="lab-vm"
                    value={hostnamePrefix}
                    onChange={(e) => setHostnamePrefix(e.target.value)}
                  />
                  <p className="text-xs text-muted-foreground">
                    實際 hostname：{hostnamePrefix || "prefix"}-1、
                    {hostnamePrefix || "prefix"}-2…
                  </p>
                </div>

                <div className="grid gap-2">
                  <Label>
                    OS Template <span className="text-destructive">*</span>
                  </Label>
                  <Select
                    value={templateId?.toString() ?? ""}
                    onValueChange={(v) => setTemplateId(Number(v))}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="選擇 VM 模板" />
                    </SelectTrigger>
                    <SelectContent>
                      {vmTemplates?.map((t) => (
                        <SelectItem key={t.vmid} value={t.vmid.toString()}>
                          {t.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div className="grid gap-2">
                    <Label>
                      用戶名 <span className="text-destructive">*</span>
                    </Label>
                    <Input
                      placeholder="student"
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                    />
                  </div>
                  <div className="grid gap-2">
                    <Label>
                      密碼 <span className="text-destructive">*</span>
                    </Label>
                    <Input
                      type="password"
                      placeholder="統一密碼"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                    />
                  </div>
                </div>

                <div className="grid gap-2">
                  <Label>到期日</Label>
                  <Input
                    type="date"
                    value={expiryDate}
                    onChange={(e) => setExpiryDate(e.target.value)}
                  />
                </div>

                <div className="rounded-xl border bg-muted/20 p-4 space-y-4">
                  <p className="text-sm font-medium">硬體規格</p>
                  <div className="space-y-1">
                    <div className="flex justify-between text-sm">
                      <span>CPU</span>
                      <span className="font-semibold text-primary">
                        {cores} Cores
                      </span>
                    </div>
                    <Slider
                      min={1}
                      max={8}
                      step={1}
                      value={[cores]}
                      onValueChange={([v]) => setCores(v)}
                    />
                  </div>
                  <div className="space-y-1">
                    <div className="flex justify-between text-sm">
                      <span>記憶體</span>
                      <span className="font-semibold text-primary">
                        {(memory / 1024).toFixed(1)} GB
                      </span>
                    </div>
                    <Slider
                      min={512}
                      max={32768}
                      step={512}
                      value={[memory]}
                      onValueChange={([v]) => setMemory(v)}
                    />
                  </div>
                  <div className="space-y-1">
                    <div className="flex justify-between text-sm">
                      <span>磁碟</span>
                      <span className="font-semibold text-primary">
                        {diskSize} GB
                      </span>
                    </div>
                    <Slider
                      min={20}
                      max={500}
                      step={1}
                      value={[diskSize]}
                      onValueChange={([v]) => setDiskSize(v)}
                    />
                  </div>
                </div>
              </TabsContent>
            </Tabs>

            <RecurrenceSchedulePicker value={schedule} onChange={setSchedule} />

            <DialogFooter className="pt-2">
              <DialogClose asChild>
                <Button variant="outline" disabled={submitting}>
                  取消
                </Button>
              </DialogClose>
              <LoadingButton
                loading={submitting}
                onClick={handleSubmit}
                disabled={
                  !hostnamePrefix.trim() ||
                  !password ||
                  (resourceType === "lxc" && !ostemplate) ||
                  (resourceType === "qemu" && (!templateId || !username.trim()))
                }
              >
                送出審核（{memberCount} 台）
              </LoadingButton>
            </DialogFooter>
          </div>
        ) : (
          /* ── 進度追蹤 ── */
          <div className="space-y-4 py-2">
            {/* 狀態說明 */}
            {jobStatus?.status === "pending_review" && (
              <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-200">
                <p className="font-medium">已送出，等待管理員審核</p>
                <p className="mt-0.5 text-xs">
                  資源尚未建立。可關閉此視窗，待管理員通過後 scheduler
                  會自動進行建立。
                </p>
              </div>
            )}
            {jobStatus?.status === "rejected" && (
              <div className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm">
                <p className="font-medium text-destructive">申請已被退回</p>
                {jobStatus.review_comment && (
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    管理員留言：{jobStatus.review_comment}
                  </p>
                )}
              </div>
            )}
            {jobStatus?.status === "approved" && (
              <div className="rounded-md border border-blue-300 bg-blue-50 px-3 py-2 text-sm dark:border-blue-700 dark:bg-blue-950/40">
                <p className="font-medium">審核已通過</p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  資源即將開始建立…
                </p>
              </div>
            )}

            {/* 總覽 */}
            <div className="space-y-2">
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted-foreground">
                  {jobStatus?.done ?? 0} / {jobStatus?.total ?? memberCount}{" "}
                  台完成
                </span>
                <div className="flex items-center gap-2">
                  {jobStatus?.failed_count ? (
                    <Badge variant="destructive">
                      {jobStatus.failed_count} 台失敗
                    </Badge>
                  ) : null}
                  <Badge
                    variant={
                      jobStatus?.status === "completed"
                        ? "default"
                        : jobStatus?.status === "failed" ||
                            jobStatus?.status === "rejected"
                          ? "destructive"
                          : "secondary"
                    }
                  >
                    {jobStatus?.status === "pending_review" && "等待管理員審核"}
                    {jobStatus?.status === "approved" && "已通過，準備建立"}
                    {jobStatus?.status === "rejected" && "已被退回"}
                    {jobStatus?.status === "cancelled" && "已取消"}
                    {jobStatus?.status === "pending" && "排隊中"}
                    {jobStatus?.status === "running" && "建立中"}
                    {jobStatus?.status === "completed" && "已完成"}
                    {jobStatus?.status === "failed" && "失敗"}
                  </Badge>
                </div>
              </div>
              <ProgressBar
                done={jobStatus?.done ?? 0}
                total={jobStatus?.total ?? memberCount}
              />
            </div>

            {/* 每台進度列表 */}
            <div className="max-h-72 overflow-y-auto rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-8">#</TableHead>
                    <TableHead>成員</TableHead>
                    <TableHead>Hostname</TableHead>
                    <TableHead className="w-16">VMID</TableHead>
                    <TableHead className="w-8" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(jobStatus?.tasks ?? []).map((task) => (
                    <TableRow key={task.id}>
                      <TableCell className="text-muted-foreground text-xs">
                        {task.member_index}
                      </TableCell>
                      <TableCell className="text-sm">
                        <div>{task.user_name ?? "-"}</div>
                        <div className="text-xs text-muted-foreground">
                          {task.user_email}
                        </div>
                      </TableCell>
                      <TableCell className="text-xs font-mono text-muted-foreground">
                        {jobStatus
                          ? `${jobStatus.hostname_prefix}-${task.member_index}`
                          : "-"}
                      </TableCell>
                      <TableCell className="text-xs">
                        {task.vmid ?? "-"}
                      </TableCell>
                      <TableCell>
                        <div title={task.error ?? undefined}>
                          <TaskStatusIcon status={task.status} />
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>

            {/* 失敗訊息 */}
            {(jobStatus?.tasks ?? []).some((t) => t.status === "failed") && (
              <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-xs space-y-1">
                <p className="font-medium text-destructive">失敗項目：</p>
                {jobStatus!.tasks
                  .filter((t) => t.status === "failed")
                  .map((t) => (
                    <p key={t.id} className="text-muted-foreground">
                      #{t.member_index} {t.user_email}：{t.error}
                    </p>
                  ))}
              </div>
            )}

            <DialogFooter>
              <Button variant="outline" onClick={() => handleOpenChange(false)}>
                {jobStatus?.status === "pending_review" ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4" />
                    關閉並等待審核
                  </>
                ) : isRunning ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    建立中，可稍後再回來
                  </>
                ) : (
                  "關閉"
                )}
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

// ─── Group Detail Content ─────────────────────────────────────────────────────

function GroupDetailContent({ groupId }: { groupId: string }) {
  const queryClient = useQueryClient()
  const { user: currentUser } = useAuth()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const canOpenAdminResourceDetail =
    currentUser?.role === "admin" || currentUser?.is_superuser || false

  const [activeView, setActiveView] = useState<
    "members" | "ai-judge" | "ai-pve-message"
  >("members")

  const { data: group } = useSuspenseQuery(groupDetailQueryOptions(groupId))
  const members = (group.members ?? []) as GroupMemberWithRuntimeStats[]

  const removeMutation = useMutation({
    mutationFn: (userId: string) =>
      GroupsService.removeMember({ groupId, userId }),
    onSuccess: () => {
      showSuccessToast("成員已移除")
      queryClient.invalidateQueries({
        queryKey: queryKeys.groups.detail(groupId),
      })
    },
    onError: (err: any) => showErrorToast(err?.body?.detail ?? "移除失敗"),
  })

  return (
    <div className="flex flex-col gap-6">
      {/* 頂部 Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Link
              to="/groups"
              className="text-muted-foreground hover:text-foreground transition-colors"
            >
              <ArrowLeft className="h-4 w-4" />
            </Link>
            <h1 className="text-2xl font-bold tracking-tight">{group.name}</h1>
          </div>
          {group.description && (
            <p className="text-muted-foreground">{group.description}</p>
          )}
        </div>

        {/* 右上角動作按鈕 */}
        <div className="flex items-center gap-2">
          <ImportCsvDialog groupId={groupId} />
          <AddMembersDialog groupId={groupId} />
          {members.length > 0 && (
            <BatchProvisionDialog
              groupId={groupId}
              memberCount={members.length}
            />
          )}
        </div>
      </div>

      {/* 主體兩欄式佈局 */}
      <div className="flex flex-col lg:flex-row gap-6">
        {/* 左側整合功能側邊欄 */}
        <div className="w-full lg:w-56 shrink-0 flex flex-col gap-4">
          <div className="rounded-xl border bg-card p-4 shadow-sm backdrop-blur-sm bg-background/50 flex flex-col gap-2 lg:sticky lg:top-6">
            <h3 className="text-sm font-semibold text-muted-foreground mb-1 ml-1 tracking-wide">
              功能導航
            </h3>
            <Button
              variant={activeView === "members" ? "secondary" : "ghost"}
              className="w-full justify-start transition-all"
              onClick={() => setActiveView("members")}
            >
              <Users className="mr-2 h-4 w-4 text-primary" />
              群組資源大廳
            </Button>

            <div className="my-1 h-px bg-border/50" />

            <h3 className="text-sm font-semibold text-muted-foreground mb-1 ml-1 tracking-wide mt-2">
              系統整合
            </h3>
            <Button
              variant={activeView === "ai-judge" ? "secondary" : "ghost"}
              className="w-full justify-start transition-all hover:bg-accent hover:text-accent-foreground border-border/50"
              onClick={() => setActiveView("ai-judge")}
            >
              <Brain className="mr-2 h-4 w-4 text-blue-500" />
              AI 情境分析
            </Button>
            <Button
              variant={activeView === "ai-pve-message" ? "secondary" : "ghost"}
              className="w-full justify-start transition-all hover:bg-accent hover:text-accent-foreground border-border/50"
              onClick={() => setActiveView("ai-pve-message")}
            >
              <MessageSquare className="mr-2 h-4 w-4 text-green-500" />
              AI PVE 訊息
            </Button>
          </div>
        </div>

        {/* 內容區塊 */}
        <div className="flex-1 min-w-0 flex flex-col gap-6">
          {activeView === "ai-judge" && <AiJudgeContent groupId={groupId} />}
          {activeView === "ai-pve-message" && (
            <AiPveMessageContent groupId={groupId} />
          )}
          {activeView === "members" && (
            <>
              {/* 橫排 1-20 週區塊 */}
              <div className="rounded-xl border bg-card p-4 shadow-sm backdrop-blur-sm bg-background/50">
                <div className="flex items-center gap-2 overflow-x-auto pb-2 scroll-smooth">
                  {Array.from({ length: 20 }, (_, i) => i + 1).map((week) => (
                    <button
                      key={week}
                      type="button"
                      className="shrink-0 rounded-full px-5 py-1.5 text-sm font-medium transition-all
                                 border border-border/40 bg-muted/20 text-muted-foreground
                                 hover:bg-primary/10 hover:text-primary hover:border-primary/40
                                 active:scale-95"
                    >
                      第 {week} 週
                    </button>
                  ))}
                </div>
              </div>

              {/* 表格區塊 */}
              <div className="rounded-xl border bg-card p-5 shadow-sm">
                <h2 className="text-lg font-semibold mb-1">
                  成員列表（{members.length} 人）
                </h2>
                {!canOpenAdminResourceDetail && (
                  <p className="mb-4 text-xs text-muted-foreground">
                    教師檢視模式：僅提供 VM
                    狀態與即時資源摘要，不會直接導向管理員資源詳情頁。
                  </p>
                )}
                {members.length === 0 ? (
                  <p className="text-muted-foreground text-sm py-4">
                    尚無成員，點擊「加入成員」開始新增
                  </p>
                ) : (
                  <div className="mt-4">
                    {/* 統計 bar */}
                    {(() => {
                      const lxcMembers = members.filter(
                        (m) => m.vm_type === "lxc" && m.vmid,
                      )
                      const vmMembers = members.filter(
                        (m) => m.vm_type === "qemu" && m.vmid,
                      )
                      const lxcRunning = lxcMembers.filter(
                        (m) => m.vm_status === "running",
                      ).length
                      const vmRunning = vmMembers.filter(
                        (m) => m.vm_status === "running",
                      ).length
                      return (
                        <div className="flex items-center gap-4 mb-3 text-sm px-1">
                          <span className="text-muted-foreground">
                            LXC{" "}
                            <span className="font-semibold text-foreground">
                              {lxcRunning}/{lxcMembers.length}
                            </span>
                          </span>
                          <span className="text-muted-foreground">
                            VM{" "}
                            <span className="font-semibold text-foreground">
                              {vmRunning}/{vmMembers.length}
                            </span>
                          </span>
                        </div>
                      )
                    })()}
                    <div className="overflow-hidden rounded-md border">
                      <Table>
                        <TableHeader className="bg-muted/30">
                          <TableRow>
                            <TableHead>姓名</TableHead>
                            <TableHead>Email</TableHead>
                            <TableHead>VMID</TableHead>
                            <TableHead>VM 狀態 / 即時資源</TableHead>
                            <TableHead>加入時間</TableHead>
                            <TableHead className="w-12" />
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {members.map((member) => (
                            <TableRow key={member.user_id}>
                              <TableCell className="font-medium">
                                {member.full_name ?? "-"}
                              </TableCell>
                              <TableCell className="text-muted-foreground">
                                {member.email}
                              </TableCell>
                              <TableCell className="font-mono text-sm text-muted-foreground">
                                {member.vmid ?? "-"}
                              </TableCell>
                              <TableCell>
                                <div className="flex flex-wrap items-center gap-2">
                                  <VmStatusBadge
                                    vmid={member.vmid}
                                    status={member.vm_status}
                                    detailView={
                                      canOpenAdminResourceDetail
                                        ? "admin"
                                        : currentUser?.id === member.user_id
                                          ? "my"
                                          : null
                                    }
                                  />
                                  <VmRuntimeIndicators member={member} />
                                </div>
                              </TableCell>
                              <TableCell className="text-xs text-muted-foreground">
                                {member.added_at
                                  ? new Date(
                                      member.added_at,
                                    ).toLocaleDateString("zh-TW")
                                  : "-"}
                              </TableCell>
                              <TableCell>
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                                  onClick={() =>
                                    removeMutation.mutate(member.user_id)
                                  }
                                  disabled={removeMutation.isPending}
                                >
                                  <UserMinus className="h-4 w-4" />
                                </Button>
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

function GroupDetailPage() {
  const { groupId } = Route.useParams()
  return (
    <Suspense fallback={<div className="text-muted-foreground">載入中…</div>}>
      <GroupDetailContent groupId={groupId} />
    </Suspense>
  )
}
