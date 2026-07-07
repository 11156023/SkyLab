import {
  useMutation,
  useQueryClient,
  useSuspenseQuery,
} from "@tanstack/react-query"
import { RefreshCcw, RotateCcw, ShieldCheck, Trash2 } from "lucide-react"
import { useState } from "react"
import { useTranslation } from "react-i18next"
import { toast } from "sonner"
import type { SnapshotCreateRequest } from "@/client"
import { ResourceDetailsService } from "@/client"
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
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Textarea } from "@/components/ui/textarea"

const INIT_SNAPSHOT_NAME = "skylab-init"

interface SnapshotsTabProps {
  vmid: number
}

function errorDetail(error: unknown): string | undefined {
  const maybe = error as { detail?: string; message?: string }
  return maybe?.detail || maybe?.message
}

export default function SnapshotsTab({ vmid }: SnapshotsTabProps) {
  const { t } = useTranslation("resourceDetail")
  const queryClient = useQueryClient()

  const [createDialogOpen, setCreateDialogOpen] = useState(false)
  const [resetDialogOpen, setResetDialogOpen] = useState(false)
  const [snapname, setSnapname] = useState("")
  const [description, setDescription] = useState("")

  const { data: snapshots } = useSuspenseQuery({
    queryKey: ["snapshots", vmid],
    queryFn: () => ResourceDetailsService.listSnapshots({ vmid }),
  })

  const hasInitSnapshot = snapshots.some((s) => s.name === INIT_SNAPSHOT_NAME)

  const resetMutation = useMutation({
    mutationFn: () => ResourceDetailsService.resetToInit({ vmid }),
    onSuccess: () => {
      toast.success(t("snapshots.resetStarted"))
      setResetDialogOpen(false)
    },
    onError: (err: unknown) => {
      toast.error(errorDetail(err) || t("snapshots.resetError"))
    },
  })

  const initSnapshotMutation = useMutation({
    mutationFn: () => ResourceDetailsService.createInitSnapshot({ vmid }),
    onSuccess: () => {
      toast.success(t("snapshots.initCreated"))
      queryClient.invalidateQueries({ queryKey: ["snapshots", vmid] })
    },
    onError: (err: unknown) => {
      toast.error(errorDetail(err) || t("snapshots.initError"))
    },
  })

  const createMutation = useMutation({
    mutationFn: (data: SnapshotCreateRequest) =>
      ResourceDetailsService.createSnapshot({ vmid, requestBody: data }),
    onSuccess: () => {
      toast.success(t("snapshots.createSuccess"))
      setCreateDialogOpen(false)
      setSnapname("")
      setDescription("")
      queryClient.invalidateQueries({ queryKey: ["snapshots", vmid] })
    },
    onError: () => {
      toast.error(t("snapshots.createError"))
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (snapname: string) =>
      ResourceDetailsService.deleteSnapshot({ vmid, snapname }),
    onSuccess: () => {
      toast.success(t("snapshots.deleteSuccess"))
      queryClient.invalidateQueries({ queryKey: ["snapshots", vmid] })
    },
    onError: () => {
      toast.error(t("snapshots.deleteError"))
    },
  })

  const rollbackMutation = useMutation({
    mutationFn: (snapname: string) =>
      ResourceDetailsService.rollbackSnapshot({ vmid, snapname }),
    onSuccess: () => {
      toast.success(t("snapshots.rollbackSuccess"))
      queryClient.invalidateQueries({ queryKey: ["resource", vmid] })
    },
    onError: () => {
      toast.error(t("snapshots.rollbackError"))
    },
  })

  const handleCreate = () => {
    if (!snapname) {
      toast.error(t("snapshots.nameRequired"))
      return
    }

    createMutation.mutate({
      snapname,
      description: description || undefined,
      vmstate: false,
    })
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>{t("snapshots.title")}</CardTitle>
              <CardDescription>{t("snapshots.description")}</CardDescription>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                onClick={() => setResetDialogOpen(true)}
                disabled={!hasInitSnapshot || resetMutation.isPending}
                title={
                  hasInitSnapshot ? undefined : t("snapshots.resetUnavailable")
                }
              >
                <RefreshCcw className="h-4 w-4 mr-1" />
                {t("snapshots.reset")}
              </Button>
              {!hasInitSnapshot && (
                <Button
                  variant="ghost"
                  onClick={() => initSnapshotMutation.mutate()}
                  disabled={initSnapshotMutation.isPending}
                >
                  {t("snapshots.createInit")}
                </Button>
              )}
              <Dialog
                open={createDialogOpen}
                onOpenChange={setCreateDialogOpen}
              >
                <DialogTrigger asChild>
                  <Button>{t("snapshots.create")}</Button>
                </DialogTrigger>
                <DialogContent>
                  <DialogHeader>
                    <DialogTitle>{t("snapshots.createTitle")}</DialogTitle>
                    <DialogDescription>
                      {t("snapshots.createDescription")}
                    </DialogDescription>
                  </DialogHeader>
                  <div className="space-y-4">
                    <div className="space-y-2">
                      <Label htmlFor="snapname">{t("snapshots.name")} *</Label>
                      <Input
                        id="snapname"
                        value={snapname}
                        onChange={(e) => setSnapname(e.target.value)}
                        placeholder="snap-2024-02-25"
                      />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="desc">
                        {t("snapshots.descriptionLabel")}
                      </Label>
                      <Textarea
                        id="desc"
                        value={description}
                        onChange={(e) => setDescription(e.target.value)}
                        placeholder={t("snapshots.descriptionPlaceholder")}
                      />
                    </div>
                  </div>
                  <DialogFooter>
                    <Button
                      variant="outline"
                      onClick={() => setCreateDialogOpen(false)}
                    >
                      {t("common.cancel")}
                    </Button>
                    <Button
                      onClick={handleCreate}
                      disabled={createMutation.isPending}
                    >
                      {t("common.create")}
                    </Button>
                  </DialogFooter>
                </DialogContent>
              </Dialog>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {snapshots.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              {t("snapshots.noSnapshots")}
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("snapshots.name")}</TableHead>
                  <TableHead>{t("snapshots.descriptionLabel")}</TableHead>
                  <TableHead>{t("snapshots.createdAt")}</TableHead>
                  <TableHead className="text-right">
                    {t("common.actions")}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {snapshots.map((snap) => (
                  <TableRow key={snap.name}>
                    <TableCell className="font-medium">
                      {snap.name}
                      {snap.name === INIT_SNAPSHOT_NAME && (
                        <Badge variant="secondary" className="ml-2">
                          <ShieldCheck className="h-3 w-3 mr-1" />
                          {t("snapshots.protected")}
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell>{snap.description || "-"}</TableCell>
                    <TableCell>
                      {snap.snaptime
                        ? new Date(snap.snaptime * 1000).toLocaleString()
                        : "-"}
                    </TableCell>
                    <TableCell className="text-right space-x-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => {
                          if (confirm(t("snapshots.rollbackConfirm"))) {
                            rollbackMutation.mutate(snap.name)
                          }
                        }}
                        disabled={rollbackMutation.isPending}
                      >
                        <RotateCcw className="h-4 w-4 mr-1" />
                        {t("snapshots.rollback")}
                      </Button>
                      {snap.name !== INIT_SNAPSHOT_NAME && (
                        <Button
                          variant="destructive"
                          size="sm"
                          onClick={() => {
                            if (confirm(t("snapshots.deleteConfirm"))) {
                              deleteMutation.mutate(snap.name)
                            }
                          }}
                          disabled={deleteMutation.isPending}
                        >
                          <Trash2 className="h-4 w-4 mr-1" />
                          {t("common.delete")}
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <AlertDialog open={resetDialogOpen} onOpenChange={setResetDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("snapshots.resetConfirmTitle")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("snapshots.resetConfirmBody")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={() => resetMutation.mutate()}>
              {t("snapshots.resetConfirmAction")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
