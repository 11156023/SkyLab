import { standardSchemaResolver } from "@hookform/resolvers/standard-schema"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link, useNavigate } from "@tanstack/react-router"
import {
  ArrowLeft,
  ChevronDown,
  ChevronUp,
  Cpu,
  Globe,
  HardDrive,
  LayoutTemplate,
  Lock,
  MemoryStick,
  Network,
  Plug,
  Shield,
  X,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useState } from "react"
import { useForm, useWatch } from "react-hook-form"
import { useTranslation } from "react-i18next"
import { z } from "zod"

import { LxcService, VmService } from "@/client"
import { AiChatPanel } from "@/components/Applications/AiChatPanel"
import {
  type FastTemplate,
  FastTemplatesTab,
} from "@/components/Applications/FastTemplatesTab"
import { Button } from "@/components/ui/button"
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
import useCustomToast from "@/hooks/useCustomToast"
import { queryKeys } from "@/lib/queryKeys"
import {
  toLxcCreateRequestBody,
  toVmCreateRequestBody,
} from "@/lib/resourcePayloads"
import { pickMatchingOsTemplate } from "@/lib/serviceTemplates"
import {
  generateQuickStartHostname,
  getQuickStartTemplate,
  type QuickStartTemplateSlug,
} from "@/lib/templateQuickStart"
import type { FormPrefill } from "@/services/aiTemplateRecommendation"
import { FirewallService } from "@/services/firewall"
import { ReverseProxyApiService } from "@/services/reverseProxy"
import { handleError } from "@/utils"

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

function normalizeDomainLabel(value: string) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9-]/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 63)
}

function getTemplateInterfacePort(
  template: FastTemplate | null,
): number | null {
  return typeof template?.interface_port === "number"
    ? template.interface_port
    : null
}

function getErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message
  if (
    error &&
    typeof error === "object" &&
    "body" in error &&
    error.body &&
    typeof error.body === "object" &&
    "detail" in error.body &&
    typeof error.body.detail === "string"
  ) {
    return error.body.detail
  }
  return "設定進階網路時發生錯誤。"
}

type QuickStartAccessMode = "private" | "public-website" | "public-port"
type QuickStartFirewallPreset = "safe" | "website" | "internal"

