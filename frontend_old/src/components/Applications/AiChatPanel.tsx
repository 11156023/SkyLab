import {
  Bot,
  Cpu,
  Download,
  FileText,
  Globe,
  HardDrive,
  Layers3,
  Loader2,
  MemoryStick,
  MessageSquare,
  Send,
  Sparkles,
  Trash2,
} from "lucide-react"
import {
  Fragment,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type WheelEvent,
} from "react"
import { useTranslation } from "react-i18next"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import {
  type AiMetrics,
  type AiPlanResult,
  AiTemplateRecommendationApi,
  type AiChatMessage as ChatMessage,
  type FormPrefill,
  type RecommendationFormContext,
} from "@/services/aiTemplateRecommendation"

export type { AiPlanResult } from "@/services/aiTemplateRecommendation"

interface AiChatPanelProps {
  autoImportPlan?: boolean
  onImportPlan?: (prefill: FormPrefill) => void
  onImportReason?: (reason: string) => void
  quickStartPrompt?: string
  recommendationContext?: RecommendationFormContext
}

type ServiceTemplateCard = {
  isSelected: boolean
  name: string
  slug: string
  why: string
}

function stripThinkTags(text: string): string {
  const stripped = text.replace(/<think>[\s\S]*?<\/think>/gi, "").trim()
  return stripped || text.trim()
}

