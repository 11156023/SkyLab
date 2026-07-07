import {
  useMutation,
  useQueryClient,
  useSuspenseQuery,
} from "@tanstack/react-query"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import type { SpecChangeRequestCreate } from "@/client"
import {
  ResourceDetailsService,
  ResourcesService,
  SpecChangeRequestsService,
} from "@/client"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import useAuth from "@/hooks/useAuth"

interface SpecificationsTabProps {
  vmid: number
}

export default function SpecificationsTab({ vmid }: SpecificationsTabProps) {
  const { t } = useTranslation("resourceDetail")
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const isAdmin = user?.role === "admin" || user?.is_superuser || false

  const { data: config } = useSuspenseQuery({
    queryKey: ["resourceConfig", vmid],
    queryFn: () => ResourcesService.getResourceConfig({ vmid }),
  })

  const [cores, setCores] = useState<number>(config.cpu_cores || 1)
  const [memory, setMemory] = useState<number>(config.memory_mb || 512)
  const [reason, setReason] = useState<string>("")

  const directUpdateMutation = useMutation({
    mutationFn: (data: { cores?: number; memory?: number }) =>
      ResourceDetailsService.directUpdateSpec({ vmid, requestBody: data }),
    onSuccess: () => {
      toast.success(t("specifications.updateSuccess"))
      queryClient.invalidateQueries({ queryKey: ["resourceConfig", vmid] })
    },
    onError: () => {
      toast.error(t("specifications.updateError"))
    },
  })

  const requestMutation = useMutation({
    mutationFn: (data: SpecChangeRequestCreate) =>
      SpecChangeRequestsService.createSpecChangeRequest({ requestBody: data }),
    onSuccess: () => {
      toast.success(t("specifications.requestSubmitted"))
      setReason("")
    },
    onError: () => {
      toast.error(t("specifications.requestError"))
    },
  })

  const handleSubmit = () => {
    if (isAdmin) {
      // Admin: direct update
      directUpdateMutation.mutate({
        cores: cores !== config.cpu_cores ? cores : undefined,
        memory: memory !== config.memory_mb ? memory : undefined,
      })
    } else {
      // User: submit request
      if (reason.length < 10) {
        toast.error(t("specifications.reasonTooShort"))
        return
      }

      const hasChanges =
        cores !== config.cpu_cores || memory !== config.memory_mb
      if (!hasChanges) {
        toast.error(t("specifications.noChanges"))
        return
      }

      requestMutation.mutate({
        vmid,
        change_type: "combined",
        reason,
        requested_cpu: cores !== config.cpu_cores ? cores : undefined,
        requested_memory: memory !== config.memory_mb ? memory : undefined,
      })
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>{t("specifications.title")}</CardTitle>
          <CardDescription>
            {isAdmin
              ? t("specifications.adminDesc")
              : t("specifications.userDesc")}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="cores">CPU {t("overview.cores")}</Label>
              <Input
                id="cores"
                type="number"
                min={1}
                max={32}
                value={cores}
                onChange={(e) => setCores(Number.parseInt(e.target.value, 10))}
              />
              <p className="text-xs text-muted-foreground">
                {t("specifications.currentValue")}: {config.cpu_cores}
              </p>
            </div>

            <div className="space-y-2">
              <Label htmlFor="memory">{t("overview.memory")} (MB)</Label>
              <Input
                id="memory"
                type="number"
                min={512}
                max={65536}
                step={512}
                value={memory}
                onChange={(e) => setMemory(Number.parseInt(e.target.value, 10))}
              />
              <p className="text-xs text-muted-foreground">
                {t("specifications.currentValue")}: {config.memory_mb} MB
              </p>
            </div>
          </div>

          {!isAdmin && (
            <div className="space-y-2">
              <Label htmlFor="reason">{t("specifications.reason")} *</Label>
              <Textarea
                id="reason"
                placeholder={t("specifications.reasonPlaceholder")}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={4}
              />
              <p className="text-xs text-muted-foreground">
                {t("specifications.reasonMinLength")}
              </p>
            </div>
          )}

          <Button
            onClick={handleSubmit}
            disabled={
              directUpdateMutation.isPending || requestMutation.isPending
            }
          >
            {isAdmin
              ? t("specifications.applyChanges")
              : t("specifications.submitRequest")}
          </Button>
        </CardContent>
      </Card>

      {!isAdmin && (
        <Card>
          <CardHeader>
            <CardTitle>{t("specifications.approvalProcess")}</CardTitle>
          </CardHeader>
          <CardContent>
            <ol className="list-decimal list-inside space-y-2 text-sm text-muted-foreground">
              <li>{t("specifications.step1")}</li>
              <li>{t("specifications.step2")}</li>
              <li>{t("specifications.step3")}</li>
            </ol>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
