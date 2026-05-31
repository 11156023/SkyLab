import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CheckCircle2, RefreshCw, ShieldAlert, Trash2 } from "lucide-react"
import { useMemo, useState } from "react"

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
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  AiJudgeService,
  type TeacherJudgeScriptArtifact,
  type TeacherJudgeScriptStatus,
} from "@/features/ai-judge/api"
import useCustomToast from "@/hooks/useCustomToast"
import { queryKeys } from "@/lib/queryKeys"

const STATUS_LABELS: Record<TeacherJudgeScriptStatus, string> = {
  draft: "草稿",
  review_failed: "審查未通過",
  reviewed: "待老師核准",
  approved: "已核准",
  archived: "已停用",
}

function ScriptStatusBadge({ status }: { status: TeacherJudgeScriptStatus }) {
  if (status === "approved") return <Badge>{STATUS_LABELS[status]}</Badge>
  if (status === "review_failed") {
    return <Badge variant="destructive">{STATUS_LABELS[status]}</Badge>
  }
  if (status === "archived") {
    return <Badge variant="outline">{STATUS_LABELS[status]}</Badge>
  }
  return <Badge variant="secondary">{STATUS_LABELS[status]}</Badge>
}

function ReviewPanel({
  title,
  result,
}: {
  title: string
  result: Record<string, any>
}) {
  const issues = Array.isArray(result.issues) ? result.issues : []

  return (
    <div className="rounded-md border bg-muted/20 p-3 text-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">{title}</span>
        <Badge variant={result.approved ? "secondary" : "destructive"}>
          {result.approved ? "通過" : "阻擋"}
        </Badge>
      </div>
      {issues.length > 0 ? (
        <ul className="mt-2 list-disc space-y-1 pl-5 text-muted-foreground">
          {issues.map((issue, index) => (
            <li key={`${title}-${index}`}>{String(issue)}</li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-muted-foreground">沒有列出風險項目。</p>
      )}
      {result.suggested_fix && (
        <p className="mt-2 text-muted-foreground">
          建議：{String(result.suggested_fix)}
        </p>
      )}
    </div>
  )
}

export function AiJudgeScriptsContent({ groupId }: { groupId: string }) {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] =
    useState<TeacherJudgeScriptArtifact | null>(null)

  const scriptsQuery = useQuery({
    queryKey: queryKeys.groups.teacherJudgeScripts(groupId),
    queryFn: () => AiJudgeService.listScripts({ groupId }),
  })

  const scripts = scriptsQuery.data ?? []
  const selected = useMemo<TeacherJudgeScriptArtifact | null>(() => {
    if (scripts.length === 0) return null
    return scripts.find((script) => script.id === selectedId) ?? scripts[0]
  }, [scripts, selectedId])

  const invalidateScripts = () =>
    queryClient.invalidateQueries({
      queryKey: queryKeys.groups.teacherJudgeScripts(groupId),
    })

  const approveMutation = useMutation({
    mutationFn: (scriptId: string) =>
      AiJudgeService.approveScript({ groupId, scriptId }),
    onSuccess: () => {
      showSuccessToast("收集腳本已核准")
      invalidateScripts()
    },
    onError: (err: any) =>
      showErrorToast(err?.body?.detail ?? err?.message ?? "核准失敗"),
  })

  const regenerateMutation = useMutation({
    mutationFn: (scriptId: string) =>
      AiJudgeService.regenerateScript({ groupId, scriptId }),
    onSuccess: (script) => {
      setSelectedId(script.id)
      showSuccessToast("收集腳本已重新生成")
      invalidateScripts()
    },
    onError: (err: any) =>
      showErrorToast(err?.body?.detail ?? err?.message ?? "重新生成失敗"),
  })

  const deleteMutation = useMutation({
    mutationFn: (scriptId: string) =>
      AiJudgeService.deleteScript({ groupId, scriptId }),
    onSuccess: (_data, scriptId) => {
      showSuccessToast("收集腳本已刪除")
      setSelectedId(null)
      setDeleteTarget(null)
      queryClient.setQueryData<TeacherJudgeScriptArtifact[]>(
        queryKeys.groups.teacherJudgeScripts(groupId),
        (current) => current?.filter((script) => script.id !== scriptId) ?? [],
      )
      invalidateScripts()
    },
    onError: (err: any) =>
      showErrorToast(err?.body?.detail ?? err?.message ?? "刪除失敗"),
  })

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="text-xl font-bold tracking-tight">AI 收集腳本</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          管理群組內由評分表產生的受管 Python 收集腳本。
        </p>
      </div>

      {scriptsQuery.isLoading ? (
        <div className="text-sm text-muted-foreground">載入腳本中...</div>
      ) : scriptsQuery.isError ? (
        <Card className="border-destructive/40 shadow-sm">
          <CardContent className="flex flex-wrap items-center justify-between gap-3 py-6 text-sm">
            <span className="text-destructive">
              載入收集腳本失敗，請稍後再試。
            </span>
            <Button
              size="sm"
              variant="outline"
              onClick={() => scriptsQuery.refetch()}
            >
              重新載入
            </Button>
          </CardContent>
        </Card>
      ) : scripts.length === 0 ? (
        <Card className="border-border/50 shadow-sm">
          <CardContent className="py-8 text-sm text-muted-foreground">
            尚未建立收集腳本。請先到「AI 情境分析」上傳評分表並製作腳本。
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-6 xl:grid-cols-[320px_1fr]">
          <div className="space-y-3">
            {scripts.map((script) => (
              <button
                key={script.id}
                type="button"
                className={`w-full rounded-lg border p-3 text-left transition ${
                  selected?.id === script.id
                    ? "border-primary bg-primary/5"
                    : "border-border bg-card hover:bg-muted/30"
                }`}
                onClick={() => setSelectedId(script.id)}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{script.name}</span>
                  <ScriptStatusBadge status={script.status} />
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  v{script.version} · {script.template_key} ·{" "}
                  {new Date(script.updated_at).toLocaleString("zh-TW")}
                </p>
              </button>
            ))}
          </div>

          {selected && (
            <div className="space-y-4">
              <Card className="border-border/50 shadow-sm">
                <CardHeader>
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <CardTitle className="flex items-center gap-2 text-base">
                      <ShieldAlert className="h-5 w-5 text-primary" />
                      {selected.name} v{selected.version}
                    </CardTitle>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        size="sm"
                        onClick={() => approveMutation.mutate(selected.id)}
                        disabled={
                          selected.status !== "reviewed" ||
                          approveMutation.isPending
                        }
                      >
                        <CheckCircle2 className="mr-1 h-4 w-4" />
                        核准
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => regenerateMutation.mutate(selected.id)}
                        disabled={
                          selected.status === "archived" ||
                          regenerateMutation.isPending
                        }
                      >
                        <RefreshCw className="mr-1 h-4 w-4" />
                        重新生成
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => setDeleteTarget(selected)}
                        disabled={deleteMutation.isPending}
                      >
                        <Trash2 className="mr-1 h-4 w-4" />
                        刪除腳本
                      </Button>
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="grid gap-3 md:grid-cols-2">
                    <ReviewPanel
                      title="Hard policy（靜態）"
                      result={selected.policy_check_result_json}
                    />
                    <ReviewPanel
                      title="AI reviewer"
                      result={selected.ai_review_result_json}
                    />
                  </div>
                  <pre className="max-h-[560px] overflow-auto rounded-md bg-muted/70 p-4 text-xs text-foreground">
                    {selected.script_content}
                  </pre>
                </CardContent>
              </Card>
            </div>
          )}
        </div>
      )}

      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open && !deleteMutation.isPending) {
            setDeleteTarget(null)
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>確認刪除收集腳本？</AlertDialogTitle>
            <AlertDialogDescription>
              {deleteTarget
                ? `你即將永久刪除「${deleteTarget.name}」v${deleteTarget.version}。刪除後無法再查看、核准或重新生成。`
                : "你即將永久刪除這份收集腳本。"}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteMutation.isPending}>
              取消
            </AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={deleteMutation.isPending || deleteTarget === null}
              onClick={(event) => {
                event.preventDefault()
                if (deleteTarget) {
                  deleteMutation.mutate(deleteTarget.id)
                }
              }}
            >
              {deleteMutation.isPending ? "刪除中..." : "確認刪除"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
