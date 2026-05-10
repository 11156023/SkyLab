import { standardSchemaResolver } from "@hookform/resolvers/standard-schema"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { ArrowLeft, LayoutTemplate, RefreshCw, X } from "lucide-react"
import {
  type CSSProperties,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { useForm, useWatch } from "react-hook-form"
import { useTranslation } from "react-i18next"
import { z } from "zod"

import { type ApiError, LxcService, VmService } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Form,
  FormControl,
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
import { Slider } from "@/components/ui/slider"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import useAuth from "@/hooks/useAuth"
import useCustomToast from "@/hooks/useCustomToast"
import { queryKeys } from "@/lib/queryKeys"
import {
  getQuickStartPreset,
  pickQuickStartVmTemplateId,
  type QuickStartMode,
  type QuickStartPresetId,
} from "@/lib/quickStart"
import { toVmRequestCreateRequestBody } from "@/lib/resourcePayloads"
import { pickMatchingOsTemplate } from "@/lib/serviceTemplates"
import { cn } from "@/lib/utils"
import { type GPUSummary, GpuService } from "@/services/gpu"
import {
  VmRequestsApi,
  type VmRequestWindowAvailabilityResponse,
} from "@/services/vmRequests"
import { handleError } from "@/utils"
import { AiChatPanel, type AiPlanResult } from "./AiChatPanel"
import { type FastTemplate, FastTemplatesTab } from "./FastTemplatesTab"

function normalizeHostname(value: string) {
  return (
    String(value || "")
      .toLowerCase()
      // 保留 Unicode 字母、數字和連字符，其他替換為連字符
      .replace(/[^\p{L}\p{N}-]/gu, "-")
      .replace(/-+/g, "-")
      .replace(/^-|-$/g, "")
      .slice(0, 63)
  )
}

function formatScheduleSummary(startAt?: string, endAt?: string) {
  if (!startAt || !endAt) return "尚未選擇時段"

  const formatter = new Intl.DateTimeFormat("zh-TW", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "2-digit",
    timeZone: "Asia/Taipei",
  })

  return `${formatter.format(new Date(startAt))} - ${formatter.format(new Date(endAt))}`
}

function formatTaipeiDateInput(offsetDays = 0) {
  const date = new Date()
  date.setDate(date.getDate() + offsetDays)
  return new Intl.DateTimeFormat("en-CA", {
    day: "2-digit",
    month: "2-digit",
    timeZone: "Asia/Taipei",
    year: "numeric",
  }).format(date)
}

function addDaysToDateInput(dateInput: string, days: number) {
  const date = new Date(`${dateInput}T00:00:00+08:00`)
  date.setUTCDate(date.getUTCDate() + days)
  return new Intl.DateTimeFormat("en-CA", {
    day: "2-digit",
    month: "2-digit",
    timeZone: "Asia/Taipei",
    year: "numeric",
  }).format(date)
}

function toTaipeiDateTimeStart(dateInput: string) {
  return `${dateInput}T00:00:00+08:00`
}

function addHoursIso(date: Date, hours: number) {
  return new Date(date.getTime() + hours * 60 * 60 * 1000).toISOString()
}

function getOrderedEndDate(startDate: string, endDate: string) {
  if (!endDate || endDate < startDate) return startDate
  return endDate
}

function getAvailabilityTone(
  availability: VmRequestWindowAvailabilityResponse | undefined,
) {
  if (!availability) {
    return {
      badge: "outline" as const,
      label: "尚未評估",
      panel: "border-border bg-muted/20",
      text: "text-muted-foreground",
    }
  }
  if (availability.status === "available") {
    return {
      badge: "secondary" as const,
      label: "可安排",
      panel: "border-emerald-300/70 bg-emerald-500/10",
      text: "text-emerald-700",
    }
  }
  if (availability.status === "limited") {
    return {
      badge: "outline" as const,
      label: "容量緊張",
      panel: "border-amber-300/70 bg-amber-500/10",
      text: "text-amber-700",
    }
  }
  return {
    badge: "destructive" as const,
    label: "容量不足",
    panel: "border-destructive/40 bg-destructive/10",
    text: "text-destructive",
  }
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-border/70 py-2.5 last:border-b-0">
      <span className="text-xs uppercase tracking-[0.18em] text-muted-foreground">
        {label}
      </span>
      <span className="max-w-[72%] text-right text-sm leading-snug">
        {value}
      </span>
    </div>
  )
}

type ImportedFormPrefill = NonNullable<
  NonNullable<AiPlanResult["final_plan"]>["form_prefill"]
>

type DesktopPanelFrame = {
  height: number
  left: number
  top: number
  width: number
}

const AI_PANEL_BOTTOM_GAP = 16

type ApplicationRequestPageProps = {
  quickStartMode?: QuickStartMode
  quickStartPreset?: QuickStartPresetId
}

