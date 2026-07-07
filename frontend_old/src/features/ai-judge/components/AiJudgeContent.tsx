import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  Download,
  FileSpreadsheet,
  FileText,
  Plus,
  RefreshCw,
  Sparkles,
  Trash2,
} from "lucide-react"
import { useCallback, useState } from "react"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
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
  type TeacherJudgeFile,
  type TemplateKey,
} from "@/features/ai-judge/api"
import {
  ChatPanel,
  RubricCard,
  RubricStats,
  RubricUploader,
} from "@/features/ai-judge/components"
import useCustomToast from "@/hooks/useCustomToast"

const teacherJudgeFilesQueryKey = (groupId: string) =>
  ["group", groupId, "teacher-judge-files"] as const

function getJudgeFileName(file: TeacherJudgeFile) {
  return file.original_filename
}

/**
 * AI Judge Content Block - extracted from Page
 */
export function AiJudgeContent({
  groupId,
  onScriptCreated,
}: {
  groupId: string
  onScriptCreated?: () => void
}) {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  // State
  const [analysis, setAnalysis] = useState<RubricAnalysis | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isUploading, setIsUploading] = useState(false)
  const [isChatting, setIsChatting] = useState(false)
  const [isExporting, setIsExporting] = useState(false)
  const [isCreatingScript, setIsCreatingScript] = useState(false)
  const [uploadedFileName, setUploadedFileName] = useState("rubric")
  const [sourceFileId, setSourceFileId] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<TeacherJudgeFile | null>(
    null,
  )
  const [pendingConflictFile, setPendingConflictFile] = useState<File | null>(
    null,
  )
  const [selectedTemplateKey, setSelectedTemplateKey] =
    useState<TemplateKey>("linux")
  const [analysisTemplateKey, setAnalysisTemplateKey] =
    useState<TemplateKey>("linux")

  const filesQuery = useQuery({
    queryKey: teacherJudgeFilesQueryKey(groupId),
    queryFn: () => AiJudgeService.listFiles({ groupId }),
  })

  const invalidateFiles = useCallback(
    () =>
      queryClient.invalidateQueries({
        queryKey: teacherJudgeFilesQueryKey(groupId),
      }),
    [groupId, queryClient],
  )

  const updateFileAnalysisMutation = useMutation({
    mutationFn: (nextAnalysis: RubricAnalysis) => {
      if (!sourceFileId) {
        throw new Error("沒有可更新的評分表文件")
      }
      return AiJudgeService.updateFileAnalysis({
        groupId,
        fileId: sourceFileId,
        analysis: nextAnalysis,
      })
    },
    onSuccess: (file) => {
      queryClient.setQueryData<TeacherJudgeFile[]>(
        teacherJudgeFilesQueryKey(groupId),
        (current) =>
          current?.map((item) => (item.id === file.id ? file : item)) ?? [file],
      )
    },
    onError: (err: any) =>
      showErrorToast(err?.body?.detail ?? err?.message ?? "更新評分表失敗"),
  })

  const deleteFileMutation = useMutation({
    mutationFn: (fileId: string) =>
      AiJudgeService.deleteFile({ groupId, fileId }),
    onSuccess: (_data, fileId) => {
      showSuccessToast("評分表已刪除")
      setDeleteTarget(null)
      queryClient.setQueryData<TeacherJudgeFile[]>(
        teacherJudgeFilesQueryKey(groupId),
        (current) => current?.filter((file) => file.id !== fileId) ?? [],
      )
      if (sourceFileId === fileId) {
        setSourceFileId(null)
      }
      invalidateFiles()
    },
    onError: (err: any) =>
      showErrorToast(err?.body?.detail ?? err?.message ?? "刪除評分表失敗"),
  })

  const files = filesQuery.data ?? []

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
  const applyAnalysis = useCallback(
    (nextAnalysis: RubricAnalysis, options?: { persist?: boolean }) => {
      setAnalysis(nextAnalysis)
      if (options?.persist && sourceFileId) {
        updateFileAnalysisMutation.mutate(nextAnalysis)
      }
    },
    [sourceFileId, updateFileAnalysisMutation],
  )

  const handleUpload = useCallback(
    async (file: File, conflictStrategy?: "overwrite" | "copy") => {
      setIsUploading(true)
      try {
        const response = await AiJudgeService.uploadFile({
          groupId,
          file,
          template_key: selectedTemplateKey,
          conflict_strategy: conflictStrategy,
        })
        const uploadedFile: TeacherJudgeFile = {
          ...response.file,
          analysis_json: response.file.analysis_json ?? response.analysis,
        }
        setAnalysis(response.analysis)
        setUploadedFileName(file.name || "rubric")
        setSourceFileId(uploadedFile.id)
        setAnalysisTemplateKey(response.template_key ?? selectedTemplateKey)
        setMessages([])
        setPendingConflictFile(null)
        queryClient.setQueryData<TeacherJudgeFile[]>(
          teacherJudgeFilesQueryKey(groupId),
          (current) => {
            const existing = current ?? []
            const withoutUploaded = existing.filter(
              (item) => item.id !== uploadedFile.id,
            )
            return [uploadedFile, ...withoutUploaded]
          },
        )
        invalidateFiles()
        showSuccessToast(
          `分析完成：${response.analysis.items.length} 題評估項目`,
        )
      } catch (err: any) {
        if ((err?.status ?? err?.body?.status_code) === 409) {
          setPendingConflictFile(file)
        } else {
          showErrorToast(err?.body?.detail ?? err?.message ?? "上傳失敗")
        }
      } finally {
        setIsUploading(false)
      }
    },
    [
      groupId,
      selectedTemplateKey,
      showSuccessToast,
      showErrorToast,
      queryClient,
      invalidateFiles,
    ],
  )

  const handleSelectFile = useCallback(
    (file: TeacherJudgeFile) => {
      if (!file.analysis_json) {
        showErrorToast("這份評分表尚未有可載入的分析結果")
        return
      }
      const fileName = getJudgeFileName(file)
      setAnalysis(file.analysis_json)
      setUploadedFileName(fileName || "rubric")
      setSourceFileId(file.id)
      setAnalysisTemplateKey(file.template_key)
      setSelectedTemplateKey(file.template_key)
      setMessages([])
      showSuccessToast(`已載入「${fileName}」`)
    },
    [showErrorToast, showSuccessToast],
  )

  const handleDownloadFile = useCallback(
    async (file: TeacherJudgeFile) => {
      try {
        const blob = await AiJudgeService.downloadFile({
          groupId,
          fileId: file.id,
        })
        downloadBlob(blob, getJudgeFileName(file))
      } catch (err: any) {
        showErrorToast(err?.body?.detail ?? err?.message ?? "下載評分表失敗")
      }
    },
    [groupId, showErrorToast],
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
          const nextAnalysis = applyItemsToAnalysis(
            analysis,
            response.updated_items as RubricItem[],
          )
          applyAnalysis(nextAnalysis, { persist: true })
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
      applyAnalysis,
      analysisTemplateKey,
    ],
  )
  const handleItemChange = useCallback(
    (index: number, updatedItem: RubricItem) => {
      if (!analysis) return
      const newItems = [...analysis.items]
      newItems[index] = updatedItem
      applyAnalysis(applyItemsToAnalysis(analysis, newItems), {
        persist: true,
      })
    },
    [analysis, applyItemsToAnalysis, applyAnalysis],
  )

  const handleItemDelete = useCallback(
    (index: number) => {
      if (!analysis) return
      const newItems = analysis.items.filter((_, i) => i !== index)
      applyAnalysis(applyItemsToAnalysis(analysis, newItems), {
        persist: true,
      })
    },
    [analysis, applyItemsToAnalysis, applyAnalysis],
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
    applyAnalysis(
      applyItemsToAnalysis(analysis, [...analysis.items, newItem]),
      {
        persist: true,
      },
    )
  }, [analysis, applyItemsToAnalysis, applyAnalysis])

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

  const handleCreateScript = useCallback(async () => {
    if (!analysis) return

    setIsCreatingScript(true)
    try {
      const artifact = await AiJudgeService.createScript({
        groupId,
        name: uploadedFileName,
        template_key: analysisTemplateKey,
        rubric_snapshot: analysis,
        source_file_id: sourceFileId,
      })
      showSuccessToast(
        artifact.status === "reviewed"
          ? "收集腳本已產生並通過審查"
          : "收集腳本已產生，請查看審查結果",
      )
      onScriptCreated?.()
    } catch (err: any) {
      showErrorToast(err?.body?.detail ?? err?.message ?? "製作收集腳本失敗")
    } finally {
      setIsCreatingScript(false)
    }
  }, [
    analysis,
    groupId,
    uploadedFileName,
    analysisTemplateKey,
    sourceFileId,
    showSuccessToast,
    showErrorToast,
    onScriptCreated,
  ])

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold tracking-tight">評分表</h2>
          <p className="text-sm text-muted-foreground mt-1">
            上傳評估表，查看 AI 偵測判斷並調整評估項目
          </p>
        </div>
        {analysis && (
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button
              onClick={handleCreateScript}
              disabled={isCreatingScript || isChatting}
            >
              {isCreatingScript ? (
                <>
                  <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                  製作中...
                </>
              ) : (
                <>
                  <Sparkles className="mr-2 h-4 w-4" />
                  製作收集腳本
                </>
              )}
            </Button>
            <Button
              variant="outline"
              onClick={handleExport}
              disabled={isExporting}
            >
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
          </div>
        )}
      </div>

      {isCreatingScript && (
        <Card className="border-primary/30 bg-primary/5 shadow-sm">
          <CardContent className="flex items-start gap-3 py-4 text-sm">
            <span className="mt-0.5 h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            <div className="space-y-1">
              <p className="font-medium text-foreground">
                正在生成受管收集腳本
              </p>
              <p className="text-muted-foreground">
                AI 正在依目前評分項目與環境命令產生 Python
                收集腳本，系統會接著執行 hard policy 與 AI reviewer 審查。
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      <Card className="shadow-sm border-border/50">
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <CardTitle className="flex items-center gap-2">
              <FileText className="h-5 w-5" />
              已保存評分表
            </CardTitle>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => filesQuery.refetch()}
              disabled={filesQuery.isFetching}
            >
              <RefreshCw className="mr-1 h-4 w-4" />
              重新整理
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {filesQuery.isLoading ? (
            <p className="text-sm text-muted-foreground">載入評分表中...</p>
          ) : filesQuery.isError ? (
            <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
              <span className="text-destructive">
                載入評分表失敗，請稍後再試。
              </span>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => filesQuery.refetch()}
              >
                重新載入
              </Button>
            </div>
          ) : files.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              尚未保存評分表。上傳文件後會自動保存原始檔與分析結果。
            </p>
          ) : (
            <div className="space-y-2">
              {files.map((file) => {
                const fileName = getJudgeFileName(file)
                return (
                  <div
                    key={file.id}
                    className={`flex flex-wrap items-center justify-between gap-3 rounded-lg border p-3 ${
                      sourceFileId === file.id
                        ? "border-primary bg-primary/5"
                        : "border-border bg-card"
                    }`}
                  >
                    <button
                      type="button"
                      className="min-w-0 flex-1 text-left"
                      onClick={() => handleSelectFile(file)}
                    >
                      <p className="truncate text-sm font-medium">{fileName}</p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {getTemplateLabel(file.template_key)} ·{" "}
                        {new Date(file.updated_at).toLocaleString("zh-TW")}
                        {file.status === "replaced" ? " · 已取代" : ""}
                      </p>
                    </button>
                    <div className="flex items-center gap-2">
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        onClick={() => handleDownloadFile(file)}
                      >
                        <Download className="mr-1 h-4 w-4" />
                        原檔
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        onClick={() => setDeleteTarget(file)}
                        disabled={deleteFileMutation.isPending}
                      >
                        <Trash2 className="mr-1 h-4 w-4" />
                        刪除
                      </Button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </CardContent>
      </Card>

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

      <AlertDialog
        open={pendingConflictFile !== null}
        onOpenChange={(open) => {
          if (!open && !isUploading) {
            setPendingConflictFile(null)
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>已有同名評分表</AlertDialogTitle>
            <AlertDialogDescription>
              {pendingConflictFile
                ? `「${pendingConflictFile.name}」已存在。請選擇覆蓋原本文件，或建立一份副本後重新分析。`
                : "此評分表已存在。請選擇覆蓋或建立副本。"}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isUploading}>取消</AlertDialogCancel>
            <Button
              type="button"
              variant="outline"
              disabled={isUploading || pendingConflictFile === null}
              onClick={() => {
                if (pendingConflictFile) {
                  handleUpload(pendingConflictFile, "copy")
                }
              }}
            >
              建立副本
            </Button>
            <AlertDialogAction
              disabled={isUploading || pendingConflictFile === null}
              onClick={(event) => {
                event.preventDefault()
                if (pendingConflictFile) {
                  handleUpload(pendingConflictFile, "overwrite")
                }
              }}
            >
              覆蓋原本
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open && !deleteFileMutation.isPending) {
            setDeleteTarget(null)
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>確認刪除評分表？</AlertDialogTitle>
            <AlertDialogDescription>
              {deleteTarget
                ? `你即將刪除「${getJudgeFileName(deleteTarget)}」的原始檔與保存分析。刪除後不會影響已建立的腳本。`
                : "你即將刪除這份評分表。"}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteFileMutation.isPending}>
              取消
            </AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={deleteFileMutation.isPending || deleteTarget === null}
              onClick={(event) => {
                event.preventDefault()
                if (deleteTarget) {
                  deleteFileMutation.mutate(deleteTarget.id)
                }
              }}
            >
              {deleteFileMutation.isPending ? "刪除中..." : "確認刪除"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
