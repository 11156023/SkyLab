import { useMutation, useQueryClient } from "@tanstack/react-query"
import {
  MonitorPlay,
  MoreVertical,
  Play,
  Power,
  RotateCcw,
  Square,
  Terminal,
  Trash2,
  XCircle,
} from "lucide-react"
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import useCustomToast from "@/hooks/useCustomToast"
import { queryKeys } from "@/lib/queryKeys"

interface VMActionsProps {
  vmid: number
  name: string
  type: string
  status: string
  onOpenConsole: (vmid: number, name: string, type: string) => void
}

export function VMActions({
  vmid,
  name,
  type,
  status,
  onOpenConsole,
}: VMActionsProps) {
  const { t } = useTranslation(["resources", "messages", "common"])
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const isRunning = status === "running"
  const isStopped = status === "stopped"
  const isLXC = type === "lxc"

  const startMutation = useMutation({
    mutationFn: () => ResourcesService.startResource({ vmid }),
    onSuccess: () => {
      showSuccessToast(t("messages:vm.starting", { name }))
      queryClient.invalidateQueries({ queryKey: queryKeys.resources.all })
    },
    onError: (error: Error) => {
      showErrorToast(
        t("messages:vm.startFailed", { name, error: error.message }),
      )
    },
  })

  const stopMutation = useMutation({
    mutationFn: () => ResourcesService.stopResource({ vmid }),
    onSuccess: () => {
      showSuccessToast(t("messages:vm.stopping", { name }))
      queryClient.invalidateQueries({ queryKey: queryKeys.resources.all })
    },
    onError: (error: Error) => {
      showErrorToast(
        t("messages:vm.stopFailed", { name, error: error.message }),
      )
    },
  })

  const rebootMutation = useMutation({
    mutationFn: () => ResourcesService.rebootResource({ vmid }),
    onSuccess: () => {
      showSuccessToast(t("messages:vm.rebooting", { name }))
      queryClient.invalidateQueries({ queryKey: queryKeys.resources.all })
    },
    onError: (error: Error) => {
      showErrorToast(
        t("messages:vm.rebootFailed", { name, error: error.message }),
      )
    },
  })

  const shutdownMutation = useMutation({
    mutationFn: () => ResourcesService.shutdownResource({ vmid }),
    onSuccess: () => {
      showSuccessToast(t("messages:vm.shuttingDown", { name }))
      queryClient.invalidateQueries({ queryKey: queryKeys.resources.all })
    },
    onError: (error: Error) => {
      showErrorToast(
        t("messages:vm.shutdownFailed", { name, error: error.message }),
      )
    },
  })

  const resetMutation = useMutation({
    mutationFn: () => ResourcesService.resetResource({ vmid }),
    onSuccess: () => {
      showSuccessToast(t("messages:vm.resetting", { name }))
      queryClient.invalidateQueries({ queryKey: queryKeys.resources.all })
    },
    onError: (error: Error) => {
      showErrorToast(
        t("messages:vm.resetFailed", { name, error: error.message }),
      )
    },
  })

  const deleteMutation = useMutation({
    mutationFn: () =>
      ResourcesService.deleteResource({ vmid, force: isRunning }),
    onSuccess: () => {
      showSuccessToast(
        t("messages:vm.deleteQueued", {
          name,
          defaultValue: `已將 ${name} 加入刪除佇列`,
        }),
      )
      // Invalidate deletion requests so the row immediately shows "deleting"
      // state via useDeletingResources. Do NOT invalidate resources.my here
      // to avoid the race where the refetch brings the machine back before the
      // background task completes — useDeletingResourcesLiveSync handles the
      // final invalidation once the deletion is confirmed.
      queryClient.invalidateQueries({ queryKey: ["deleting-resources"] })
      queryClient.invalidateQueries({ queryKey: queryKeys.resources.all })
      queryClient.invalidateQueries({ queryKey: ["jobs"] })
      setDeleteDialogOpen(false)
    },
    onError: (error: Error) => {
      showErrorToast(
        t("messages:vm.deleteFailed", { name, error: error.message }),
      )
      setDeleteDialogOpen(false)
    },
  })

  const isLoading =
    startMutation.isPending ||
    stopMutation.isPending ||
    rebootMutation.isPending ||
    shutdownMutation.isPending ||
    resetMutation.isPending ||
    deleteMutation.isPending

  return (
    <div className="flex items-center gap-2">
      <Button
        variant="outline"
        size="sm"
        disabled={!isRunning}
        onClick={(e) => {
          e.stopPropagation()
          onOpenConsole(vmid, name, type)
        }}
      >
        {isLXC ? (
          <Terminal className="h-4 w-4 mr-1" />
        ) : (
          <MonitorPlay className="h-4 w-4 mr-1" />
        )}
        {isLXC
          ? t("resources:actions.terminal")
          : t("resources:actions.console")}
      </Button>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="sm"
            disabled={isLoading}
            onClick={(e) => e.stopPropagation()}
          >
            <MoreVertical className="h-4 w-4" />
            <span className="sr-only">{t("resources:actions.openMenu")}</span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="end"
          className="w-48"
          onClick={(e) => e.stopPropagation()}
        >
          <DropdownMenuLabel>
            {t("resources:actions.powerControl")}
          </DropdownMenuLabel>
          <DropdownMenuSeparator />

          <DropdownMenuItem
            onClick={() => startMutation.mutate()}
            disabled={isRunning || isLoading}
            className="cursor-pointer"
          >
            <Play className="mr-2 h-4 w-4 text-green-600" />
            <span>{t("resources:actions.start")}</span>
          </DropdownMenuItem>

          <DropdownMenuItem
            onClick={() => shutdownMutation.mutate()}
            disabled={!isRunning || isLoading}
            className="cursor-pointer"
          >
            <Power className="mr-2 h-4 w-4 text-blue-600" />
            <span>{t("resources:actions.shutdown")}</span>
          </DropdownMenuItem>

          <DropdownMenuItem
            onClick={() => rebootMutation.mutate()}
            disabled={!isRunning || isLoading}
            className="cursor-pointer"
          >
            <RotateCcw className="mr-2 h-4 w-4 text-orange-600" />
            <span>{t("resources:actions.reboot")}</span>
          </DropdownMenuItem>

          <DropdownMenuSeparator />

          <DropdownMenuItem
            onClick={() => stopMutation.mutate()}
            disabled={isStopped || isLoading}
            className="cursor-pointer"
          >
            <Square className="mr-2 h-4 w-4 text-amber-600" />
            <span>{t("resources:actions.stopForce")}</span>
          </DropdownMenuItem>

          <DropdownMenuItem
            onClick={() => resetMutation.mutate()}
            disabled={!isRunning || isLoading}
            className="cursor-pointer"
          >
            <XCircle className="mr-2 h-4 w-4 text-red-600" />
            <span>{t("resources:actions.resetForce")}</span>
          </DropdownMenuItem>

          <DropdownMenuSeparator />

          <DropdownMenuItem
            onClick={() => setDeleteDialogOpen(true)}
            disabled={isLoading}
            className="cursor-pointer text-red-600 focus:text-red-600"
          >
            <Trash2 className="mr-2 h-4 w-4" />
            <span>{t("resources:actions.delete")}</span>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent onClick={(e) => e.stopPropagation()}>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("resources:actions.deleteConfirm.title")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("resources:actions.deleteConfirm.description", { name, vmid })}
              {isRunning && (
                <span className="block mt-2 text-amber-600 dark:text-amber-500">
                  {t("resources:actions.deleteConfirm.warningRunning")}
                </span>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteMutation.isPending}>
              {t("resources:actions.deleteConfirm.cancel")}
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={() => deleteMutation.mutate()}
              disabled={deleteMutation.isPending}
              className="bg-red-600 hover:bg-red-700 focus:ring-red-600"
            >
              {deleteMutation.isPending
                ? t("resources:actions.deleteConfirm.deleting")
                : t("resources:actions.deleteConfirm.delete")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
