import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Play, Power, RotateCcw, Square, Trash2, XCircle } from "lucide-react"
import { useState } from "react"
import { useTranslation } from "react-i18next"

import { ResourcesService } from "@/client"
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
import useCustomToast from "@/hooks/useCustomToast"
import { queryKeys } from "@/lib/queryKeys"

interface BatchActionBarProps {
  selectedVmids: number[]
  onClearSelection: () => void
}

export function BatchActionBar({
  selectedVmids,
  onClearSelection,
}: BatchActionBarProps) {
  const { t } = useTranslation(["resources", "messages"])
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const count = selectedVmids.length

  const batchMutation = useMutation({
    mutationFn: (action: string) =>
      ResourcesService.batchAction({
        requestBody: { vmids: selectedVmids, action },
      }),
    onSuccess: (data, action) => {
      const { succeeded, failed } = data
      if (failed === 0) {
        showSuccessToast(
          t("resources:batch.success", {
            count: succeeded,
            action: t(`resources:batch.actions.${action}`),
          }),
        )
      } else {
        showErrorToast(
          t("resources:batch.partial", {
            succeeded,
            failed,
            action: t(`resources:batch.actions.${action}`),
          }),
        )
      }
      if (action === "delete") {
        queryClient.setQueryData(
          queryKeys.resources.my,
          (old: Array<{ vmid: number }> | undefined) =>
            old?.filter((r) => !selectedVmids.includes(r.vmid)) ?? [],
        )
      }
      queryClient.invalidateQueries({ queryKey: queryKeys.resources.all })
      queryClient.invalidateQueries({ queryKey: queryKeys.resources.my })
      onClearSelection()
      setDeleteDialogOpen(false)
    },
    onError: (error: Error) => {
      showErrorToast(error.message)
      setDeleteDialogOpen(false)
    },
  })

  if (count === 0) return null

  return (
    <>
      <div className="flex items-center gap-2 rounded-lg border bg-muted/50 px-4 py-2">
        <span className="text-sm font-medium">
          {t("resources:batch.selected", { count })}
        </span>

        <div className="mx-2 h-4 w-px bg-border" />

        <Button
          variant="outline"
          size="sm"
          onClick={() => batchMutation.mutate("start")}
          disabled={batchMutation.isPending}
        >
          <Play className="mr-1 h-4 w-4 text-green-600" />
          {t("resources:actions.start")}
        </Button>

        <Button
          variant="outline"
          size="sm"
          onClick={() => batchMutation.mutate("shutdown")}
          disabled={batchMutation.isPending}
        >
          <Power className="mr-1 h-4 w-4 text-blue-600" />
          {t("resources:actions.shutdown")}
        </Button>

        <Button
          variant="outline"
          size="sm"
          onClick={() => batchMutation.mutate("reboot")}
          disabled={batchMutation.isPending}
        >
          <RotateCcw className="mr-1 h-4 w-4 text-orange-600" />
          {t("resources:actions.reboot")}
        </Button>

        <Button
          variant="outline"
          size="sm"
          onClick={() => batchMutation.mutate("stop")}
          disabled={batchMutation.isPending}
        >
          <Square className="mr-1 h-4 w-4 text-amber-600" />
          {t("resources:actions.stopForce")}
        </Button>

        <Button
          variant="outline"
          size="sm"
          onClick={() => batchMutation.mutate("reset")}
          disabled={batchMutation.isPending}
        >
          <XCircle className="mr-1 h-4 w-4 text-red-600" />
          {t("resources:actions.resetForce")}
        </Button>

        <div className="mx-2 h-4 w-px bg-border" />

        <Button
          variant="destructive"
          size="sm"
          onClick={() => setDeleteDialogOpen(true)}
          disabled={batchMutation.isPending}
        >
          <Trash2 className="mr-1 h-4 w-4" />
          {t("resources:actions.delete")}
        </Button>

        <div className="mx-2 h-4 w-px bg-border" />

        <Button
          variant="ghost"
          size="sm"
          onClick={onClearSelection}
          disabled={batchMutation.isPending}
        >
          {t("resources:batch.clearSelection")}
        </Button>
      </div>

      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("resources:batch.deleteConfirm.title", { count })}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("resources:batch.deleteConfirm.description", { count })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={batchMutation.isPending}>
              {t("resources:actions.deleteConfirm.cancel")}
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={() => batchMutation.mutate("delete")}
              disabled={batchMutation.isPending}
              className="bg-red-600 hover:bg-red-700 focus:ring-red-600"
            >
              {batchMutation.isPending
                ? t("resources:actions.deleteConfirm.deleting")
                : t("resources:batch.deleteConfirm.confirm", { count })}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
