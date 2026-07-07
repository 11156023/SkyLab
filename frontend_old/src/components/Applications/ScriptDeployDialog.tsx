import { useMutation, useQuery } from "@tanstack/react-query"
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Loader2,
  Rocket,
  ScrollText,
  Server,
  XCircle,
} from "lucide-react"
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react"
import { useTranslation } from "react-i18next"

import type {
  ScriptDeployStatus as DeployStatus,
  ScriptDeployRequest,
} from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import useCustomToast from "@/hooks/useCustomToast"
import { ScriptDeployApi } from "@/services/scriptDeploy"

import type { FastTemplate } from "./FastTemplatesTab"

function stripAnsi(text: string): string {
  const ansiControl = "\\u001B"
  const bell = "\\u0007"

  return text
    .replace(new RegExp(`${ansiControl}\\[[0-9;]*[A-Za-z]`, "g"), "")
    .replace(new RegExp(`${ansiControl}\\][^${bell}]*${bell}`, "g"), "")
    .replace(new RegExp(`${ansiControl}[^[\\]()][^${ansiControl}]*`, "g"), "")
    .replace(new RegExp(ansiControl, "g"), "")
    .replace(/\[([0-9;]*)[A-Za-z]/g, "")
    .replace(/\[\?[0-9;]*[A-Za-z]/g, "")
    .replace(/\r/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim()
}

export type ScriptDeployFormData = {
  hostname: string
  password: string
  cpu: number
  ram: number
  disk: number
  unprivileged: boolean
  ssh: boolean
}

type ScriptDeployPageProps = {
  template: FastTemplate
  formData: ScriptDeployFormData
  onBack: () => void
  onComplete: (vmid?: number) => void
}

export function ScriptDeployPage({
  template,
  formData,
  onBack,
  onComplete,
}: ScriptDeployPageProps) {
  const { t } = useTranslation("applications")
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const defaultMethod = template.install_methods?.[0]
  const scriptPath = defaultMethod?.script || `ct/${template.slug}.sh`
  const [taskId, setTaskId] = useState<string | null>(null)
  const deployStartedRef = useRef(false)
  const registrationHandledRef = useRef<string | null>(null)
  const logEndRef = useRef<HTMLDivElement | null>(null)

  const statusQuery = useQuery<DeployStatus>({
    queryKey: ["script-deploy", taskId],
    queryFn: () => ScriptDeployApi.getStatus({ taskId: taskId! }),
    enabled: Boolean(taskId),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (status === "completed" || status === "failed") return false
      return 3000
    },
    retry: false,
    refetchOnWindowFocus: false,
  })

  const status = statusQuery.data ?? null
  const cleanedOutput = useMemo(
    () => (status?.output ? stripAnsi(status.output) : ""),
    [status?.output],
  )

  useLayoutEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [])

  const deployMutation = useMutation({
    mutationFn: () => {
      const requestBody: ScriptDeployRequest = {
        template_slug: template.slug,
        script_path: scriptPath,
        hostname: formData.hostname,
        password: formData.password,
        cpu: formData.cpu,
        ram: formData.ram,
        disk: formData.disk,
        unprivileged: formData.unprivileged,
        ssh: formData.ssh,
        environment_type: "教學環境",
        os_info: template.name || null,
      }

      return ScriptDeployApi.deploy({ requestBody })
    },
    onSuccess: (data) => {
      setTaskId(data.task_id)
    },
    onError: (err) => {
      showErrorToast(err.message || "部署失敗")
    },
  })

  useEffect(() => {
    if (!deployStartedRef.current) {
      deployStartedRef.current = true
      deployMutation.mutate()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deployMutation.mutate])

  useEffect(() => {
    if (!taskId || !status) return
    if (status.status === "running" || status.status === "pending") return
    if (registrationHandledRef.current === taskId) return

    registrationHandledRef.current = taskId

    if (status.status === "completed") {
      void ScriptDeployApi.register({ taskId }).catch(() => {
        // Registration failure is non-fatal.
      })
      showSuccessToast(status.message || `部署完成，VMID: ${status.vmid}`)
      return
    }

    showErrorToast(status.error || "部署失敗")
  }, [showErrorToast, showSuccessToast, status, taskId])

  const isDone = status?.status === "completed" || status?.status === "failed"

  return (
    <div className="mx-auto w-full max-w-[760px] space-y-6">
      <div className="flex items-start gap-3">
        <Button
          variant="outline"
          size="icon"
          className="mt-0.5 shrink-0"
          onClick={isDone ? onBack : undefined}
          disabled={!isDone}
        >
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Rocket className="h-5 w-5 text-primary" />
            <h1 className="text-2xl font-bold tracking-tight">
              {t("deploy.title", { defaultValue: "快速部署" })}
            </h1>
            <Badge variant="secondary">{template.name}</Badge>
          </div>
          <p className="text-muted-foreground">
            {t("deploy.description", {
              defaultValue:
                "系統會依照模板與表單設定建立資源，並持續同步部署狀態。",
            })}
          </p>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="rounded-lg border bg-card p-3">
          <div className="text-xs text-muted-foreground">Hostname</div>
          <div className="mt-0.5 truncate text-sm font-medium">
            {formData.hostname}
          </div>
        </div>
        <div className="rounded-lg border bg-card p-3">
          <div className="text-xs text-muted-foreground">CPU</div>
          <div className="mt-0.5 text-sm font-medium">{formData.cpu} Cores</div>
        </div>
        <div className="rounded-lg border bg-card p-3">
          <div className="text-xs text-muted-foreground">RAM</div>
          <div className="mt-0.5 text-sm font-medium">
            {(formData.ram / 1024).toFixed(1)} GB
          </div>
        </div>
        <div className="rounded-lg border bg-card p-3">
          <div className="text-xs text-muted-foreground">Disk</div>
          <div className="mt-0.5 text-sm font-medium">{formData.disk} GB</div>
        </div>
      </div>

      <div className="rounded-lg border bg-muted/50 p-3 text-sm text-muted-foreground">
        <span className="font-medium">
          {t("deploy.scriptPath", { defaultValue: "腳本路徑" })}:
        </span>
        <code className="ml-1">{scriptPath}</code>
      </div>

      <div className="flex items-center gap-2">
        {(!status ||
          status.status === "running" ||
          status.status === "pending") && (
          <>
            <Loader2 className="h-5 w-5 animate-spin text-primary" />
            <span className="text-sm">
              {status?.progress ||
                t("deploy.running", { defaultValue: "部署中" })}
            </span>
          </>
        )}
        {status?.status === "completed" && (
          <>
            <CheckCircle2 className="h-5 w-5 text-green-500" />
            <span className="text-sm font-medium text-green-600">
              {status.message}
            </span>
          </>
        )}
        {status?.status === "failed" && (
          <>
            <XCircle className="h-5 w-5 text-destructive" />
            <span className="text-sm text-destructive">
              {t("deploy.failed", { defaultValue: "部署失敗" })}
            </span>
          </>
        )}
      </div>

      {cleanedOutput && (
        <div className="overflow-hidden rounded-lg border border-border/50 bg-zinc-950">
          <div className="flex items-center gap-1.5 border-b border-border/30 bg-zinc-900/80 px-3 py-1.5">
            <ScrollText className="h-3.5 w-3.5 text-zinc-500" />
            <span className="text-xs font-medium text-zinc-400">
              {t("deploy.log", { defaultValue: "部署日誌" })}
            </span>
            {status?.status === "running" && (
              <span className="ml-auto flex items-center gap-1 text-[10px] text-emerald-500">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" />
                LIVE
              </span>
            )}
          </div>
          <pre className="max-h-[60vh] overflow-auto px-3 py-2 font-mono text-xs leading-relaxed text-zinc-300 scrollbar-thin scrollbar-track-transparent scrollbar-thumb-zinc-700">
            {cleanedOutput}
            <div ref={logEndRef} />
          </pre>
        </div>
      )}

      {status?.status === "failed" && status.error && (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3">
          <div className="mb-1 flex items-center gap-1.5 text-xs font-medium text-destructive">
            <AlertTriangle className="h-3.5 w-3.5" />
            {t("deploy.errorDetail", { defaultValue: "錯誤詳情" })}
          </div>
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap text-xs text-muted-foreground">
            {stripAnsi(status.error)}
          </pre>
        </div>
      )}

      {status?.status === "completed" && status.vmid && (
        <div className="rounded-lg border bg-green-500/5 p-3">
          <div className="flex items-center gap-1.5 text-sm">
            <Server className="h-4 w-4" />
            VMID: <span className="font-mono font-bold">{status.vmid}</span>
          </div>
        </div>
      )}

      {isDone && (
        <div className="flex gap-3 border-t pt-4">
          <Button variant="outline" onClick={onBack}>
            {t("deploy.close", { defaultValue: "關閉" })}
          </Button>
          {status?.status === "completed" && (
            <Button onClick={() => onComplete(status?.vmid ?? undefined)}>
              {t("deploy.goToResources", {
                defaultValue: "前往資源列表",
              })}
            </Button>
          )}
        </div>
      )}
    </div>
  )
}
