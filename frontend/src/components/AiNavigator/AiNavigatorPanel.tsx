import { useMutation } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { Bot, Loader2, MoveRight, Sparkles } from "lucide-react"
import { type FormEvent, useMemo, useState } from "react"

import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import useCustomToast from "@/hooks/useCustomToast"
import {
  AiNavigationService,
  type NavigationResolveResponse,
} from "@/services/aiNavigation"

function formatConfidence(score: number) {
  if (!Number.isFinite(score)) return "-"
  return `${Math.round(Math.max(0, Math.min(1, score)) * 100)}%`
}

function resolveStatusText(result: NavigationResolveResponse | null): string {
  if (!result) return "等待輸入需求"
  if (result.action === "navigate") return "可直接前往"
  if (result.action === "suggest") return "提供候選頁面"
  return "需要補充資訊"
}

export function AiNavigatorPanel() {
  const navigate = useNavigate()
  const { showErrorToast } = useCustomToast()
  const [query, setQuery] = useState("")
  const [result, setResult] = useState<NavigationResolveResponse | null>(null)

  const resolveMutation = useMutation({
    mutationFn: (text: string) => AiNavigationService.resolve(text),
    onSuccess: (data) => {
      setResult(data)
    },
    onError: (error) => {
      showErrorToast(error instanceof Error ? error.message : "請稍後再試")
      setResult({
        intent: query.trim(),
        confidence: 0,
        action: "clarify",
        primary: null,
        suggestions: [],
        clarification_question: "暫時無法判斷，請稍後再試一次。",
      })
    },
  })

  const canSubmit = query.trim().length >= 2 && !resolveMutation.isPending
  const statusText = useMemo(() => resolveStatusText(result), [result])

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const text = query.trim()
    if (!text || resolveMutation.isPending) return
    await resolveMutation.mutateAsync(text)
  }

  const goToPath = async (path: string) => {
    await navigate({ to: path })
  }

  return (
    <div className="space-y-4">
      <section className="rounded-2xl border border-border/70 bg-background/80 p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="space-y-1">
            <h2 className="text-lg font-semibold tracking-tight">AI 導覽</h2>
            <p className="text-xs text-muted-foreground">
              告訴我你想完成的事情，我會導到最合適的頁面。
            </p>
          </div>
          <div className="rounded-full border border-border/70 px-2.5 py-1 text-[11px] text-muted-foreground">
            {statusText}
          </div>
        </div>

        <form onSubmit={handleSubmit} className="mt-4 space-y-2.5">
          <Textarea
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="例如：我要申請一台 VM、我要看我的資源、我要設定反向代理、我要查 AI API 用量"
            className="min-h-22 resize-y text-sm"
            disabled={resolveMutation.isPending}
          />
          <div className="flex items-center justify-between gap-2">
            <p className="text-[11px] text-muted-foreground">
              目前字數：{query.trim().length}
            </p>
            <Button size="sm" type="submit" disabled={!canSubmit}>
              {resolveMutation.isPending ? (
                <>
                  <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                  分析中
                </>
              ) : (
                <>
                  <Sparkles className="mr-1.5 h-4 w-4" />
                  產生導覽
                </>
              )}
            </Button>
          </div>
        </form>
      </section>

      {result ? (
        <section className="space-y-3 rounded-2xl border border-border/70 bg-background/80 p-4">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span>意圖：{result.intent || "未提供"}</span>
            <span>·</span>
            <span>信心：{formatConfidence(result.confidence)}</span>
          </div>

          {result.primary ? (
            <div className="rounded-xl border border-primary/35 bg-primary/5 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2.5">
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground">主要建議</p>
                  <p className="text-base font-semibold">
                    {result.primary.title}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {result.primary.reason}
                  </p>
                  <p className="font-mono text-[11px] text-muted-foreground">
                    {result.primary.path}
                  </p>
                </div>
                <Button
                  size="sm"
                  onClick={() => void goToPath(result.primary!.path)}
                >
                  前往頁面
                  <MoveRight className="ml-1.5 h-4 w-4" />
                </Button>
              </div>
            </div>
          ) : null}

          {result.suggestions.length > 0 ? (
            <div className="space-y-2">
              <p className="text-xs font-medium">候選頁面</p>
              <div className="grid gap-2">
                {result.suggestions.map((item) => (
                  <button
                    key={item.path}
                    type="button"
                    onClick={() => void goToPath(item.path)}
                    className="text-left rounded-xl border border-border/70 bg-background px-3 py-2.5 transition-colors hover:border-primary/35 hover:bg-primary/5"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-sm font-medium">{item.title}</p>
                      <Bot className="h-4 w-4 text-primary" />
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {item.reason}
                    </p>
                    <p className="mt-1.5 font-mono text-[11px] text-muted-foreground">
                      {item.path}
                    </p>
                  </button>
                ))}
              </div>
            </div>
          ) : null}

          {result.clarification_question ? (
            <div className="rounded-xl border border-dashed border-border/80 bg-muted/20 p-3 text-xs text-muted-foreground">
              {result.clarification_question}
            </div>
          ) : null}
        </section>
      ) : null}
    </div>
  )
}