export function ApplicationRequestPage({
  quickStartMode,
  quickStartPreset,
}: ApplicationRequestPageProps) {
  const { t } = useTranslation([
    "applications",
    "resources",
    "validation",
    "common",
    "messages",
  ])
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const { user } = useAuth()
  const backPath =
    user?.role === "admin" || user?.is_superuser
      ? "/approvals"
      : "/applications"
  const showAiAssistant = true
  const [showTemplateSelector, setShowTemplateSelector] = useState(false)
  const [resourceType, setResourceType] = useState<"lxc" | "vm">("lxc")
  const [serviceTemplateName, setServiceTemplateName] = useState("")
  const [serviceTemplateSlug, setServiceTemplateSlug] = useState("")
  const [serviceTemplateScriptPath, setServiceTemplateScriptPath] = useState("")
  const aiColumnRef = useRef<HTMLElement | null>(null)
  const [desktopPanelFrame, setDesktopPanelFrame] =
    useState<DesktopPanelFrame | null>(null)
  const activeQuickStart = useMemo(
    () => getQuickStartPreset(quickStartPreset),
    [quickStartPreset],
  )
  const isQuickStartAiMode =
    Boolean(activeQuickStart) && quickStartMode === "ai"
  const isStudentQuickTemplateUser =
    user?.role === "student" && !user.is_superuser

  const formSchema = useMemo(
    () =>
      z.object({
        resource_type: z.enum(["lxc", "vm"]),
        mode: z
          .enum(["quick_template", "scheduled", "immediate"])
          .default("scheduled"),
        reason: z
          .string()
          .min(1, { message: t("validation:reason.required") })
          .min(10, {
            message: t("validation:reason.minLength", { count: 10 }),
          }),
        hostname: z
          .string()
          .min(1, { message: t("validation:name.required") })
          .max(63)
          .regex(/^[\p{L}\p{N}]([\p{L}\p{N}-]*[\p{L}\p{N}])?$/u, {
            message: t("validation:name.invalid"),
          }),
        ostemplate: z.string().optional(),
        rootfs_size: z.number().min(8).max(500).optional(),
        template_id: z.number().optional(),
        disk_size: z.number().min(20).max(500).optional(),
        username: z.string().optional(),
        cores: z.number().min(1).max(8),
        memory: z.number().min(512).max(32768),
        gpu_mapping_id: z.string().optional(),
        password: z
          .string()
          .min(1, { message: t("validation:password.required") })
          .min(8, {
            message: t("validation:password.minLength", { count: 8 }),
          }),
        storage: z.string().default("local-lvm"),
        os_info: z.string().optional(),
        start_at: z.string().optional(),
        end_at: z.string().optional(),
        immediate_no_end: z.boolean().optional(),
      }),
    [t],
  )

  type FormData = z.input<typeof formSchema>

  const form = useForm<FormData>({
    resolver: standardSchemaResolver(formSchema),
    mode: "onBlur",
    criteriaMode: "all",
    defaultValues: {
      resource_type: "lxc",
      mode: "scheduled",
      reason: "",
      hostname: "",
      ostemplate: "",
      template_id: undefined,
      username: "",
      cores: 2,
      memory: 2048,
      gpu_mapping_id: "",
      disk_size: 20,
      rootfs_size: 8,
      password: "",
      storage: "local-lvm",
      os_info: "",
      start_at: "",
      end_at: "",
      immediate_no_end: true,
    },
  })

  const watchedResourceType = useWatch({
    control: form.control,
    name: "resource_type",
  })
  const watchedHostname = useWatch({ control: form.control, name: "hostname" })
  const watchedPassword = useWatch({ control: form.control, name: "password" })
  const watchedReason = useWatch({ control: form.control, name: "reason" })
  const watchedOsTemplate = useWatch({
    control: form.control,
    name: "ostemplate",
  })
  const watchedTemplateId = useWatch({
    control: form.control,
    name: "template_id",
  })
  const watchedUsername = useWatch({ control: form.control, name: "username" })
  const watchedCores = useWatch({ control: form.control, name: "cores" })
  const watchedMemory = useWatch({ control: form.control, name: "memory" })
  const watchedRootfsSize = useWatch({
    control: form.control,
    name: "rootfs_size",
  })
  const watchedDiskSize = useWatch({
    control: form.control,
    name: "disk_size",
  })
  const watchedStartAt = useWatch({ control: form.control, name: "start_at" })
  const watchedEndAt = useWatch({ control: form.control, name: "end_at" })
  const watchedMode = useWatch({ control: form.control, name: "mode" })
  const watchedImmediateNoEnd = useWatch({
    control: form.control,
    name: "immediate_no_end",
  })
  const watchedGpuMappingId = useWatch({
    control: form.control,
    name: "gpu_mapping_id",
  })
  const [researchStartDate, setResearchStartDate] = useState(() =>
    formatTaipeiDateInput(1),
  )
  const [researchEndDate, setResearchEndDate] = useState(() =>
    formatTaipeiDateInput(90),
  )
  const [quickTemplateAvailabilityAnchor, setQuickTemplateAvailabilityAnchor] =
    useState(() => new Date())

  function getSelectedTemplateLabel() {
    if (resourceType === "lxc") {
      if (!watchedOsTemplate) return serviceTemplateName || "尚未選擇"
      const matchedTemplate = lxcTemplates?.find(
        (template) => template.volid === watchedOsTemplate,
      )
      return (
        serviceTemplateName ||
        matchedTemplate?.volid.split("/").pop()?.replace(".tar.zst", "") ||
        watchedOsTemplate
      )
    }

    if (!watchedTemplateId) return "尚未選擇"
    return (
      vmTemplates?.find((template) => template.vmid === watchedTemplateId)
        ?.name || `Template #${watchedTemplateId}`
    )
  }

  const requestSpecSummary = useMemo(() => {
    const memoryGb = (Number(watchedMemory || 0) / 1024).toFixed(1)
    const storageGb =
      resourceType === "vm"
        ? Number(watchedDiskSize || 0)
        : Number(watchedRootfsSize || 0)
    const storageLabel = resourceType === "vm" ? "Disk" : "Rootfs"

    return `${Number(watchedCores || 0)} Core / ${memoryGb} GB RAM / ${storageGb} GB ${storageLabel}`
  }, [
    resourceType,
    watchedCores,
    watchedDiskSize,
    watchedMemory,
    watchedRootfsSize,
  ])

  const requestWindowSummary = useMemo(
    () => formatScheduleSummary(watchedStartAt, watchedEndAt),
    [watchedEndAt, watchedStartAt],
  )

  const isSubmitReady = useMemo(() => {
    const reasonReady = Boolean(watchedReason?.trim())
    const basicReady = Boolean(
      reasonReady && watchedHostname?.trim() && watchedPassword,
    )

    const isFixedStartMode =
      watchedMode === "immediate" || watchedMode === "quick_template"
    const slotReady = isFixedStartMode
      ? true
      : Boolean(watchedStartAt && watchedEndAt)

    if (!basicReady || !slotReady) return false

    if (watchedResourceType === "vm") {
      return Boolean(watchedTemplateId && watchedUsername?.trim())
    }

    return Boolean(watchedOsTemplate)
  }, [
    watchedHostname,
    watchedMode,
    watchedOsTemplate,
    watchedPassword,
    watchedReason,
    watchedResourceType,
    watchedStartAt,
    watchedTemplateId,
    watchedEndAt,
    watchedUsername,
  ])

  const requestReadinessLabel = isSubmitReady ? "可送出申請" : "尚有欄位待完成"

  const { data: lxcTemplates, isLoading: lxcTemplatesLoading } = useQuery({
    queryKey: queryKeys.resources.templates.lxc,
    queryFn: () => LxcService.getTemplates(),
    enabled: resourceType === "lxc",
  })

  const { data: vmTemplates, isLoading: vmTemplatesLoading } = useQuery({
    queryKey: queryKeys.resources.templates.vm,
    queryFn: () => VmService.getVmTemplates(),
    enabled: resourceType === "vm",
  })

  const isScheduledMode = watchedMode === "scheduled"
  const hasSelectedWindow = Boolean(watchedStartAt && watchedEndAt)
  const canLoadGpuOptions =
    resourceType === "vm" && (!isScheduledMode || hasSelectedWindow)
  const gpuOptionsQueryParams = useMemo(
    () =>
      isScheduledMode
        ? {
            startAt: watchedStartAt || undefined,
            endAt: watchedEndAt || undefined,
          }
        : undefined,
    [isScheduledMode, watchedEndAt, watchedStartAt],
  )

  const { data: gpuOptions } = useQuery({
    queryKey: queryKeys.gpu.options(gpuOptionsQueryParams),
    queryFn: () => GpuService.listOptions(gpuOptionsQueryParams),
    enabled: canLoadGpuOptions,
  })

  const quickTemplateAvailabilityRequest = useMemo(
    () => ({
      resource_type: "lxc" as const,
      cores: Number(watchedCores || 1),
      memory: Number(watchedMemory || 512),
      rootfs_size: Number(watchedRootfsSize || 8),
      gpu_required: 0,
      start_at: quickTemplateAvailabilityAnchor.toISOString(),
      end_at: addHoursIso(quickTemplateAvailabilityAnchor, 3),
      mode: "quick_template" as const,
    }),
    [
      quickTemplateAvailabilityAnchor,
      watchedCores,
      watchedMemory,
      watchedRootfsSize,
    ],
  )

  const canCheckQuickTemplateAvailability =
    watchedMode === "quick_template" &&
    resourceType === "lxc" &&
    Boolean(serviceTemplateSlug) &&
    Boolean(watchedCores && watchedMemory && watchedRootfsSize)

  const {
    data: quickTemplateAvailability,
    isFetching: quickTemplateAvailabilityLoading,
  } = useQuery({
    queryKey: queryKeys.vmRequests.availability.window(
      quickTemplateAvailabilityRequest,
    ),
    queryFn: () =>
      VmRequestsApi.windowAvailability({
        requestBody: quickTemplateAvailabilityRequest,
      }),
    enabled: canCheckQuickTemplateAvailability,
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  })

  const researchAvailabilityRequest = useMemo(
    () => ({
      resource_type: resourceType,
      cores: Number(watchedCores || 1),
      memory: Number(watchedMemory || 512),
      disk_size:
        resourceType === "vm" ? Number(watchedDiskSize || 20) : undefined,
      rootfs_size:
        resourceType === "lxc" ? Number(watchedRootfsSize || 8) : undefined,
      gpu_required: watchedGpuMappingId ? 1 : 0,
      start_at: watchedStartAt || "",
      end_at: watchedEndAt || "",
      mode: "research" as const,
    }),
    [
      resourceType,
      watchedCores,
      watchedDiskSize,
      watchedEndAt,
      watchedGpuMappingId,
      watchedMemory,
      watchedRootfsSize,
      watchedStartAt,
    ],
  )

  const canCheckResearchAvailability =
    watchedMode === "scheduled" &&
    Boolean(watchedStartAt && watchedEndAt) &&
    Boolean(watchedCores && watchedMemory) &&
    (resourceType === "vm"
      ? Boolean(watchedDiskSize)
      : Boolean(watchedRootfsSize))

  const {
    data: researchAvailability,
    isFetching: researchAvailabilityLoading,
  } = useQuery({
    queryKey: queryKeys.vmRequests.availability.window(
      researchAvailabilityRequest,
    ),
    queryFn: () =>
      VmRequestsApi.windowAvailability({
        requestBody: researchAvailabilityRequest,
      }),
    enabled: canCheckResearchAvailability,
    refetchOnWindowFocus: false,
    staleTime: 30_000,
  })

  const quickTemplateAvailabilityTone = getAvailabilityTone(
    quickTemplateAvailability,
  )
  const researchAvailabilityTone = getAvailabilityTone(researchAvailability)
  const availabilityBlocksSubmit =
    (watchedMode === "quick_template" &&
      quickTemplateAvailability?.feasible === false) ||
    (watchedMode === "scheduled" && researchAvailability?.feasible === false)
  const canSubmitRequest = isSubmitReady && !availabilityBlocksSubmit

  const updateFormValue = useCallback(
    (field: keyof FormData, value: FormData[keyof FormData]) => {
      form.setValue(field, value as never, {
        shouldDirty: true,
        shouldTouch: true,
        shouldValidate: true,
      })
    },
    [form],
  )

  useEffect(() => {
    if (watchedMode !== "scheduled") return
    const orderedEndDate = getOrderedEndDate(researchStartDate, researchEndDate)
    if (orderedEndDate !== researchEndDate) {
      setResearchEndDate(orderedEndDate)
      return
    }
    const endDate = addDaysToDateInput(orderedEndDate, 1)
    updateFormValue("start_at", toTaipeiDateTimeStart(researchStartDate))
    updateFormValue("end_at", toTaipeiDateTimeStart(endDate))
  }, [researchEndDate, researchStartDate, updateFormValue, watchedMode])

  useEffect(() => {
    if (!activeQuickStart) return

    setShowTemplateSelector(false)
    setServiceTemplateName("")
    setServiceTemplateSlug("")
    setServiceTemplateScriptPath("")
    setResourceType(activeQuickStart.resourceType)

    updateFormValue("resource_type", activeQuickStart.resourceType)
    updateFormValue("mode", "immediate")
    updateFormValue("immediate_no_end", true)
    updateFormValue("start_at", "")
    updateFormValue("end_at", "")
    updateFormValue("gpu_mapping_id", "")
    updateFormValue("template_id", undefined)
    updateFormValue("cores", activeQuickStart.defaultCores)
    updateFormValue("memory", activeQuickStart.defaultMemoryMb)
    updateFormValue("disk_size", activeQuickStart.defaultDiskGb)
    updateFormValue("username", activeQuickStart.defaultUsername)
    updateFormValue("os_info", activeQuickStart.osInfo)
    updateFormValue("reason", activeQuickStart.defaultReason)
  }, [activeQuickStart, updateFormValue])

  useEffect(() => {
    if (!activeQuickStart || resourceType !== "vm" || !vmTemplates?.length)
      return
    if (watchedTemplateId) return

    const matchedTemplateId = pickQuickStartVmTemplateId(
      vmTemplates,
      activeQuickStart,
    )
    if (matchedTemplateId) {
      updateFormValue("template_id", matchedTemplateId)
    }
  }, [
    activeQuickStart,
    resourceType,
    updateFormValue,
    vmTemplates,
    watchedTemplateId,
  ])

  useEffect(() => {
    if (resourceType !== "vm") return
    if (!canLoadGpuOptions && watchedGpuMappingId) {
      updateFormValue("gpu_mapping_id", "")
      return
    }
    if (!watchedGpuMappingId || !gpuOptions) return
    const exists = gpuOptions.some(
      (gpu) => gpu.mapping_id === watchedGpuMappingId,
    )
    if (!exists) {
      updateFormValue("gpu_mapping_id", "")
    }
  }, [
    canLoadGpuOptions,
    gpuOptions,
    resourceType,
    updateFormValue,
    watchedGpuMappingId,
  ])

  const selectedTemplateLabel = getSelectedTemplateLabel()

  const mutation = useMutation({
    mutationFn: (data: FormData) => {
      const payloadOptions = {
        lxcEnvironmentType:
          serviceTemplateName || t("resources:create.customSpec"),
        vmEnvironmentType:
          activeQuickStart?.resourceType === "vm"
            ? activeQuickStart.defaultEnvironmentType
            : t("resources:create.customSpec"),
        validationMessages: {
          lxcRequirements: t("validation:requirement.lxc"),
          vmRequirements: t("validation:requirement.vm"),
        },
      }

      const enrichedData = {
        ...data,
        service_template_slug: serviceTemplateSlug || undefined,
        service_template_script_path: serviceTemplateScriptPath || undefined,
      }

      return VmRequestsApi.create({
        requestBody: toVmRequestCreateRequestBody(enrichedData, payloadOptions),
      })
    },
    onSuccess: () => {
      showSuccessToast(t("messages:success.applicationSubmitted"))
      queryClient.invalidateQueries({ queryKey: queryKeys.vmRequests.all })
      // Refresh the "creating placeholder" list on /resources so the user
      // immediately sees the in-progress VM after auto-approve + background
      // provision kicks in.
      queryClient.invalidateQueries({ queryKey: ["pending-resources"] })
      navigate({ to: backPath })
    },
    onError: (err) => handleError.call(showErrorToast, err as ApiError),
  })

  const handleImportPlan = useCallback(
    (prefill: ImportedFormPrefill | undefined) => {
      if (!prefill) return

      const type = prefill.resource_type === "vm" ? "vm" : "lxc"

      setResourceType(type)
      updateFormValue("resource_type", type)

      if (prefill.hostname) {
        updateFormValue("hostname", normalizeHostname(prefill.hostname))
      }
      if (prefill.cores) updateFormValue("cores", prefill.cores)
      if (prefill.memory_mb) updateFormValue("memory", prefill.memory_mb)
      if (prefill.reason) updateFormValue("reason", prefill.reason)

      if (type === "lxc") {
        if (prefill.disk_gb) updateFormValue("rootfs_size", prefill.disk_gb)
        if (prefill.lxc_os_image) {
          updateFormValue("ostemplate", prefill.lxc_os_image)
        }
        if (prefill.service_template_slug) {
          setServiceTemplateSlug(prefill.service_template_slug)
          setServiceTemplateName(prefill.service_template_slug)
          setServiceTemplateScriptPath(`ct/${prefill.service_template_slug}.sh`)
          if (isStudentQuickTemplateUser) {
            updateFormValue("mode", "quick_template")
            updateFormValue("start_at", "")
            updateFormValue("end_at", "")
          }
        }
      } else {
        if (prefill.disk_gb) updateFormValue("disk_size", prefill.disk_gb)
        if (prefill.vm_template_id) {
          updateFormValue("template_id", prefill.vm_template_id)
        }
        if (prefill.username) updateFormValue("username", prefill.username)
        if (prefill.gpu_mapping_id) {
          updateFormValue("gpu_mapping_id", prefill.gpu_mapping_id)
        }
      }

      if (prefill.start_at) {
        updateFormValue("start_at", prefill.start_at)
      }
      if (prefill.end_at) {
        updateFormValue("end_at", prefill.end_at)
      }
    },
    [isStudentQuickTemplateUser, updateFormValue],
  )

  const handleImportReason = useCallback(
    (reason: string) => {
      if (!reason) return

      updateFormValue("reason", reason)
      showSuccessToast(t("applications:aiChat.reasonImportSuccess"))
    },
    [showSuccessToast, t, updateFormValue],
  )

  const handleSelectTemplate = useCallback(
    (template: FastTemplate) => {
      setServiceTemplateName(template.name || "")
      setServiceTemplateSlug(template.slug || "")
      const method = template.install_methods?.[0]
      setServiceTemplateScriptPath(
        method?.script || (template.slug ? `ct/${template.slug}.sh` : ""),
      )
      setResourceType("lxc")
      updateFormValue("resource_type", "lxc")
      // 帶入模板的預設資源值
      if (method?.resources) {
        if (method.resources.cpu) {
          updateFormValue(
            "cores",
            isStudentQuickTemplateUser
              ? Math.min(method.resources.cpu, 2)
              : method.resources.cpu,
          )
        }
        if (method.resources.ram)
          updateFormValue(
            "memory",
            isStudentQuickTemplateUser
              ? Math.min(method.resources.ram, 4096)
              : method.resources.ram,
          )
        if (method.resources.hdd)
          updateFormValue(
            "rootfs_size",
            isStudentQuickTemplateUser
              ? Math.min(Math.max(method.resources.hdd, 8), 32)
              : Math.max(method.resources.hdd, 8),
          )
      }
      // 自動挑選符合模板要求 OS / version 的 ostemplate volid
      const volids = (lxcTemplates ?? []).map((t) => t.volid)
      const picked = pickMatchingOsTemplate(volids, method?.resources)
      if (picked) {
        updateFormValue("ostemplate", picked)
      }
      if (isStudentQuickTemplateUser) {
        updateFormValue("mode", "quick_template")
        updateFormValue("start_at", "")
        updateFormValue("end_at", "")
        updateFormValue("gpu_mapping_id", "")
        if (!watchedReason?.trim()) {
          updateFormValue(
            "reason",
            `Quick template lab environment for ${template.name || template.slug || "service testing"}.`,
          )
        }
      }
      setShowTemplateSelector(false)
    },
    [isStudentQuickTemplateUser, lxcTemplates, updateFormValue, watchedReason],
  )

  const onSubmit = useCallback(
    async (data: FormData) => {
      const selectedGpuId = data.gpu_mapping_id?.trim()

      if (data.resource_type === "vm" && selectedGpuId) {
        const queryParams =
          data.mode === "scheduled"
            ? {
                startAt: data.start_at || undefined,
                endAt: data.end_at || undefined,
              }
            : undefined

        const latestGpuOptions = await queryClient.fetchQuery({
          queryKey: queryKeys.gpu.options(queryParams),
          queryFn: () => GpuService.listOptions(queryParams),
          staleTime: 0,
        })

        const selectedGpu = latestGpuOptions.find(
          (gpu) => gpu.mapping_id === selectedGpuId,
        )

        if (!selectedGpu) {
          showErrorToast("目前所選時段的 GPU 已不可用，請重新選擇時段或 GPU。")
          return
        }
      }

      mutation.mutate(data)
    },
    [mutation, queryClient, showErrorToast],
  )

  useEffect(() => {
    const updateDesktopPanelFrame = () => {
      const node = aiColumnRef.current
      if (!node || window.innerWidth < 1024) {
        setDesktopPanelFrame(null)
        return
      }

      const rect = node.getBoundingClientRect()
      const footerNode = document.querySelector<HTMLElement>(
        '[data-app-footer="fixed"]',
      )
      const footerHeight = footerNode?.getBoundingClientRect().height ?? 0
      setDesktopPanelFrame({
        height: Math.max(
          320,
          window.innerHeight - rect.top - footerHeight - AI_PANEL_BOTTOM_GAP,
        ),
        left: rect.left,
        top: rect.top,
        width: rect.width,
      })
    }

    updateDesktopPanelFrame()

    const node = aiColumnRef.current
    const resizeObserver =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(() => updateDesktopPanelFrame())
        : null

    if (node && resizeObserver) {
      resizeObserver.observe(node)
    }

    window.addEventListener("resize", updateDesktopPanelFrame)

    return () => {
      resizeObserver?.disconnect()
      window.removeEventListener("resize", updateDesktopPanelFrame)
    }
  }, [])

  const desktopPanelStyle = useMemo<CSSProperties | undefined>(() => {
    if (!desktopPanelFrame) return undefined

    return {
      height: `${desktopPanelFrame.height}px`,
      left: `${desktopPanelFrame.left}px`,
      position: "fixed",
      top: `${desktopPanelFrame.top}px`,
      width: `${desktopPanelFrame.width}px`,
    }
  }, [desktopPanelFrame])

  const aiColumnStyle = useMemo<CSSProperties | undefined>(() => {
    if (!desktopPanelFrame) return undefined

    return {
      height: `${desktopPanelFrame.height}px`,
    }
  }, [desktopPanelFrame])

  return (
    <div
      className={`mx-auto flex w-full ${showAiAssistant ? "max-w-[1180px]" : "max-w-[760px]"} flex-col gap-6`}
    >
      {activeQuickStart && (
        <section className="rounded-2xl border border-primary/20 bg-primary/5 px-5 py-4">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0 space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="secondary">快速入門</Badge>
                <Badge variant="outline">
                  {isQuickStartAiMode ? "AI 幫你選環境" : "標準模板"}
                </Badge>
                <Badge variant="outline">VM</Badge>
              </div>
              <div>
                <h2 className="text-base font-semibold tracking-tight">
                  {activeQuickStart.title}
                </h2>
                <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
                  已帶入預設設定，先填 VM 名稱與密碼即可。
                </p>
              </div>
              <div className="hidden flex-wrap gap-2 text-xs text-muted-foreground">
                <span className="rounded-full border border-border/60 bg-background/80 px-3 py-1">
                  預設規格：{activeQuickStart.defaultCores} Core /{" "}
                  {(activeQuickStart.defaultMemoryMb / 1024).toFixed(1)} GB RAM
                  / {activeQuickStart.defaultDiskGb} GB Disk
                </span>
                <span className="rounded-full border border-border/60 bg-background/80 px-3 py-1">
                  預設帳號：{activeQuickStart.defaultUsername}
                </span>
              </div>
            </div>

            <div className="hidden rounded-2xl border border-border/60 bg-background/80 px-4 py-3 text-sm text-muted-foreground lg:max-w-sm">
              <div className="font-medium text-foreground">
                目前只需要你補：
              </div>
              <div className="mt-2 leading-6">
                1. VM 名稱
                <br />
                2. 登入密碼
                <br />
                3. 確認後送出申請
              </div>
              {isQuickStartAiMode && (
                <div className="mt-3 text-xs leading-5 text-primary">
                  AI 會自動根據這個情境建立建議並預填表單。
                </div>
              )}
            </div>
          </div>
        </section>
      )}

      <div
        className={`grid items-start gap-6 ${showAiAssistant ? "lg:grid-cols-[minmax(0,1fr)_400px] xl:grid-cols-[minmax(0,1fr)_420px]" : ""}`}
      >
        <div className="min-w-0 max-w-[760px] space-y-6">
          <div className="flex items-start gap-3">
            <Button
              variant="outline"
              size="icon"
              className="mt-0.5 shrink-0"
              onClick={() => navigate({ to: backPath })}
              aria-label={t("common:buttons.back")}
            >
              <ArrowLeft className="h-4 w-4" />
            </Button>
            <div className="min-w-0">
              <h1 className="text-2xl font-bold tracking-tight">
                {t("applications:create.heading")}
              </h1>
              <p className="text-muted-foreground">
                {t("applications:create.description")}
              </p>
            </div>
          </div>

          {showTemplateSelector ? (
            <FastTemplatesTab
              onSelectTemplate={handleSelectTemplate}
              onBack={() => setShowTemplateSelector(false)}
            />
          ) : (
            <Form {...form}>
              <form
                onSubmit={form.handleSubmit(onSubmit)}
                className="space-y-6"
              >
                {(user?.is_superuser ||
                  user?.role === "admin" ||
                  user?.role === "teacher") && (
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => form.setValue("mode", "scheduled")}
                      className={cn(
                        "rounded-lg border px-4 py-2 text-sm transition-colors",
                        watchedMode === "scheduled"
                          ? "border-primary bg-primary/10 text-primary"
                          : "border-border text-muted-foreground hover:bg-muted/50",
                      )}
                    >
                      預約模式
                    </button>
                    <button
                      type="button"
                      onClick={() => form.setValue("mode", "immediate")}
                      className={cn(
                        "rounded-lg border px-4 py-2 text-sm transition-colors",
                        watchedMode === "immediate"
                          ? "border-primary bg-primary/10 text-primary"
                          : "border-border text-muted-foreground hover:bg-muted/50",
                      )}
                    >
                      立即模式
                    </button>
                  </div>
                )}

                <Tabs
                  value={resourceType}
                  onValueChange={(value) => {
                    const nextType = value as "lxc" | "vm"
                    setResourceType(nextType)
                    updateFormValue("resource_type", nextType)
                  }}
                  className="w-full"
                >
                  <TabsList className="grid w-full grid-cols-2">
                    <TabsTrigger value="lxc">
                      {t("resources:form.type.lxc")}
                    </TabsTrigger>
                    <TabsTrigger value="vm">
                      {t("resources:form.type.qemu")}
                    </TabsTrigger>
                  </TabsList>

                  <TabsContent value="lxc" className="mt-6 space-y-6">
                    <div className="space-y-5">
                      <FormField
                        control={form.control}
                        name="hostname"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>
                              {t("resources:form.name")}{" "}
                              <span className="text-destructive">*</span>
                            </FormLabel>
                            <FormControl>
                              <Input
                                {...field}
                                placeholder="project-alpha-web"
                                onBlur={(event) => {
                                  const normalized = normalizeHostname(
                                    event.target.value,
                                  )
                                  field.onChange(normalized)
                                  field.onBlur()
                                }}
                                required
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={form.control}
                        name="ostemplate"
                        render={({ field }) => (
                          <FormItem
                            className={
                              serviceTemplateSlug ? "hidden" : undefined
                            }
                          >
                            <FormLabel>
                              {t("resources:form.osTemplate")}{" "}
                              <span className="text-destructive">*</span>
                            </FormLabel>
                            <Select
                              onValueChange={field.onChange}
                              value={field.value}
                            >
                              <FormControl>
                                <SelectTrigger>
                                  <SelectValue
                                    placeholder={t("resources:form.os")}
                                  />
                                </SelectTrigger>
                              </FormControl>
                              <SelectContent>
                                {lxcTemplatesLoading ? (
                                  <SelectItem value="loading" disabled>
                                    {t("common:status.loading")}
                                  </SelectItem>
                                ) : lxcTemplates && lxcTemplates.length > 0 ? (
                                  lxcTemplates.map((template) => (
                                    <SelectItem
                                      key={template.volid}
                                      value={template.volid}
                                    >
                                      {template.volid
                                        .split("/")
                                        .pop()
                                        ?.replace(".tar.zst", "")}
                                    </SelectItem>
                                  ))
                                ) : (
                                  <SelectItem value="none" disabled>
                                    {t("common:common.none")}
                                  </SelectItem>
                                )}
                              </SelectContent>
                            </Select>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormItem>
                        <FormLabel>
                          {t("applications:form.serviceTemplate")}
                        </FormLabel>
                        {serviceTemplateName ? (
                          <div className="flex items-center gap-2 rounded-md border bg-muted/30 px-3 py-2">
                            <LayoutTemplate className="h-4 w-4 shrink-0 text-primary" />
                            <div className="flex-1 min-w-0">
                              <span className="block truncate text-sm font-medium">
                                {serviceTemplateName}
                              </span>
                              {serviceTemplateSlug ? (
                                <span className="block truncate text-xs text-muted-foreground">
                                  {serviceTemplateSlug}
                                </span>
                              ) : null}
                            </div>
                            <Button
                              type="button"
                              variant="ghost"
                              size="icon"
                              className="h-6 w-6 shrink-0"
                              onClick={() => {
                                setServiceTemplateName("")
                                setServiceTemplateSlug("")
                                setServiceTemplateScriptPath("")
                                if (watchedMode === "quick_template") {
                                  updateFormValue("mode", "scheduled")
                                }
                              }}
                            >
                              <X className="h-3.5 w-3.5" />
                            </Button>
                          </div>
                        ) : (
                          <Button
                            type="button"
                            variant="outline"
                            className="w-full justify-start gap-2 text-muted-foreground"
                            onClick={() => setShowTemplateSelector(true)}
                          >
                            <LayoutTemplate className="h-4 w-4" />
                            {t("applications:form.selectTemplate")}
                          </Button>
                        )}
                      </FormItem>

                      <FormField
                        control={form.control}
                        name="os_info"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>{t("resources:form.osInfo")}</FormLabel>
                            <FormControl>
                              <Input
                                {...field}
                                placeholder="Ubuntu 22.04 LTS"
                              />
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
                              {t("resources:form.rootPassword")}{" "}
                              <span className="text-destructive">*</span>
                            </FormLabel>
                            <FormControl>
                              <Input
                                {...field}
                                placeholder="root password"
                                type="password"
                                required
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    </div>

                    <div className="rounded-2xl border bg-muted/20 p-5">
                      <h3 className="mb-4 font-medium">
                        {t("resources:form.hardware")}
                      </h3>
                      <div className="space-y-5">
                        <FormField
                          control={form.control}
                          name="cores"
                          render={({ field }) => (
                            <FormItem>
                              <div className="flex items-center justify-between">
                                <FormLabel>
                                  {t("resources:form.cpuCores")}
                                </FormLabel>
                                <span className="text-sm font-semibold text-primary">
                                  {field.value} Cores
                                </span>
                              </div>
                              <FormControl>
                                <Slider
                                  min={1}
                                  max={8}
                                  step={1}
                                  value={[field.value]}
                                  onValueChange={(values) =>
                                    field.onChange(values[0])
                                  }
                                />
                              </FormControl>
                              <div className="flex justify-between text-xs text-muted-foreground">
                                <span>1</span>
                                <span>2</span>
                                <span>4</span>
                                <span>8</span>
                              </div>
                              <FormMessage />
                            </FormItem>
                          )}
                        />

                        <FormField
                          control={form.control}
                          name="memory"
                          render={({ field }) => (
                            <FormItem>
                              <div className="flex items-center justify-between">
                                <FormLabel>
                                  {t("resources:form.memory")}
                                </FormLabel>
                                <span className="text-sm font-semibold text-primary">
                                  {(field.value / 1024).toFixed(1)} GB
                                </span>
                              </div>
                              <FormControl>
                                <Slider
                                  min={512}
                                  max={32768}
                                  step={512}
                                  value={[field.value]}
                                  onValueChange={(values) =>
                                    field.onChange(values[0])
                                  }
                                />
                              </FormControl>
                              <div className="flex justify-between text-xs text-muted-foreground">
                                <span>1GB</span>
                                <span>8GB</span>
                                <span>16GB</span>
                                <span>32GB</span>
                              </div>
                              <FormMessage />
                            </FormItem>
                          )}
                        />

                        <FormField
                          control={form.control}
                          name="rootfs_size"
                          render={({ field }) => (
                            <FormItem>
                              <div className="flex items-center justify-between">
                                <FormLabel>
                                  {t("resources:form.disk")}
                                </FormLabel>
                                <div className="flex items-center gap-2">
                                  <Input
                                    className="h-8 w-20 text-right"
                                    type="number"
                                    min={8}
                                    max={500}
                                    value={field.value ?? 8}
                                    onChange={(event) =>
                                      field.onChange(
                                        Number.parseInt(
                                          event.target.value,
                                          10,
                                        ) || 8,
                                      )
                                    }
                                  />
                                  <span className="text-sm font-semibold text-primary">
                                    GB
                                  </span>
                                </div>
                              </div>
                              <FormControl>
                                <Slider
                                  min={8}
                                  max={500}
                                  step={1}
                                  value={[field.value ?? 8]}
                                  onValueChange={(values) =>
                                    field.onChange(values[0])
                                  }
                                />
                              </FormControl>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                      </div>
                    </div>
                  </TabsContent>

                  <TabsContent value="vm" className="mt-6 space-y-6">
                    <div className="space-y-5">
                      <FormField
                        control={form.control}
                        name="hostname"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>
                              {t("resources:form.vmName")}{" "}
                              <span className="text-destructive">*</span>
                            </FormLabel>
                            <FormControl>
                              <Input
                                {...field}
                                placeholder="web-server-01"
                                onBlur={(event) => {
                                  const normalized = normalizeHostname(
                                    event.target.value,
                                  )
                                  field.onChange(normalized)
                                  field.onBlur()
                                }}
                                required
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={form.control}
                        name="template_id"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>
                              {t("resources:form.os")}{" "}
                              <span className="text-destructive">*</span>
                            </FormLabel>
                            <Select
                              onValueChange={(value) =>
                                field.onChange(Number.parseInt(value, 10))
                              }
                              value={field.value?.toString()}
                            >
                              <FormControl>
                                <SelectTrigger>
                                  <SelectValue
                                    placeholder={t("resources:form.os")}
                                  />
                                </SelectTrigger>
                              </FormControl>
                              <SelectContent>
                                {vmTemplatesLoading ? (
                                  <SelectItem value="loading" disabled>
                                    {t("common:status.loading")}
                                  </SelectItem>
                                ) : vmTemplates && vmTemplates.length > 0 ? (
                                  vmTemplates.map((template) => (
                                    <SelectItem
                                      key={template.vmid}
                                      value={template.vmid.toString()}
                                    >
                                      {template.name}
                                    </SelectItem>
                                  ))
                                ) : (
                                  <SelectItem value="none" disabled>
                                    {t("common:common.none")}
                                  </SelectItem>
                                )}
                              </SelectContent>
                            </Select>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={form.control}
                        name="os_info"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>{t("resources:form.osInfo")}</FormLabel>
                            <FormControl>
                              <Input
                                {...field}
                                placeholder="Ubuntu 22.04 LTS"
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={form.control}
                        name="username"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>
                              {t("resources:form.username")}{" "}
                              <span className="text-destructive">*</span>
                            </FormLabel>
                            <FormControl>
                              <Input {...field} placeholder="admin" required />
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
                              {t("resources:form.password")}{" "}
                              <span className="text-destructive">*</span>
                            </FormLabel>
                            <FormControl>
                              <Input
                                {...field}
                                placeholder="password"
                                type="password"
                                required
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    </div>

                    <div className="rounded-2xl border bg-muted/20 p-5">
                      <h3 className="mb-4 font-medium">
                        {t("resources:form.hardware")}
                      </h3>
                      <div className="space-y-5">
                        <FormField
                          control={form.control}
                          name="cores"
                          render={({ field }) => (
                            <FormItem>
                              <div className="flex items-center justify-between">
                                <FormLabel>
                                  {t("resources:form.cpuCores")}
                                </FormLabel>
                                <span className="text-sm font-semibold text-primary">
                                  {field.value} Cores
                                </span>
                              </div>
                              <FormControl>
                                <Slider
                                  min={1}
                                  max={8}
                                  step={1}
                                  value={[field.value]}
                                  onValueChange={(values) =>
                                    field.onChange(values[0])
                                  }
                                />
                              </FormControl>
                              <div className="flex justify-between text-xs text-muted-foreground">
                                <span>1</span>
                                <span>2</span>
                                <span>4</span>
                                <span>8</span>
                              </div>
                              <FormMessage />
                            </FormItem>
                          )}
                        />

                        <FormField
                          control={form.control}
                          name="memory"
                          render={({ field }) => (
                            <FormItem>
                              <div className="flex items-center justify-between">
                                <FormLabel>
                                  {t("resources:form.memory")}
                                </FormLabel>
                                <span className="text-sm font-semibold text-primary">
                                  {(field.value / 1024).toFixed(1)} GB
                                </span>
                              </div>
                              <FormControl>
                                <Slider
                                  min={512}
                                  max={32768}
                                  step={512}
                                  value={[field.value]}
                                  onValueChange={(values) =>
                                    field.onChange(values[0])
                                  }
                                />
                              </FormControl>
                              <div className="flex justify-between text-xs text-muted-foreground">
                                <span>1GB</span>
                                <span>8GB</span>
                                <span>16GB</span>
                                <span>32GB</span>
                              </div>
                              <FormMessage />
                            </FormItem>
                          )}
                        />

                        <FormField
                          control={form.control}
                          name="disk_size"
                          render={({ field }) => (
                            <FormItem>
                              <div className="flex items-center justify-between">
                                <FormLabel>
                                  {t("resources:form.disk")}
                                </FormLabel>
                                <div className="flex items-center gap-2">
                                  <Input
                                    className="h-8 w-20 text-right"
                                    type="number"
                                    min={20}
                                    max={500}
                                    value={field.value ?? 20}
                                    onChange={(event) =>
                                      field.onChange(
                                        Number.parseInt(
                                          event.target.value,
                                          10,
                                        ) || 20,
                                      )
                                    }
                                  />
                                  <span className="text-sm font-semibold text-primary">
                                    GB
                                  </span>
                                </div>
                              </div>
                              <FormControl>
                                <Slider
                                  min={20}
                                  max={500}
                                  step={1}
                                  value={[field.value ?? 20]}
                                  onValueChange={(values) =>
                                    field.onChange(values[0])
                                  }
                                />
                              </FormControl>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                      </div>
                    </div>

                    {/* GPU Selection */}
                    {resourceType === "vm" && (
                      <div className="rounded-2xl border bg-muted/20 p-5">
                        <h3 className="mb-4 font-medium">GPU 加速</h3>
                        {isScheduledMode && !hasSelectedWindow && (
                          <p className="mb-3 text-xs text-muted-foreground">
                            請先選擇租借時段，再載入該時段可用的 GPU。
                          </p>
                        )}
                        {canLoadGpuOptions &&
                          (!gpuOptions || gpuOptions.length === 0) && (
                            <p className="mb-3 text-xs text-muted-foreground">
                              此時段目前沒有可用 GPU，可改選其他時段或不使用
                              GPU。
                            </p>
                          )}
                        <FormField
                          control={form.control}
                          name="gpu_mapping_id"
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>選擇 GPU（可選）</FormLabel>
                              <Select
                                disabled={
                                  !canLoadGpuOptions ||
                                  !gpuOptions ||
                                  gpuOptions.length === 0
                                }
                                onValueChange={(value) =>
                                  field.onChange(
                                    value === "__none__" ? "" : value,
                                  )
                                }
                                value={field.value || "__none__"}
                              >
                                <FormControl>
                                  <SelectTrigger>
                                    <SelectValue
                                      placeholder={
                                        canLoadGpuOptions
                                          ? "不需要 GPU"
                                          : "請先選擇時段"
                                      }
                                    />
                                  </SelectTrigger>
                                </FormControl>
                                <SelectContent>
                                  <SelectItem value="__none__">
                                    不需要 GPU
                                  </SelectItem>
                                  {(gpuOptions || []).map((gpu: GPUSummary) => (
                                    <SelectItem
                                      key={gpu.mapping_id}
                                      value={gpu.mapping_id}
                                      disabled={gpu.available_count <= 0}
                                    >
                                      <div className="flex items-center gap-2">
                                        <span>
                                          {gpu.description || gpu.mapping_id}
                                        </span>
                                        {gpu.has_mdev && (
                                          <span className="text-xs text-blue-500">
                                            vGPU
                                          </span>
                                        )}
                                        {gpu.total_vram_mb > 0 && (
                                          <span className="text-xs text-muted-foreground">
                                            (
                                            {gpu.total_vram_mb >= 1024
                                              ? `${(gpu.total_vram_mb / 1024).toFixed(0)} GB`
                                              : `${gpu.total_vram_mb} MB`}
                                            {gpu.has_mdev &&
                                            gpu.used_vram_mb > 0
                                              ? `, 已分配 ${gpu.used_vram_mb >= 1024 ? `${(gpu.used_vram_mb / 1024).toFixed(0)} GB` : `${gpu.used_vram_mb} MB`}`
                                              : ""}
                                            )
                                          </span>
                                        )}
                                        {!gpu.total_vram_mb && gpu.vram && (
                                          <span className="text-xs text-muted-foreground">
                                            ({gpu.vram})
                                          </span>
                                        )}
                                        <span className="text-xs text-muted-foreground">
                                          [{gpu.available_count}/
                                          {gpu.device_count} 可用]
                                        </span>
                                        {gpu.available_count <= 0 && (
                                          <span className="text-xs text-destructive">
                                            已滿
                                          </span>
                                        )}
                                      </div>
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                              <p className="text-xs text-muted-foreground">
                                GPU
                                會依所選時段重新計算可用性，送出前仍會再做一次檢查。
                              </p>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                      </div>
                    )}
                  </TabsContent>

                  <FormField
                    control={form.control}
                    name="reason"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>
                          {t("applications:form.reason")}{" "}
                          <span className="text-destructive">*</span>
                        </FormLabel>
                        <FormControl>
                          <Textarea
                            {...field}
                            className="min-h-[140px] resize-y"
                            placeholder={t(
                              "applications:form.reasonPlaceholder",
                            )}
                            required
                          />
                        </FormControl>
                        <div className="flex justify-end text-xs text-muted-foreground">
                          {(field.value || "").length}
                        </div>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </Tabs>
                {watchedMode === "scheduled" ? (
                  <div className="rounded-2xl border bg-muted/20 p-5 space-y-4">
                    <div>
                      <h3 className="font-medium">研究申請期間</h3>
                      <p className="mt-1 text-sm text-muted-foreground">
                        研究申請以天為單位，送出後會等待管理員審核。
                      </p>
                    </div>
                    <div className="grid gap-4 sm:grid-cols-2">
                      <div className="space-y-2">
                        <FormLabel>開始日期</FormLabel>
                        <Input
                          type="date"
                          value={researchStartDate}
                          onChange={(event) =>
                            setResearchStartDate(event.target.value)
                          }
                        />
                      </div>
                      <div className="space-y-2">
                        <FormLabel>結束日期</FormLabel>
                        <Input
                          type="date"
                          min={researchStartDate}
                          value={researchEndDate}
                          onChange={(event) =>
                            setResearchEndDate(event.target.value)
                          }
                        />
                      </div>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      系統會送出 {watchedStartAt || "-"} 到{" "}
                      {watchedEndAt || "-"}{" "}
                      的研究資源申請，結束日期會包含完整當日。
                    </p>
                    <div
                      className={cn(
                        "rounded-xl border px-4 py-3",
                        researchAvailabilityTone.panel,
                      )}
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="text-sm font-medium">容量評估</div>
                        <Badge variant={researchAvailabilityTone.badge}>
                          {researchAvailabilityLoading
                            ? "評估中"
                            : researchAvailabilityTone.label}
                        </Badge>
                      </div>
                      <p
                        className={cn(
                          "mt-2 text-sm leading-6",
                          researchAvailabilityTone.text,
                        )}
                      >
                        {researchAvailabilityLoading
                          ? "正在檢查整段研究期間的保留容量。"
                          : researchAvailability?.summary ||
                            "選擇日期後會評估整段期間是否有足夠容量。"}
                      </p>
                      {researchAvailability && (
                        <div className="mt-2 space-y-1 text-xs text-muted-foreground">
                          <div>
                            評估期間：{researchAvailability.duration_days} 天，
                            檢查 {researchAvailability.checked_checkpoint_count}{" "}
                            個時間點
                          </div>
                          {researchAvailability.reason && (
                            <div>{researchAvailability.reason}</div>
                          )}
                          {researchAvailability.warnings.map((warning) => (
                            <div key={warning}>{warning}</div>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                ) : watchedMode === "quick_template" ? (
                  <div className="rounded-2xl border border-primary/20 bg-primary/5 p-5 space-y-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <h3 className="font-medium">快速模板</h3>
                      <div className="flex items-center gap-2">
                        <Badge variant={quickTemplateAvailabilityTone.badge}>
                          {quickTemplateAvailabilityLoading
                            ? "檢查中"
                            : quickTemplateAvailabilityTone.label}
                        </Badge>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          onClick={() =>
                            setQuickTemplateAvailabilityAnchor(new Date())
                          }
                          title="重新檢查容量"
                        >
                          <RefreshCw className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </div>
                    <p className="text-sm text-muted-foreground">
                      有可用容量時會自動核准並立即建立，使用時間固定 3
                      小時，到期後由系統自動關機。
                    </p>
                    <div
                      className={cn(
                        "rounded-xl border px-4 py-3",
                        quickTemplateAvailabilityTone.panel,
                      )}
                    >
                      <p
                        className={cn(
                          "text-sm leading-6",
                          quickTemplateAvailabilityTone.text,
                        )}
                      >
                        {quickTemplateAvailabilityLoading
                          ? "正在檢查現在是否可立即建立。"
                          : quickTemplateAvailability?.summary ||
                            (serviceTemplateSlug
                              ? "系統會檢查現在到 3 小時後是否有足夠容量。"
                              : "選擇快速模板後會檢查現在是否可建立。")}
                      </p>
                      {quickTemplateAvailability?.reason && (
                        <p className="mt-1 text-xs text-muted-foreground">
                          {quickTemplateAvailability.reason}
                        </p>
                      )}
                    </div>
                  </div>
                ) : (
                  <div className="rounded-2xl border bg-muted/20 p-5 space-y-4">
                    <h3 className="font-medium">立即模式設定</h3>
                    <p className="text-sm text-muted-foreground">
                      立即模式會在送出申請後馬上開始部署，不需要選擇開始時間。
                    </p>
                    <div className="flex items-center gap-2">
                      <Checkbox
                        id="immediate-no-end"
                        checked={watchedImmediateNoEnd ?? true}
                        onCheckedChange={(checked) =>
                          form.setValue("immediate_no_end", Boolean(checked))
                        }
                      />
                      <label
                        htmlFor="immediate-no-end"
                        className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70"
                      >
                        無限期 (No end date)
                      </label>
                    </div>
                    {!watchedImmediateNoEnd && (
                      <FormField
                        control={form.control}
                        name="end_at"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>結束時間</FormLabel>
                            <FormControl>
                              <Input
                                type="datetime-local"
                                value={field.value ?? ""}
                                onChange={field.onChange}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    )}
                  </div>
                )}
                <section className="hidden rounded-2xl border bg-card/60 p-5">
                  <div className="flex flex-col gap-3 border-b border-border/70 pb-4 sm:flex-row sm:items-start sm:justify-between">
                    <div>
                      <h3 className="text-lg font-semibold tracking-tight">
                        申請摘要
                      </h3>
                      <p className="text-sm text-muted-foreground">
                        送出前先確認這筆申請會提交的內容，讓這裡的顯示資訊和審核頁看到的摘要更一致。
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="rounded-full border border-border/70 bg-background/70 px-3 py-1 text-xs font-medium text-muted-foreground">
                        {resourceType === "vm" ? "QEMU 虛擬機" : "LXC 容器"}
                      </span>
                      <span className="rounded-full bg-teal-500/15 px-3 py-1 text-xs font-medium text-teal-300">
                        {requestReadinessLabel}
                      </span>
                    </div>
                  </div>

                  <div className="mt-4 grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
                    <div className="rounded-xl border border-border/70 bg-background/40 px-4 py-2">
                      <SummaryRow
                        label="主機名稱"
                        value={watchedHostname?.trim() || "尚未填寫"}
                      />
                      <SummaryRow
                        label="映像 / 模板"
                        value={selectedTemplateLabel}
                      />
                      <SummaryRow label="規格" value={requestSpecSummary} />
                      <SummaryRow label="時段" value={requestWindowSummary} />
                    </div>

                    <div className="rounded-xl border border-border/70 bg-background/40 px-4 py-3">
                      <div className="text-xs uppercase tracking-[0.18em] text-muted-foreground">
                        申請原因
                      </div>
                      <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-6 text-foreground/90">
                        {watchedReason?.trim() ||
                          "尚未填寫申請原因。建議在這裡說明用途、課程或專案背景，以及需要這個時段的原因。"}
                      </p>
                    </div>
                  </div>
                </section>
                <div className="flex flex-col gap-3 border-t pt-6 sm:flex-row sm:items-center sm:justify-between">
                  {showAiAssistant ? (
                    <p className="text-sm text-muted-foreground">
                      使用 AI 協助整理需求後，再確認規格、可申請時段與申請原因。
                    </p>
                  ) : (
                    <div />
                  )}
                  <div className="flex flex-col-reverse gap-3 sm:flex-row">
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() =>
                        navigate({
                          to: backPath,
                        })
                      }
                      disabled={mutation.isPending}
                    >
                      {t("common:buttons.cancel")}
                    </Button>
                    <LoadingButton
                      type="submit"
                      loading={mutation.isPending}
                      disabled={!canSubmitRequest}
                    >
                      {t("applications:create.submitButton")}
                    </LoadingButton>
                  </div>
                </div>
              </form>
            </Form>
          )}
        </div>

        {showAiAssistant && (
          <aside
            ref={aiColumnRef}
            className="min-w-0 lg:min-h-[32rem]"
            style={aiColumnStyle}
          >
            <div
              className="h-full overflow-hidden glass-panel rounded-2xl p-3"
              style={desktopPanelStyle}
            >
              <AiChatPanel
                autoImportPlan={isQuickStartAiMode}
                onImportPlan={handleImportPlan}
                onImportReason={handleImportReason}
                quickStartPrompt={
                  isQuickStartAiMode ? activeQuickStart?.aiPrompt : undefined
                }
                recommendationContext={{
                  resource_type: resourceType,
                  mode:
                    watchedMode === "quick_template"
                      ? "immediate"
                      : watchedMode,
                  start_at: watchedStartAt || undefined,
                  end_at: watchedEndAt || undefined,
                  selected_gpu_mapping_id: watchedGpuMappingId || undefined,
                  gpu_options: gpuOptions || [],
                }}
              />
            </div>
          </aside>
        )}
      </div>
    </div>
  )
}
