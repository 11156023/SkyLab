import { Download, FileSpreadsheet, Plus, Sparkles } from "lucide-react"
import { useCallback, useState } from "react"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  AiJudgeService,
  type ChatMessage,
  downloadBlob,
  getTemplateLabel,
  type RubricAnalysis,
  type RubricItem,
  rubricToContext,
  TEMPLATE_OPTIONS,
  type TemplateKey,
} from "@/features/ai-judge/api"
import {
  ChatPanel,
  RubricCard,
  RubricStats,
  RubricUploader,
} from "@/features/ai-judge/components"
import useCustomToast from "@/hooks/useCustomToast"

/**
 * AI Judge Content Block - extracted from Page
 */
export function AiJudgeContent({ groupId: _groupId }: { groupId: string }) {
  const { showSuccessToast, showErrorToast } = useCustomToast()

  // State
  const [analysis, setAnalysis] = useState<RubricAnalysis | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isUploading, setIsUploading] = useState(false)
  const [isChatting, setIsChatting] = useState(false)
  const [isExporting, setIsExporting] = useState(false)
  const [selectedTemplateKey, setSelectedTemplateKey] =
    useState<TemplateKey>("linux")
  const [analysisTemplateKey, setAnalysisTemplateKey] =
    useState<TemplateKey>("linux")

  // Computed
  const items = analysis?.items ?? []
  const stats = {
    totalItems: items.length,
    autoCount: items.filter((item) => item.detectable === "auto").length,
    partialCount: items.filter((item) => item.detectable === "partial").length,
    manualCount: items.filter((item) => item.detectable === "manual").length,
  }

  const applyItemsToAnalysis = useCallback(
    (base: RubricAnalysis, nextItems: RubricItem[]): RubricAnalysis => ({
      ...base,
      items: nextItems,
      total_items: nextItems.length,
      checked_count: nextItems.filter((item) => item.checked).length,
    }),
    [],
  )

  // Handlers
  const handleUpload = useCallback(
    async (file: File) => {
      setIsUploading(true)
      try {
        const response = await AiJudgeService.uploadRubric(
          file,
          selectedTemplateKey,
        )
        setAnalysis(response.analysis)
        setAnalysisTemplateKey(response.template_key ?? selectedTemplateKey)
        setMessages([])
        showSuccessToast(
          `分析完成：${response.analysis.items.length} 題評估項目`,
        )
      } catch (err: any) {
        showErrorToast(err?.body?.detail ?? err?.message ?? "上傳失敗")
      } finally {
        setIsUploading(false)
      }
    },
    [selectedTemplateKey, showSuccessToast, showErrorToast],
  )

  const handleSendMessage = useCallback(
    async (content: string, isRefine = false) => {
      if (!analysis) return

      const userMessage: ChatMessage = { role: "user", content }
      const newMessages = [...messages, userMessage]
      setMessages(newMessages)
      setIsChatting(true)

      try {
        const response = await AiJudgeService.chat({
          messages: newMessages,
          rubric_context: rubricToContext(analysis),
          is_refine: isRefine,
          template_key: analysisTemplateKey,
        })

        // Add assistant reply
        const assistantMessage: ChatMessage = {
          role: "assistant",
          content: response.reply,
        }
        setMessages((prev) => [...prev, assistantMessage])

        // Update items if changed
        if (response.updated_items) {
          setAnalysis((prev) =>
            prev
              ? applyItemsToAnalysis(
                  prev,
                  response.updated_items as RubricItem[],
                )
              : null,
          )
          showSuccessToast("評估表已更新")
        }
      } catch (err: any) {
        showErrorToast(err?.body?.detail ?? err?.message ?? "對話失敗")
        // Remove failed user message
        setMessages(messages)
      } finally {
        setIsChatting(false)
      }
    },
    [
      analysis,
      messages,
      showSuccessToast,
      showErrorToast,
      applyItemsToAnalysis,
      analysisTemplateKey,
    ],
  )
  const handleItemChange = useCallback(
    (index: number, updatedItem: RubricItem) => {
      if (!analysis) return
      const newItems = [...analysis.items]
      newItems[index] = updatedItem
      setAnalysis(applyItemsToAnalysis(analysis, newItems))
    },
    [analysis, applyItemsToAnalysis],
  )

  const handleItemDelete = useCallback(
    (index: number) => {
      if (!analysis) return
      const newItems = analysis.items.filter((_, i) => i !== index)
      setAnalysis(applyItemsToAnalysis(analysis, newItems))
    },
    [analysis, applyItemsToAnalysis],
  )

  const handleAddItem = useCallback(() => {
    if (!analysis) return
    const newItem: RubricItem = {
      id: `item-${Date.now()}`,
      title: "新評估項目",
      description: "",
      checked: false,
      detectable: "manual",
      detection_method: null,
      fallback: null,
      check_steps: [],
    }
    setAnalysis(applyItemsToAnalysis(analysis, [...analysis.items, newItem]))
  }, [analysis, applyItemsToAnalysis])

  const handleExport = useCallback(async () => {
    if (!analysis) return

    setIsExporting(true)
    try {
      const blob = await AiJudgeService.downloadExcel({
        items: analysis.items,
        summary: analysis.summary,
      })
      downloadBlob(blob, "rubric.xlsx")
      showSuccessToast("Excel 下載成功")
    } catch (err: any) {
      showErrorToast(err?.message ?? "匯出失敗")
    } finally {
      setIsExporting(false)
    }
  }, [analysis, showSuccessToast, showErrorToast])

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold tracking-tight">AI 情境分析</h2>
          <p className="text-sm text-muted-foreground mt-1">
            上傳評估表，查看 AI 偵測判斷並調整評估項目
          </p>
        </div>
        {analysis && (
          <Button onClick={handleExport} disabled={isExporting}>
            {isExporting ? (
              <>
                <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                匯出中...
              </>
            ) : (
              <>
                <Download className="mr-2 h-4 w-4" />
                匯出 Excel
              </>
            )}
          </Button>
        )}
      </div>

      {/* Main content */}
      {!analysis ? (
        /* Upload section */
        <Card className="shadow-sm border-border/50">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FileSpreadsheet className="h-5 w-5" />
              上傳情境評估表
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <span className="text-sm font-medium">評分環境</span>
              <div className="flex flex-wrap gap-2">
                {TEMPLATE_OPTIONS.map((option) => (
                  <Button
                    key={option.key}
                    type="button"
                    variant={
                      selectedTemplateKey === option.key ? "default" : "outline"
                    }
                    size="sm"
                    onClick={() => setSelectedTemplateKey(option.key)}
                    disabled={isUploading}
                  >
                    {option.label}
                  </Button>
                ))}
              </div>
            </div>
            <RubricUploader onUpload={handleUpload} isLoading={isUploading} />
          </CardContent>
        </Card>
      ) : (
        /* Analysis results */
        <div className="grid gap-6 xl:grid-cols-[1fr_400px]">
          {/* Left: Rubric items */}
          <div className="space-y-4">
            {/* Stats */}
            <Card className="shadow-sm border-border/50">
              <CardContent className="pt-6">
                <RubricStats {...stats} />
                <p className="mt-4 text-sm text-muted-foreground">
                  本次評分環境：{getTemplateLabel(analysisTemplateKey)}
                </p>
                {analysis.summary && (
                  <p className="mt-4 rounded-lg bg-muted/50 p-3 text-sm text-muted-foreground shadow-inner">
                    {analysis.summary}
                  </p>
                )}
              </CardContent>
            </Card>

            {/* Items */}
            <Card className="shadow-sm border-border/50">
              <CardHeader>
                <div className="flex items-center justify-between">
                  <CardTitle>評估項目 ({items.length})</CardTitle>
                  <Button variant="outline" size="sm" onClick={handleAddItem}>
                    <Plus className="mr-1 h-4 w-4" />
                    新增項目
                  </Button>
                </div>
              </CardHeader>
              <CardContent>
                <div className="space-y-3">
                  {items.map((item, index) => (
                    <RubricCard
                      key={item.id}
                      item={item}
                      index={index}
                      onChange={(updated) => handleItemChange(index, updated)}
                      onDelete={() => handleItemDelete(index)}
                      disabled={isChatting}
                    />
                  ))}
                </div>
              </CardContent>
            </Card>
          </div>
          {/* Right: Chat panel */}
          <Card className="flex h-[calc(100vh-250px)] min-h-[500px] flex-col xl:sticky xl:top-6 shadow-sm border-border/50 flex-shrink-0">
            <CardHeader className="border-b bg-muted/10 py-4">
              <CardTitle className="flex items-center gap-2 text-base">
                <Sparkles className="h-5 w-5 text-primary" />
                AI 對話助手
              </CardTitle>
            </CardHeader>
            <CardContent className="flex-1 overflow-hidden p-0">
              <ChatPanel
                messages={messages}
                onSendMessage={handleSendMessage}
                isLoading={isChatting}
                disabled={!analysis}
              />
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  )
}
