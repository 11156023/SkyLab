import {
  useMutation,
  useQueryClient,
  useSuspenseQuery,
} from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { Plus, Trash2, Users } from "lucide-react"
import { Suspense, useState } from "react"
import { useForm } from "react-hook-form"
import type { GroupPublic } from "@/client"
import { GroupsService } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { LoadingButton } from "@/components/ui/loading-button"
import { Textarea } from "@/components/ui/textarea"
import { requireGroupManagerUser } from "@/features/auth/guards"
import { groupListQueryOptions } from "@/features/groups/queryOptions"
import useCustomToast from "@/hooks/useCustomToast"
import { queryKeys } from "@/lib/queryKeys"

export const Route = createFileRoute("/_layout/groups")({
  component: GroupsPage,
  beforeLoad: () => requireGroupManagerUser(),
  head: () => ({
    meta: [{ title: "群組管理 - SkyLab" }],
  }),
})

function CreateGroupDialog() {
  const [open, setOpen] = useState(false)
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const { register, handleSubmit, reset } = useForm<{
    name: string
    description: string
  }>()

  const mutation = useMutation({
    mutationFn: (data: { name: string; description: string }) =>
      GroupsService.createGroup({
        requestBody: {
          name: data.name,
          description: data.description || undefined,
        },
      }),
    onSuccess: () => {
      showSuccessToast("群組已建立")
      reset()
      setOpen(false)
      queryClient.invalidateQueries({ queryKey: queryKeys.groups.all })
    },
    onError: () => showErrorToast("建立群組失敗"),
  })

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <Plus className="mr-2 h-4 w-4" />
          建立群組
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>建立新群組</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit((data) => mutation.mutate(data))}>
          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <Label htmlFor="name">群組名稱 *</Label>
              <Input
                id="name"
                {...register("name", { required: true })}
                placeholder="例：2024 Spring CS101"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="description">說明</Label>
              <Textarea
                id="description"
                {...register("description")}
                placeholder="群組說明（選填）"
                rows={3}
              />
            </div>
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline" disabled={mutation.isPending}>
                取消
              </Button>
            </DialogClose>
            <LoadingButton type="submit" loading={mutation.isPending}>
              建立
            </LoadingButton>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function GroupCard({ group }: { group: GroupPublic }) {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const deleteMutation = useMutation({
    mutationFn: () => GroupsService.deleteGroup({ groupId: group.id }),
    onSuccess: () => {
      showSuccessToast("群組已刪除")
      queryClient.invalidateQueries({ queryKey: queryKeys.groups.all })
    },
    onError: () => showErrorToast("刪除失敗"),
  })

  return (
    <div className="glass-panel flex items-center justify-between rounded-lg p-4 transition-all hover:brightness-105 dark:hover:brightness-110">
      <Link
        to="/groups/$groupId"
        params={{ groupId: group.id }}
        className="flex-1"
      >
        <div>
          <div className="font-semibold">{group.name}</div>
          {group.description && (
            <div className="text-sm text-muted-foreground mt-1">
              {group.description}
            </div>
          )}
          <div className="flex items-center gap-1 text-xs text-muted-foreground mt-2">
            <Users className="h-3 w-3" />
            {group.member_count ?? 0} 位成員
          </div>
        </div>
      </Link>
      <Button
        variant="ghost"
        size="icon"
        onClick={() => deleteMutation.mutate()}
        disabled={deleteMutation.isPending}
        className="text-destructive hover:text-destructive"
      >
        <Trash2 className="h-4 w-4" />
      </Button>
    </div>
  )
}

function GroupsContent() {
  const { data } = useSuspenseQuery(groupListQueryOptions())

  if (data.count === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
        <Users className="h-12 w-12 mb-4 opacity-30" />
        <p>尚無群組，點擊「建立群組」開始</p>
      </div>
    )
  }

  return (
    <div className="grid gap-3">
      {data.data.map((group) => (
        <GroupCard key={group.id} group={group} />
      ))}
    </div>
  )
}

function GroupsPage() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">群組管理</h1>
          <p className="text-muted-foreground">
            管理課程/班級群組，批量分配虛擬機
          </p>
        </div>
        <CreateGroupDialog />
      </div>
      <Suspense fallback={<div className="text-muted-foreground">載入中…</div>}>
        <GroupsContent />
      </Suspense>
    </div>
  )
}