export function ResourceCreatePage({
  quickStartTemplate,
}: {
  quickStartTemplate?: QuickStartTemplateSlug
}) {
  const { t } = useTranslation([
    "resources",
    "validation",
    "common",
    "messages",
    "applications",
  ])
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const showAiAssistant = true
  const [showTemplateSelector, setShowTemplateSelector] = useState(false)
  const [resourceType, setResourceType] = useState<"lxc" | "vm">("lxc")
  const [serviceTemplateName, setServiceTemplateName] = useState("")
  const [serviceTemplateSlug, setServiceTemplateSlug] = useState("")
  const [lastAutoHostname, setLastAutoHostname] = useState("")
  const [showAdvancedSettings, setShowAdvancedSettings] = useState(false)
  const [accessMode, setAccessMode] = useState<QuickStartAccessMode>("private")
  const [enableHttps, setEnableHttps] = useState("on")
  const [firewallPreset, setFirewallPreset] =
    useState<QuickStartFirewallPreset>("safe")
  const [autoDomain, setAutoDomain] = useState("on")
  const [externalPort, setExternalPort] = useState("")
  const activeQuickStartTemplate = useMemo(
    () => getQuickStartTemplate(quickStartTemplate),
    [quickStartTemplate],
  )
  const isQuickStartMode = Boolean(activeQuickStartTemplate)
  const quickStartInterfacePort = useMemo(
    () => getTemplateInterfacePort(activeQuickStartTemplate),
    [activeQuickStartTemplate],
  )

  const formSchema = useMemo(
    () =>
      z.object({
        resource_type: z.enum(["lxc", "vm"]),
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
        password: z
          .string()
          .min(1, { message: t("validation:password.required") })
          .min(6, {
            message: t("validation:password.minLength", { count: 6 }),
          }),
        storage: z.string().default("local-lvm"),
        os_info: z.string().optional(),
        expiry_date: z.string().optional(),
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
      hostname: "",
      ostemplate: "",
      template_id: undefined,
      username: "",
      cores: 2,
      memory: 2048,
      disk_size: 20,
      rootfs_size: 8,
      password: "",
      storage: "local-lvm",
      os_info: "",
      expiry_date: "",
    },
  })

  const watchedOsTemplate = useWatch({
    control: form.control,
    name: "ostemplate",
  })
  const watchedHostname = useWatch({
    control: form.control,
    name: "hostname",
  })

  const isQuickStartTemplateReady =
    !isQuickStartMode || Boolean(watchedOsTemplate)

  const { data: reverseProxySetupContext } = useQuery({
    queryKey: ["reverse-proxy-setup-context"],
    queryFn: () => ReverseProxyApiService.getSetupContext(),
    enabled: isQuickStartMode,
  })

  const canAutoCreateWebsite =
    Boolean(quickStartInterfacePort) &&
    Boolean(reverseProxySetupContext?.enabled) &&
    Boolean(reverseProxySetupContext?.gateway_ready) &&
    Boolean(reverseProxySetupContext?.cloudflare_ready) &&
    Boolean(reverseProxySetupContext?.zones.length)

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

  const applyQuickStartNetworkSettings = useCallback(
    async (vmid: number, hostname: string) => {
      const notices: string[] = []
      const warnings: string[] = []

      if (!isQuickStartMode) return { notices, warnings }
      const requiresServicePort =
        accessMode !== "private" || firewallPreset === "website"
      if (!quickStartInterfacePort && requiresServicePort) {
        warnings.push("此模板沒有預設對外服務 Port，已略過進階網路設定。")
        return { notices, warnings }
      }

      if (accessMode === "public-website") {
        const zone = reverseProxySetupContext?.zones[0]
        if (!canAutoCreateWebsite || !zone) {
          warnings.push("目前無法自動建立公開網站，請稍後於反向代理頁面設定。")
        } else if (autoDomain !== "on") {
          warnings.push("你已關閉自動網域，建立後可再手動設定公開網站。")
        } else {
          const hostnamePrefix = normalizeDomainLabel(hostname) || `app-${vmid}`
          await ReverseProxyApiService.createRule({
            vmid,
            zone_id: zone.id,
            hostname_prefix: hostnamePrefix,
            internal_port: quickStartInterfacePort!,
            enable_https: enableHttps === "on",
          })
          notices.push(`已建立公開網站：${hostnamePrefix}.${zone.name}`)
        }
      }

      if (accessMode === "public-port") {
        const requestedExternalPort =
          Number.parseInt(externalPort, 10) || quickStartInterfacePort
        if (!Number.isInteger(requestedExternalPort)) {
          warnings.push("外部 Port 格式無效，已略過公開 Port 設定。")
        } else {
          await FirewallService.createFirewallConnection({
            requestBody: {
              source_vmid: null,
              target_vmid: vmid,
              ports: [
                {
                  port: quickStartInterfacePort!,
                  protocol: "tcp",
                  external_port: requestedExternalPort,
                },
              ],
              direction: "one_way",
            },
          })
          notices.push(`已開放公開 Port：${requestedExternalPort}`)
        }
      }

      if (firewallPreset === "website" && accessMode === "private") {
        await FirewallService.createFirewallConnection({
          requestBody: {
            source_vmid: null,
            target_vmid: vmid,
            ports: [
              {
                port: quickStartInterfacePort!,
                protocol: "tcp",
              },
            ],
            direction: "one_way",
          },
        })
        notices.push(
          `已套用網站防火牆預設，開放內部服務 Port ${quickStartInterfacePort}`,
        )
      }

      return { notices, warnings }
    },
    [
      accessMode,
      autoDomain,
      canAutoCreateWebsite,
      enableHttps,
      externalPort,
      firewallPreset,
      isQuickStartMode,
      quickStartInterfacePort,
      reverseProxySetupContext?.zones,
    ],
  )

  const mutation = useMutation({
    mutationFn: (data: FormData) => {
      const payloadOptions = {
        lxcEnvironmentType:
          serviceTemplateName || t("resources:create.customSpec"),
        vmEnvironmentType: t("resources:create.customSpec"),
        validationMessages: {
          lxcRequirements: t("validation:requirement.lxc"),
          vmRequirements: t("validation:requirement.vm"),
        },
      }

      if (data.resource_type === "lxc") {
        return LxcService.createLxc({
          requestBody: toLxcCreateRequestBody(
            {
              ...data,
              service_template_slug: serviceTemplateSlug || undefined,
            },
            payloadOptions,
          ),
        })
      }

      return VmService.createVm({
        requestBody: toVmCreateRequestBody(
          { ...data, service_template_slug: serviceTemplateSlug || undefined },
          payloadOptions,
        ),
      })
    },
    onSuccess: async (data) => {
      const successMessages = [
        data.message || t("messages:success.resourceCreated"),
      ]

      if (isQuickStartMode) {
        try {
          const networkResult = await applyQuickStartNetworkSettings(
            data.vmid,
            form.getValues("hostname"),
          )
          successMessages.push(...networkResult.notices)
          if (networkResult.warnings.length > 0) {
            showErrorToast(networkResult.warnings.join(" "))
          }
        } catch (error) {
          showErrorToast(getErrorMessage(error))
        }
      }

      showSuccessToast(successMessages.join(" "))
      queryClient.invalidateQueries({ queryKey: queryKeys.resources.all })
      navigate({ to: "/resources" })
    },
    onError: handleError.bind(showErrorToast),
  })

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

  const handleSelectTemplate = useCallback(
    (template: FastTemplate) => {
      setServiceTemplateName(template.name || "")
      setServiceTemplateSlug(template.slug || "")
      setResourceType("lxc")
      updateFormValue("resource_type", "lxc")

      const method = template.install_methods?.[0]
      // 帶入模板的預設資源值，但保留使用者已輸入的容器名稱
      if (method?.resources) {
        if (method.resources.cpu) updateFormValue("cores", method.resources.cpu)
        if (method.resources.ram)
          updateFormValue("memory", method.resources.ram)
        if (method.resources.hdd)
          updateFormValue("rootfs_size", Math.max(method.resources.hdd, 8))
      }

      // 自動挑選一個符合模板要求 OS / version 的 ostemplate volid
      const volids = (lxcTemplates ?? []).map((t) => t.volid)
      const picked =
        pickMatchingOsTemplate(volids, method?.resources) || volids[0]
      if (picked) {
        updateFormValue("ostemplate", picked)
      }
      if (method?.resources?.os) {
        const osLabel = method.resources.version
          ? `${method.resources.os} ${method.resources.version}`
          : String(method.resources.os)
        updateFormValue("os_info", osLabel)
      }

      setShowTemplateSelector(false)
    },
    [lxcTemplates, updateFormValue],
  )

  const handleImportPlan = useCallback(
    (prefill: FormPrefill | undefined) => {
      if (!prefill) return

      const nextType = prefill.resource_type === "vm" ? "vm" : "lxc"
      setResourceType(nextType)
      updateFormValue("resource_type", nextType)

      if (prefill.hostname) {
        updateFormValue("hostname", normalizeHostname(prefill.hostname))
      }
      if (prefill.cores) updateFormValue("cores", prefill.cores)
      if (prefill.memory_mb) updateFormValue("memory", prefill.memory_mb)

      if (nextType === "lxc") {
        if (prefill.disk_gb) {
          updateFormValue("rootfs_size", Math.max(prefill.disk_gb, 8))
        }
        if (prefill.lxc_os_image) {
          updateFormValue("ostemplate", prefill.lxc_os_image)
        }

        const importedTemplateSlug =
          prefill.service_template_slug || prefill.lxc_template_slug
        if (importedTemplateSlug) {
          setServiceTemplateSlug(importedTemplateSlug)
          setServiceTemplateName(importedTemplateSlug)
        }
      } else {
        setServiceTemplateName("")
        setServiceTemplateSlug("")

        if (prefill.disk_gb) {
          updateFormValue("disk_size", Math.max(prefill.disk_gb, 20))
        }
        if (prefill.vm_template_id) {
          updateFormValue("template_id", prefill.vm_template_id)
        }
        if (prefill.username) {
          updateFormValue("username", prefill.username)
        }
      }

      showSuccessToast(t("applications:aiChat.importSuccess"))
    },
    [showSuccessToast, t, updateFormValue],
  )

  useEffect(() => {
    if (!activeQuickStartTemplate) return
    handleSelectTemplate(activeQuickStartTemplate)
  }, [activeQuickStartTemplate, handleSelectTemplate])

  useEffect(() => {
    if (!activeQuickStartTemplate?.slug) return

    const currentHostname = form.getValues("hostname")
    if (currentHostname.trim() && currentHostname !== lastAutoHostname) return

    const generatedHostname = generateQuickStartHostname(
      activeQuickStartTemplate.slug as QuickStartTemplateSlug,
    )
    updateFormValue("hostname", generatedHostname)
    setLastAutoHostname(generatedHostname)
  }, [activeQuickStartTemplate, form, lastAutoHostname, updateFormValue])

  useEffect(() => {
    if (!quickStartInterfacePort) {
      setExternalPort("")
      return
    }
    setExternalPort((currentValue) =>
      currentValue.trim() ? currentValue : String(quickStartInterfacePort),
    )
  }, [quickStartInterfacePort])

  useEffect(() => {
    if (accessMode === "public-website" && !canAutoCreateWebsite) {
      setAccessMode("private")
    }
  }, [accessMode, canAutoCreateWebsite])

  const onSubmit = (data: FormData) => {
    mutation.mutate(data)
  }

  return (
    <div
      className={`mx-auto flex w-full ${showAiAssistant ? "max-w-[1180px]" : "max-w-[760px]"} flex-col gap-6`}
    >
      <div
        className={`grid items-start gap-6 ${showAiAssistant ? "lg:grid-cols-[minmax(0,1fr)_400px] xl:grid-cols-[minmax(0,1fr)_420px]" : ""}`}
      >
        <div className="min-w-0 max-w-[760px] space-y-6">
          <div className="flex items-center gap-3">
            <Button
              asChild
              variant="ghost"
              size="icon"
              className="shrink-0 text-muted-foreground hover:text-foreground"
            >
              <Link to="/resources" aria-label={t("common:buttons.back")}>
                <ArrowLeft className="h-4 w-4" />
              </Link>
            </Button>
            <div className="min-w-0">
              <h1 className="text-xl font-semibold tracking-tight">
                {isQuickStartMode ? "快速入門" : t("resources:create.heading")}
              </h1>
              {!isQuickStartMode && (
                <p className="text-sm text-muted-foreground">
                  {t("resources:create.description")}
                </p>
              )}
            </div>
          </div>

          {isQuickStartMode ? (
            <article className="overflow-hidden rounded-2xl border border-border/60 bg-card shadow-sm">
              {/* Accent strip */}
              <div aria-hidden="true" className="h-[3px] bg-gradient-to-r from-primary to-primary/50" />

              <div className="p-5">
                {/* Header row */}
                <div className="flex min-w-0 items-start gap-4">
                  <div className="flex h-14 w-14 shrink-0 items-center justify-center overflow-hidden rounded-xl border border-border/60 bg-background">
                    {activeQuickStartTemplate?.logo ? (
                      <img
                        src={activeQuickStartTemplate.logo}
                        alt={activeQuickStartTemplate.name ?? ""}
                        width={36}
                        height={36}
                        className="h-9 w-9 object-contain"
                        loading="lazy"
                      />
                    ) : (
                      <LayoutTemplate className="h-6 w-6 text-primary" aria-hidden="true" />
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-base font-semibold leading-snug">
                      {activeQuickStartTemplate?.name}
                    </p>
                    <p className="mt-1 line-clamp-2 text-sm leading-relaxed text-muted-foreground">
                      {activeQuickStartTemplate?.description_zh ||
                        activeQuickStartTemplate?.description ||
                        "使用模板預設配置一鍵部署。"}
                    </p>
                  </div>
                  <Button asChild variant="ghost" size="sm" className="shrink-0 text-xs text-muted-foreground">
                    <Link to="/resources-create">完整設定</Link>
                  </Button>
                </div>

                {/* Spec badges */}
                <div className="mt-4 flex flex-wrap gap-2">
                  {activeQuickStartTemplate?.cores ? (
                    <span className="inline-flex items-center gap-1.5 rounded-full border border-border/50 bg-muted/40 px-2.5 py-1 text-xs font-medium text-muted-foreground">
                      <Cpu className="h-3 w-3" aria-hidden="true" />
                      {activeQuickStartTemplate.cores} vCPU
                    </span>
                  ) : null}
                  {activeQuickStartTemplate?.memory ? (
                    <span className="inline-flex items-center gap-1.5 rounded-full border border-border/50 bg-muted/40 px-2.5 py-1 text-xs font-medium text-muted-foreground">
                      <MemoryStick className="h-3 w-3" aria-hidden="true" />
                      {activeQuickStartTemplate.memory >= 1024
                        ? `${(activeQuickStartTemplate.memory / 1024).toFixed(activeQuickStartTemplate.memory % 1024 === 0 ? 0 : 1)} GB`
                        : `${activeQuickStartTemplate.memory} MB`} RAM
                    </span>
                  ) : null}
                  {activeQuickStartTemplate?.rootfs_size ? (
                    <span className="inline-flex items-center gap-1.5 rounded-full border border-border/50 bg-muted/40 px-2.5 py-1 text-xs font-medium text-muted-foreground">
                      <HardDrive className="h-3 w-3" aria-hidden="true" />
                      {activeQuickStartTemplate.rootfs_size} GB
                    </span>
                  ) : null}
                  {quickStartInterfacePort ? (
                    <span className="inline-flex items-center gap-1.5 rounded-full border border-border/50 bg-muted/40 px-2.5 py-1 text-xs font-medium text-muted-foreground">
                      <Plug className="h-3 w-3" aria-hidden="true" />
                      Port {quickStartInterfacePort}
                    </span>
                  ) : null}
                </div>

                {!isQuickStartTemplateReady ? (
                  <p className="mt-3 text-xs text-muted-foreground">
                    正在準備基礎映像，完成後即可建立。
                  </p>
                ) : null}
              </div>
            </article>
          ) : null}

          {isQuickStartMode ? (
            <div className="rounded-2xl border border-border/60 bg-card">
              <button
                type="button"
                className="flex min-h-[52px] w-full items-center justify-between gap-3 px-5 py-4 text-left transition-colors duration-150 hover:bg-muted/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-inset rounded-2xl"
                onClick={() => setShowAdvancedSettings((current) => !current)}
                aria-expanded={showAdvancedSettings}
              >
                <div>
                  <p className="text-sm font-medium">進階設定</p>
                  <p className="text-xs text-muted-foreground">
                    公開存取、防火牆、HTTPS、自動網域
                  </p>
                </div>
                {showAdvancedSettings ? (
                  <ChevronUp className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                ) : (
                  <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                )}
              </button>

              {showAdvancedSettings ? (
                <div className="border-t border-border/60 px-5 pb-5 pt-4 space-y-5">

                  {/* Access mode button group */}
                  <fieldset>
                    <legend className="mb-2 text-sm font-medium">公開存取</legend>
                    <div className="grid grid-cols-3 gap-2" role="group" aria-label="公開存取模式">
                      {(
                        [
                          { value: "private", label: "不公開", Icon: Lock, disabled: false },
                          { value: "public-website", label: "公開網站", Icon: Globe, disabled: !canAutoCreateWebsite },
                          { value: "public-port", label: "公開 Port", Icon: Plug, disabled: !quickStartInterfacePort },
                        ] as const
                      ).map(({ value, label, Icon, disabled }) => (
                        <button
                          key={value}
                          type="button"
                          disabled={disabled}
                          onClick={() => !disabled && setAccessMode(value)}
                          aria-pressed={accessMode === value}
                          className={`flex min-h-[52px] flex-col items-center justify-center gap-1.5 rounded-xl border px-3 py-2.5 text-xs font-medium transition-all duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-1 disabled:pointer-events-none disabled:opacity-40 ${
                            accessMode === value
                              ? "border-primary bg-primary/10 text-primary"
                              : "border-border/60 bg-background text-muted-foreground hover:border-primary/40 hover:bg-muted/40 hover:text-foreground"
                          }`}
                        >
                          <Icon className="h-4 w-4" aria-hidden="true" />
                          {label}
                        </button>
                      ))}
                    </div>
                  </fieldset>

                  {/* Firewall preset button group */}
                  <fieldset>
                    <legend className="mb-2 text-sm font-medium">防火牆預設</legend>
                    <div className="grid grid-cols-3 gap-2" role="group" aria-label="防火牆預設模式">
                      {(
                        [
                          { value: "safe", label: "安全", Icon: Shield, disabled: false },
                          { value: "website", label: "網站", Icon: Globe, disabled: !quickStartInterfacePort },
                          { value: "internal", label: "內部", Icon: Network, disabled: false },
                        ] as const
                      ).map(({ value, label, Icon, disabled }) => (
                        <button
                          key={value}
                          type="button"
                          disabled={disabled}
                          onClick={() => !disabled && setFirewallPreset(value)}
                          aria-pressed={firewallPreset === value}
                          className={`flex min-h-[52px] flex-col items-center justify-center gap-1.5 rounded-xl border px-3 py-2.5 text-xs font-medium transition-all duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-1 disabled:pointer-events-none disabled:opacity-40 ${
                            firewallPreset === value
                              ? "border-primary bg-primary/10 text-primary"
                              : "border-border/60 bg-background text-muted-foreground hover:border-primary/40 hover:bg-muted/40 hover:text-foreground"
                          }`}
                        >
                          <Icon className="h-4 w-4" aria-hidden="true" />
                          {label}
                        </button>
                      ))}
                    </div>
                  </fieldset>

                  {/* HTTPS + auto-domain toggles (public-website only) */}
                  {accessMode === "public-website" ? (
                    <div className="grid grid-cols-2 gap-3">
                      <fieldset>
                        <legend className="mb-2 text-sm font-medium">HTTPS</legend>
                        <div className="flex gap-2">
                          {(["on", "off"] as const).map((val) => (
                            <button
                              key={val}
                              type="button"
                              onClick={() => setEnableHttps(val)}
                              aria-pressed={enableHttps === val}
                              className={`flex min-h-[44px] flex-1 items-center justify-center rounded-xl border text-xs font-medium transition-all duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-1 ${
                                enableHttps === val
                                  ? "border-primary bg-primary/10 text-primary"
                                  : "border-border/60 bg-background text-muted-foreground hover:border-primary/40 hover:bg-muted/40 hover:text-foreground"
                              }`}
                            >
                              {val === "on" ? "開啟" : "關閉"}
                            </button>
                          ))}
                        </div>
                      </fieldset>
                      <fieldset>
                        <legend className="mb-2 text-sm font-medium">自動網域</legend>
                        <div className="flex gap-2">
                          {(["on", "off"] as const).map((val) => (
                            <button
                              key={val}
                              type="button"
                              onClick={() => setAutoDomain(val)}
                              aria-pressed={autoDomain === val}
                              className={`flex min-h-[44px] flex-1 items-center justify-center rounded-xl border text-xs font-medium transition-all duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-1 ${
                                autoDomain === val
                                  ? "border-primary bg-primary/10 text-primary"
                                  : "border-border/60 bg-background text-muted-foreground hover:border-primary/40 hover:bg-muted/40 hover:text-foreground"
                              }`}
                            >
                              {val === "on" ? "開啟" : "關閉"}
                            </button>
                          ))}
                        </div>
                      </fieldset>
                    </div>
                  ) : null}

                  {/* External port input */}
                  {accessMode === "public-port" ? (
                    <div className="space-y-2">
                      <label htmlFor="external-port" className="text-sm font-medium">外部 Port</label>
                      <Input
                        id="external-port"
                        aria-label="外部 Port"
                        type="number"
                        min={1}
                        max={65535}
                        value={externalPort}
                        onChange={(event) => setExternalPort(event.target.value)}
                        placeholder={
                          quickStartInterfacePort
                            ? String(quickStartInterfacePort)
                            : "8080"
                        }
                      />
                    </div>
                  ) : null}

                  {/* Info hint */}
                  <div className="rounded-xl bg-muted/40 px-3 py-2.5 text-xs text-muted-foreground space-y-1">
                    {quickStartInterfacePort ? (
                      <p>模板預設服務 Port：{quickStartInterfacePort}</p>
                    ) : (
                      <p>此模板沒有預設服務 Port，公開網站與公開 Port 會停用。</p>
                    )}
                    {accessMode === "public-website" && canAutoCreateWebsite ? (
                      <p>
                        會使用「{normalizeDomainLabel(watchedHostname) || "app"}」作為子網域前綴。
                      </p>
                    ) : null}
                    {accessMode === "public-website" && !canAutoCreateWebsite ? (
                      <p>目前反向代理或 DNS 尚未完成設定，暫時不能自動建立公開網站。</p>
                    ) : null}
                    {firewallPreset === "internal" ? (
                      <p>內部模式不會自動建立對外公開規則。</p>
                    ) : null}
                  </div>
                </div>
              ) : null}
            </div>
          ) : null}

          {showTemplateSelector && !isQuickStartMode ? (
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
                <Tabs
                  value={resourceType}
                  onValueChange={(value) => {
                    if (isQuickStartMode) return
                    const nextType = value as "lxc" | "vm"
                    setResourceType(nextType)
                    updateFormValue("resource_type", nextType)
                  }}
                  className="w-full"
                >
                  {isQuickStartMode ? null : (
                    <TabsList className="grid w-full grid-cols-2">
                      <TabsTrigger value="lxc">
                        {t("resources:form.type.lxc")}
                      </TabsTrigger>
                      <TabsTrigger value="vm">
                        {t("resources:form.type.qemu")}
                      </TabsTrigger>
                    </TabsList>
                  )}

                  <TabsContent value="lxc" className={isQuickStartMode ? "space-y-6" : "mt-6 space-y-6"}>
                    <div className={isQuickStartMode ? "rounded-xl border border-border/60 bg-card p-5 space-y-5" : "space-y-5"}>
                      {isQuickStartMode && (
                        <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                          基本設定
                        </p>
                      )}
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

                      {isQuickStartMode ? null : (
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
                      )}

                      {isQuickStartMode ? null : (
                        <FormField
                          control={form.control}
                          name="os_info"
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>
                                {t("resources:form.osInfo")}
                              </FormLabel>
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
                      )}

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

                      {isQuickStartMode ? null : (
                        <FormField
                          control={form.control}
                          name="expiry_date"
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel>
                                {t("resources:form.expiryDate")}
                              </FormLabel>
                              <FormControl>
                                <Input type="date" {...field} />
                              </FormControl>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                      )}
                    </div>

                    {isQuickStartMode ? null : (
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
                    )}
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

                      <FormField
                        control={form.control}
                        name="expiry_date"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>
                              {t("resources:form.expiryDate")}
                            </FormLabel>
                            <FormControl>
                              <Input type="date" {...field} />
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
                  </TabsContent>
                </Tabs>

                <div className="flex flex-col gap-3 pt-2 sm:flex-row sm:items-center sm:justify-between">
                  {showAiAssistant && !isQuickStartMode ? (
                    <p className="text-xs text-muted-foreground">
                      可在右側使用 AI 模板推薦，匯入後再確認建立參數。
                    </p>
                  ) : (
                    <div />
                  )}
                  <div className="flex flex-col-reverse gap-2 sm:flex-row">
                    <Button
                      type="button"
                      variant="ghost"
                      className="text-muted-foreground"
                      onClick={() => navigate({ to: "/resources" })}
                      disabled={mutation.isPending}
                    >
                      {t("common:buttons.cancel")}
                    </Button>
                    <LoadingButton
                      type="submit"
                      loading={mutation.isPending}
                      disabled={!isQuickStartTemplateReady}
                      className={isQuickStartMode ? "min-w-[120px]" : ""}
                    >
                      {t("resources:create.submitButton")}
                    </LoadingButton>
                  </div>
                </div>
              </form>
            </Form>
          )}
        </div>

        {showAiAssistant && (
          <aside className="min-w-0 lg:sticky lg:top-24">
            <div className="glass-panel rounded-2xl p-3 lg:h-[calc(100vh-8rem)] lg:min-h-[32rem]">
              <AiChatPanel
                onImportPlan={handleImportPlan}
                recommendationContext={{
                  resource_type: resourceType,
                  mode: "immediate",
                }}
              />
            </div>
          </aside>
        )}
      </div>
    </div>
  )
}