function renderMarkdown(source: string): string {
  let html = source
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")

  html = html.replace(
    /```([\s\S]*?)```/g,
    (_, code: string) =>
      `<pre class="my-2 p-3 rounded-lg bg-muted/80 overflow-auto text-xs"><code>${code.replace(/^\n+|\n+$/g, "")}</code></pre>`,
  )
  html = html.replace(
    /`([^`\n]+)`/g,
    '<code class="px-1 py-0.5 rounded bg-muted text-xs">$1</code>',
  )
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")

  html = html.replace(
    /^### (.+)$/gm,
    '<h4 class="font-semibold text-sm mt-3 mb-1">$1</h4>',
  )
  html = html.replace(
    /^## (.+)$/gm,
    '<h3 class="font-semibold text-base mt-3 mb-1">$1</h3>',
  )
  html = html.replace(
    /^# (.+)$/gm,
    '<h2 class="font-bold text-lg mt-3 mb-1">$1</h2>',
  )

  html = html.replace(
    /^[-*] (.+)$/gm,
    '<li class="ml-4 list-disc text-sm">$1</li>',
  )
  html = html.replace(
    /^\d+\. (.+)$/gm,
    '<li class="ml-4 list-decimal text-sm">$1</li>',
  )

  html = html.replace(/^---+$/gm, '<hr class="my-2 border-border/50" />')

  html = html.replace(/\n{2,}/g, "</p><p>")
  html = html.replace(/\n/g, "<br />")

  return `<p>${html}</p>`
}

function renderPlanMarkdown(source: string): string {
  return renderMarkdown(source || "").replace(
    /<p>/g,
    '<p class="text-sm leading-6">',
  )
}

function formatDateTimeLabel(value?: string) {
  if (!value) return "-"
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat("zh-TW", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Taipei",
  }).format(date)
}

export function AiChatPanel({
  autoImportPlan = false,
  onImportPlan,
  quickStartPrompt,
  recommendationContext,
}: AiChatPanelProps) {
  const { t } = useTranslation("applications")
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [conversationHistory, setConversationHistory] = useState<ChatMessage[]>(
    [],
  )
  const [planContextMessage, setPlanContextMessage] =
    useState<ChatMessage | null>(null)
  const [input, setInput] = useState("")
  const [isLoading, setIsLoading] = useState(false)
  const [latestPlan, setLatestPlan] = useState<AiPlanResult | null>(null)
  const [planInsertionIndex, setPlanInsertionIndex] = useState<number | null>(
    null,
  )
  const [metrics, setMetrics] = useState<AiMetrics | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const latestMessageRef = useRef<HTMLDivElement>(null)
  const loadingRef = useRef<HTMLDivElement>(null)
  const planCardRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const quickStartRunRef = useRef<string | null>(null)

  const scrollToElement = useCallback((element: HTMLDivElement | null) => {
    requestAnimationFrame(() => {
      element?.scrollIntoView({ block: "end" })
      if (scrollRef.current) {
        scrollRef.current.scrollTop = scrollRef.current.scrollHeight
      }
    })
  }, [])

  useEffect(() => {
    void messages
    void isLoading
    if (isLoading) {
      scrollToElement(loadingRef.current)
      return
    }

    if (messages.length > 0) {
      scrollToElement(latestMessageRef.current)
      return
    }

    scrollToElement(messagesEndRef.current)
  }, [messages, isLoading, scrollToElement])

  useEffect(() => {
    if (!latestPlan) return
    scrollToElement(planCardRef.current)
  }, [latestPlan, scrollToElement])

  useEffect(() => {
    void input
    if (!inputRef.current) return
    inputRef.current.style.height = "0px"
    inputRef.current.style.height = `${Math.min(inputRef.current.scrollHeight, 120)}px`
  }, [input])

  const buildRequestMessages = useCallback(
    (history: ChatMessage[]) =>
      planContextMessage ? [...history, planContextMessage] : history,
    [planContextMessage],
  )
  const formContextForChat = useMemo(
    () =>
      recommendationContext
        ? {
            resource_type: recommendationContext.resource_type,
            mode: recommendationContext.mode,
            start_at: recommendationContext.start_at,
            end_at: recommendationContext.end_at,
            selected_gpu_mapping_id:
              recommendationContext.selected_gpu_mapping_id,
          }
        : undefined,
    [recommendationContext],
  )

  useEffect(() => {
    if (!quickStartPrompt) return
    if (quickStartRunRef.current === quickStartPrompt) return
    quickStartRunRef.current = quickStartPrompt

    const userMsg: ChatMessage = { role: "user", content: quickStartPrompt }
    setMessages([userMsg])
    setConversationHistory([userMsg])
    setPlanContextMessage(null)
    setLatestPlan(null)
    setPlanInsertionIndex(null)
    setMetrics(null)
    setInput("")
    setIsLoading(true)

    let cancelled = false

    const runQuickStartPlan = async () => {
      try {
        const data = await AiTemplateRecommendationApi.recommend({
          requestBody: {
            messages: [userMsg],
            top_k: 5,
            device_nodes: [],
            form_context: recommendationContext,
          },
        })

        if (cancelled) return

        const summaryText =
          [
            data.summary?.trim(),
            data.final_plan?.application_target?.environment_reason?.trim(),
          ]
            .filter(Boolean)
            .join("\n\n") || "已根據快速入門情境產生推薦方案。"

        const aiMsg: ChatMessage = {
          role: "assistant",
          content: `已收到快速入門情境，先幫你整理需求：\n\n${summaryText}`,
        }

        setMessages([userMsg, aiMsg])
        setConversationHistory([userMsg, aiMsg])
        setPlanInsertionIndex(2)
        setLatestPlan(data)
        if (data.ai_metrics) setMetrics(data.ai_metrics)

        const planContext = [
          data.summary?.trim(),
          data.final_plan?.application_target?.environment_reason?.trim(),
          data.final_plan?.form_prefill?.reason?.trim(),
          data.final_plan?.gpu_recommendation?.reason?.trim(),
        ]
          .filter(Boolean)
          .join("\n\n")

        if (planContext) {
          setPlanContextMessage({
            role: "assistant",
            content: `AI 已產生推薦配置，以下是目前方案摘要：\n${planContext}`,
          })
        }

        if (autoImportPlan && data.final_plan?.form_prefill && onImportPlan) {
          onImportPlan(data.final_plan.form_prefill)
        }
      } catch (err) {
        if (cancelled) return
        setMessages([
          userMsg,
          {
            role: "assistant",
            content: `[${t("aiChat.planError")}] ${err instanceof Error ? err.message : String(err)}`,
          },
        ])
      } finally {
        if (!cancelled) {
          setIsLoading(false)
        }
      }
    }

    void runQuickStartPlan()

    return () => {
      cancelled = true
    }
  }, [autoImportPlan, onImportPlan, quickStartPrompt, recommendationContext, t])

  const sendChat = async () => {
    const text = input.trim()
    if (!text || isLoading) return

    setInput("")
    const userMsg: ChatMessage = { role: "user", content: text }
    setMessages((prev) => [...prev, userMsg])

    const newHistory = [...conversationHistory, userMsg]
    setConversationHistory(newHistory)
    setIsLoading(true)

    try {
      const requestMessages = buildRequestMessages(newHistory)
      const data = await AiTemplateRecommendationApi.chat({
        requestBody: {
          messages: requestMessages,
          top_k: 5,
          device_nodes: [],
          form_context: formContextForChat,
        },
      })
      const reply = stripThinkTags(data.reply || "")

      const aiMsg: ChatMessage = { role: "assistant", content: reply }
      setMessages((prev) => [...prev, aiMsg])
      setConversationHistory((prev) => [...prev, aiMsg])
      setMetrics({
        total_tokens: data.total_tokens,
        elapsed_seconds: data.elapsed_seconds,
        tokens_per_second: data.tokens_per_second,
      })
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: `[${t("aiChat.chatError")}] ${err instanceof Error ? err.message : String(err)}`,
        },
      ])
    } finally {
      setIsLoading(false)
      inputRef.current?.focus()
    }
  }

  const generatePlan = async () => {
    if (conversationHistory.length === 0) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: t("aiChat.needChatFirst"),
        },
      ])
      return
    }

    setIsLoading(true)
    try {
      const requestMessages = buildRequestMessages(conversationHistory)
      const data = await AiTemplateRecommendationApi.recommend({
        requestBody: {
          messages: requestMessages,
          top_k: 5,
          device_nodes: [],
          form_context: recommendationContext,
        },
      })
      setPlanInsertionIndex(messages.length)
      setLatestPlan(data)
      if (data.ai_metrics) setMetrics(data.ai_metrics)

      const planContext = [
        data.summary?.trim(),
        data.final_plan?.application_target?.environment_reason?.trim(),
        data.final_plan?.form_prefill?.reason?.trim(),
        data.final_plan?.gpu_recommendation?.reason?.trim(),
      ]
        .filter(Boolean)
        .join("\n\n")

      if (planContext) {
        setPlanContextMessage({
          role: "assistant",
          content: `AI 已產生推薦配置，以下是目前方案摘要：\n${planContext}`,
        })
      }
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: `[${t("aiChat.planError")}] ${err instanceof Error ? err.message : String(err)}`,
        },
      ])
    } finally {
      setIsLoading(false)
    }
  }

  const handleImportPlan = () => {
    if (!latestPlan?.final_plan?.form_prefill || !onImportPlan) return
    onImportPlan(latestPlan.final_plan.form_prefill)
  }

  const clearChat = () => {
    setMessages([])
    setConversationHistory([])
    setPlanContextMessage(null)
    setLatestPlan(null)
    setPlanInsertionIndex(null)
    setMetrics(null)
    inputRef.current?.focus()
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      sendChat()
    }
  }

  const handleMessagesWheel = useCallback(
    (event: WheelEvent<HTMLDivElement>) => {
      const container = scrollRef.current
      if (!container) return

      const maxScrollTop = container.scrollHeight - container.clientHeight
      if (maxScrollTop <= 0) return

      const scrollingDown = event.deltaY > 0
      const scrollingUp = event.deltaY < 0
      const canScrollDown = container.scrollTop < maxScrollTop
      const canScrollUp = container.scrollTop > 0

      if ((scrollingDown && canScrollDown) || (scrollingUp && canScrollUp)) {
        event.preventDefault()
        container.scrollTop += event.deltaY * 0.45
      }
    },
    [],
  )

  const plan = latestPlan?.final_plan
  const formPrefill = plan?.form_prefill
  const gpuRecommendation = plan?.gpu_recommendation
  const resourceType =
    String(formPrefill?.resource_type || "lxc").toLowerCase() === "vm"
      ? "vm"
      : "lxc"
  const prefillEnvironmentLabel =
    resourceType === "vm"
      ? formPrefill?.vm_os_choice || formPrefill?.vm_template_id || "-"
      : formPrefill?.lxc_os_image || "-"
  const scheduleSummary =
    formPrefill?.start_at && formPrefill?.end_at
      ? `${formatDateTimeLabel(formPrefill.start_at)} → ${formatDateTimeLabel(formPrefill.end_at)}`
      : recommendationContext?.start_at && recommendationContext?.end_at
        ? `${formatDateTimeLabel(recommendationContext.start_at)} → ${formatDateTimeLabel(recommendationContext.end_at)}`
        : "-"
  const applicationReason =
    formPrefill?.reason || latestPlan?.summary || t("aiChat.planError")
  const serviceTemplates = useMemo<ServiceTemplateCard[]>(() => {
    if (resourceType !== "lxc") return []

    const selectedSlug =
      formPrefill?.service_template_slug || formPrefill?.lxc_template_slug || ""
    const items = new Map<string, ServiceTemplateCard>()

    if (selectedSlug) {
      items.set(selectedSlug, {
        isSelected: true,
        name: selectedSlug,
        slug: selectedSlug,
        why: "\u9019\u500b\u670d\u52d9\u6a21\u677f\u5df2\u5e36\u5165\u8868\u55ae\u914d\u7f6e\u3002",
      })
    }

    for (const template of plan?.recommended_templates || []) {
      const slug = String(template.slug || "").trim()
      if (!slug) continue

      const existing = items.get(slug)
      items.set(slug, {
        isSelected: existing?.isSelected || slug === selectedSlug,
        name: template.name || existing?.name || slug,
        slug,
        why: template.why || existing?.why || "",
      })
    }

    return Array.from(items.values())
  }, [
    formPrefill?.lxc_template_slug,
    formPrefill?.service_template_slug,
    plan?.recommended_templates,
    resourceType,
  ])
  const inputLength = input.length

  return (
    <div className="flex h-full min-h-0 flex-col gap-4 pb-3">
      <div className="flex shrink-0 items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary/10">
            <Bot className="h-4 w-4 text-primary" />
          </div>
          <h3 className="text-sm font-semibold">{t("aiChat.title")}</h3>
        </div>
        {metrics && (
          <Badge variant="outline" className="text-[10px] font-mono">
            {t("aiChat.tokens", {
              count: metrics.total_tokens || 0,
            })}
            {metrics.tokens_per_second
              ? ` | ${metrics.tokens_per_second.toFixed(1)} tok/s`
              : ""}
          </Badge>
        )}
      </div>

      <div
        ref={scrollRef}
        onWheel={handleMessagesWheel}
        className="hidden-scroll min-h-0 flex-1 overflow-y-auto rounded-xl border border-border/60 bg-background/40 p-2"
      >
        <div className="flex min-h-full flex-col justify-end gap-2.5 p-2.5">
          {messages.length === 0 && (
            <div className="animate-in fade-in flex gap-2.5 duration-300">
              <div className="flex max-w-[90%] items-start gap-2.5">
                <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10">
                  <Sparkles className="h-3 w-3 text-primary" />
                </div>
                <div className="rounded-xl rounded-tl-sm border border-border/60 bg-card/80 px-3 py-2 text-[13px] leading-5">
                  {t("aiChat.systemGreeting")}
                </div>
              </div>
            </div>
          )}

          {messages.map((msg, i) => {
            const isUser = msg.role === "user"
            const isLastMessage = i === messages.length - 1
            return (
              <Fragment key={`msg-${i}-${msg.role}`}>
                <div
                  ref={isLastMessage ? latestMessageRef : null}
                  className={`animate-in fade-in slide-in-from-bottom-2 flex gap-2.5 duration-200 ${
                    isUser ? "justify-end" : "justify-start"
                  }`}
                >
                  {!isUser && (
                    <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10">
                      <Bot className="h-3 w-3 text-primary" />
                    </div>
                  )}
                  <div
                    className={`max-w-[85%] rounded-xl px-3 py-2 text-[13px] leading-5 ${
                      isUser
                        ? "rounded-tr-sm bg-primary text-primary-foreground"
                        : "rounded-tl-sm border border-border/60 bg-card/80"
                    }`}
                  >
                    {isUser ? (
                      <span className="whitespace-pre-wrap text-[13px] leading-5">
                        {msg.content}
                      </span>
                    ) : (
                      <div
                        className="prose prose-sm max-w-none text-[13px] dark:prose-invert [&_li]:my-0.5 [&_p]:mb-1.5 [&_p:last-child]:mb-0 [&_p]:leading-5"
                        // biome-ignore lint/security/noDangerouslySetInnerHtml: controlled markdown
                        dangerouslySetInnerHTML={{
                          __html: renderMarkdown(msg.content),
                        }}
                      />
                    )}
                  </div>
                  {isUser && (
                    <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary">
                      <MessageSquare className="h-3 w-3 text-primary-foreground" />
                    </div>
                  )}
                </div>

                {latestPlan && plan && planInsertionIndex === i + 1 && (
                  <div
                    ref={planCardRef}
                    className="animate-in fade-in slide-in-from-bottom-2 flex gap-2.5 duration-300"
                  >
                    <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10">
                      <Bot className="h-3 w-3 text-primary" />
                    </div>
                    <div className="min-w-0 max-w-[90%] overflow-hidden rounded-xl rounded-tl-sm border border-border/60 bg-card/90">
                      <div className="border-b bg-primary/5 px-4 py-3">
                        <div className="flex items-center justify-between gap-3">
                          <div className="flex min-w-0 items-center gap-2">
                            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-primary/10">
                              <Layers3 className="h-4 w-4 text-primary" />
                            </div>
                            <div className="min-w-0">
                              <div className="text-sm font-semibold">
                                {t("aiChat.planTitle")}
                              </div>
                              <div className="truncate text-xs text-muted-foreground">
                                {plan.application_target?.service_name ||
                                  "AI \u63a8\u85a6\u65b9\u6848"}
                              </div>
                            </div>
                          </div>
                          <Badge
                            variant="secondary"
                            className="shrink-0 uppercase"
                          >
                            {resourceType}
                          </Badge>
                        </div>
                      </div>

                      <div className="space-y-4 p-4">
                        <div className="space-y-2">
                          <div className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                            {"\u670d\u52d9\u5b9a\u4f4d"}
                          </div>
                          <div className="rounded-xl border border-border/60 bg-muted/10 px-3.5 py-3">
                            <div className="mb-2 flex flex-wrap items-center gap-2">
                              <Badge variant="outline">
                                {plan.application_target
                                  ?.execution_environment ||
                                  resourceType.toUpperCase()}
                              </Badge>
                              {plan.application_target?.service_name && (
                                <span className="text-sm font-semibold">
                                  {plan.application_target.service_name}
                                </span>
                              )}
                            </div>
                            <div
                              className="text-sm text-muted-foreground [&_p]:mb-1.5 [&_p:last-child]:mb-0"
                              // biome-ignore lint/security/noDangerouslySetInnerHtml: controlled markdown
                              dangerouslySetInnerHTML={{
                                __html: renderPlanMarkdown(
                                  plan.application_target?.environment_reason ||
                                    "This is the environment recommended by AI.",
                                ),
                              }}
                            />
                          </div>
                        </div>

                        <div className="space-y-2">
                          <div className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                            {"\u8868\u55ae\u9810\u586b\u6b04\u4f4d"}
                          </div>
                          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                            <div className="rounded-xl border border-border/60 bg-muted/10 p-3">
                              <div className="mb-1 flex items-center gap-2 text-xs text-muted-foreground">
                                <Layers3 className="h-3.5 w-3.5" />
                                {"\u8cc7\u6e90\u985e\u578b"}
                              </div>
                              <div className="text-sm font-semibold uppercase">
                                {formPrefill?.resource_type || "-"}
                              </div>
                            </div>

                            <div className="rounded-xl border border-border/60 bg-muted/10 p-3">
                              <div className="mb-1 flex items-center gap-2 text-xs text-muted-foreground">
                                <Globe className="h-3.5 w-3.5" />
                                Hostname
                              </div>
                              <div className="break-all text-sm font-semibold">
                                {formPrefill?.hostname || "-"}
                              </div>
                            </div>

                            <div className="rounded-xl border border-border/60 bg-muted/10 p-3">
                              <div className="mb-1 flex items-center gap-2 text-xs text-muted-foreground">
                                <FileText className="h-3.5 w-3.5" />
                                {resourceType === "vm"
                                  ? "VM \u74b0\u5883"
                                  : "LXC OS \u6620\u50cf"}
                              </div>
                              <div className="break-all text-sm font-semibold">
                                {String(prefillEnvironmentLabel)}
                              </div>
                            </div>

                            <div className="rounded-xl border border-border/60 bg-muted/10 p-3">
                              <div className="mb-1 flex items-center gap-2 text-xs text-muted-foreground">
                                <Cpu className="h-3.5 w-3.5" />
                                CPU / RAM / Disk
                              </div>
                              <div className="space-y-1 text-sm font-semibold">
                                <div className="flex items-center gap-2">
                                  <Cpu className="h-3.5 w-3.5 text-primary" />
                                  {formPrefill?.cores || "-"} cores
                                </div>
                                <div className="flex items-center gap-2">
                                  <MemoryStick className="h-3.5 w-3.5 text-primary" />
                                  {formPrefill?.memory_mb || "-"} MB
                                </div>
                                <div className="flex items-center gap-2">
                                  <HardDrive className="h-3.5 w-3.5 text-primary" />
                                  {formPrefill?.disk_gb || "-"} GB
                                </div>
                              </div>
                            </div>

                            <div className="rounded-xl border border-border/60 bg-muted/10 p-3">
                              <div className="mb-1 flex items-center gap-2 text-xs text-muted-foreground">
                                <Sparkles className="h-3.5 w-3.5" />
                                時段 / GPU
                              </div>
                              <div className="space-y-1 text-sm font-semibold">
                                <div className="break-all">
                                  {scheduleSummary}
                                </div>
                                <div className="break-all text-xs text-muted-foreground">
                                  {gpuRecommendation?.selected_gpu_label ||
                                    gpuRecommendation?.selected_gpu_mapping_id ||
                                    formPrefill?.gpu_mapping_id ||
                                    "尚未建議 GPU"}
                                </div>
                              </div>
                            </div>
                          </div>
                        </div>

                        {gpuRecommendation && (
                          <div className="space-y-2">
                            <div className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                              GPU 建議
                            </div>
                            <div className="rounded-xl border border-border/60 bg-muted/10 px-3.5 py-3 text-sm text-muted-foreground">
                              <div className="mb-1 font-semibold text-foreground">
                                {gpuRecommendation.should_use_gpu
                                  ? gpuRecommendation.selected_gpu_label ||
                                    gpuRecommendation.selected_gpu_mapping_id ||
                                    "建議使用 GPU"
                                  : "目前不建議使用 GPU"}
                              </div>
                              {gpuRecommendation.reason && (
                                <div className="mb-2 [&_p]:mb-1.5 [&_p:last-child]:mb-0">
                                  <div
                                    className="text-sm text-muted-foreground"
                                    // biome-ignore lint/security/noDangerouslySetInnerHtml: controlled markdown
                                    dangerouslySetInnerHTML={{
                                      __html: renderPlanMarkdown(
                                        gpuRecommendation.reason,
                                      ),
                                    }}
                                  />
                                </div>
                              )}
                              {gpuRecommendation.candidates?.length ? (
                                <div className="flex flex-wrap gap-2">
                                  {gpuRecommendation.candidates.map((item) => (
                                    <Badge
                                      key={item.mapping_id}
                                      variant="outline"
                                    >
                                      {item.label}
                                    </Badge>
                                  ))}
                                </div>
                              ) : null}
                            </div>
                          </div>
                        )}

                        {serviceTemplates.length > 0 && (
                          <div className="space-y-2">
                            <div className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                              {"\u670d\u52d9\u6a21\u677f"}
                            </div>
                            <div className="space-y-3">
                              {serviceTemplates.slice(0, 3).map((template) => (
                                <div
                                  key={template.slug}
                                  className="rounded-xl border border-border/60 bg-muted/10 px-3.5 py-3"
                                >
                                  <div className="mb-2 flex items-center justify-between gap-2">
                                    <div className="flex min-w-0 items-center gap-2">
                                      <div className="truncate text-sm font-semibold">
                                        {template.name}
                                      </div>
                                      {template.isSelected && (
                                        <Badge
                                          variant="secondary"
                                          className="shrink-0"
                                        >
                                          {"\u8868\u55ae\u9810\u586b"}
                                        </Badge>
                                      )}
                                    </div>
                                    <Badge variant="outline">
                                      {template.slug}
                                    </Badge>
                                  </div>
                                  <div
                                    className="text-sm text-muted-foreground [&_p]:mb-1.5 [&_p:last-child]:mb-0"
                                    // biome-ignore lint/security/noDangerouslySetInnerHtml: controlled markdown
                                    dangerouslySetInnerHTML={{
                                      __html: renderPlanMarkdown(template.why),
                                    }}
                                  />
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        <div className="space-y-2">
                          <div className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                            {"\u7533\u8acb\u539f\u56e0"}
                          </div>
                          <div
                            className="rounded-xl border border-border/60 bg-muted/10 px-3.5 py-3 text-sm text-muted-foreground [&_p]:mb-1.5 [&_p:last-child]:mb-0"
                            // biome-ignore lint/security/noDangerouslySetInnerHtml: controlled markdown
                            dangerouslySetInnerHTML={{
                              __html: renderPlanMarkdown(applicationReason),
                            }}
                          />
                        </div>

                        <div className="border-t pt-4">
                          <Button
                            type="button"
                            size="sm"
                            className="h-9 w-full"
                            onClick={handleImportPlan}
                          >
                            <Download className="mr-1.5 h-3 w-3" />
                            {t("aiChat.importPlan")}
                          </Button>
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              </Fragment>
            )
          })}

          {isLoading && (
            <div
              ref={loadingRef}
              className="animate-in fade-in flex gap-2.5 duration-200"
            >
              <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10">
                <Bot className="h-3 w-3 text-primary" />
              </div>
              <div className="rounded-xl rounded-tl-sm border border-border/60 bg-card/80 px-3 py-2">
                <div className="flex items-center gap-2 text-[13px] text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  {t("aiChat.loading")}
                </div>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>
      </div>

      <div className="mb-1 shrink-0 rounded-xl border border-border/60 bg-background/70 px-3 py-2.5">
        <Textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={t("aiChat.placeholder")}
          className="min-h-[44px] max-h-[120px] resize-none border-0 bg-transparent px-0 py-0.5 text-[12px] leading-4.5 shadow-none focus-visible:ring-0"
          disabled={isLoading}
        />
        <div className="flex items-center justify-between gap-2 border-t border-border/50 px-0 pb-0.5 pt-2">
          <div className="flex min-w-0 items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="h-7 px-1.5 text-[11px] text-muted-foreground"
              onClick={clearChat}
              disabled={isLoading}
            >
              <Trash2 className="h-3 w-3" />
            </Button>
            <Button
              type="button"
              size="sm"
              variant="secondary"
              className="h-7 rounded-full px-2.5 text-[11px]"
              onClick={generatePlan}
              disabled={isLoading || conversationHistory.length === 0}
            >
              <Sparkles className="mr-1 h-3 w-3" />
              {t("aiChat.generatePlan")}
            </Button>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <span className="text-[10px] text-muted-foreground">
              {inputLength}
            </span>
            <Button
              type="button"
              size="icon"
              className="h-7 w-7 rounded-full"
              onClick={sendChat}
              disabled={isLoading || !input.trim()}
            >
              <Send className="h-3 w-3" />
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
