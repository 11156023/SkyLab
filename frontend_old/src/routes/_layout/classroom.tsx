import { useMutation, useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { Monitor, Radio, Users, Wifi, WifiOff } from "lucide-react"
import { useMemo, useState } from "react"

import type { ClassroomStudent } from "@/client"
import { ClassroomWatchDialog } from "@/components/Classroom/ClassroomWatchDialog"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { requireGroupManagerUser } from "@/features/auth/guards"
import { groupListQueryOptions } from "@/features/groups/queryOptions"
import useCustomToast from "@/hooks/useCustomToast"
import { cn } from "@/lib/utils"
import { ClassroomAPI } from "@/services/classroom"
import { handleError } from "@/utils"

export const Route = createFileRoute("/_layout/classroom")({
  component: ClassroomPage,
  beforeLoad: () => requireGroupManagerUser(),
  head: () => ({ meta: [{ title: "虛擬教室 - SkyLab" }] }),
})

function ClassroomPage() {
  const { showErrorToast, showSuccessToast } = useCustomToast()
  const [groupId, setGroupId] = useState<string | null>(null)
  const [watch, setWatch] = useState<{
    sessionId: string
    title: string
    canControl: boolean
  } | null>(null)

  const groupsQuery = useQuery(groupListQueryOptions())
  const groups = groupsQuery.data?.data ?? []

  const effectiveGroupId = groupId ?? groups[0]?.id ?? null

  const studentsQuery = useQuery({
    queryKey: ["classroom", "students", effectiveGroupId],
    queryFn: () => ClassroomAPI.listStudents(effectiveGroupId!),
    enabled: Boolean(effectiveGroupId),
    refetchInterval: 10000,
  })
  const students = studentsQuery.data ?? []

  // 自己名下、可作為直播源的 VM（用群組資料中屬於當前使用者的卡片不適用；
  // 直播源用學生卡片以外的獨立清單較複雜，這裡從所有學生 VM 過濾出 running 供觀看，
  // 直播源改用第一個 running VM 的簡化：實務上老師自己的 VM 也會出現在群組）
  const broadcastCandidates = useMemo(
    () =>
      students.flatMap((s) =>
        (s.vms ?? []).filter((v) => v.status === "running"),
      ),
    [students],
  )

  const watchMutation = useMutation({
    mutationFn: (vmid: number) =>
      ClassroomAPI.createSession({ vmid, mode: "monitor" }),
    onSuccess: (session, vmid) => {
      setWatch({
        sessionId: session.id,
        title: `觀看 VM ${vmid}`,
        canControl: true,
      })
    },
    onError: handleError.bind(showErrorToast),
  })

  const broadcastMutation = useMutation({
    mutationFn: (vmid: number) =>
      ClassroomAPI.createSession({
        vmid,
        mode: "broadcast",
        group_id: effectiveGroupId!,
      }),
    onSuccess: () => showSuccessToast("已開始直播"),
    onError: handleError.bind(showErrorToast),
  })

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10">
            <Monitor className="h-5 w-5 text-primary" />
          </div>
          <div>
            <h1 className="text-xl font-semibold">虛擬教室</h1>
            <p className="text-sm text-muted-foreground">
              即時觀看學生畫面、直播示範給全班
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Select
            value={effectiveGroupId ?? undefined}
            onValueChange={(v) => setGroupId(v)}
          >
            <SelectTrigger className="w-56">
              <SelectValue placeholder="選擇群組" />
            </SelectTrigger>
            <SelectContent>
              {groups.map((g) => (
                <SelectItem key={g.id} value={g.id}>
                  {g.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {broadcastCandidates.length > 0 && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-rose-200 bg-rose-50/60 px-4 py-3 dark:border-rose-900/50 dark:bg-rose-950/20">
          <Radio className="h-4 w-4 text-rose-600 dark:text-rose-400" />
          <span className="text-sm font-medium">直播示範</span>
          <Select onValueChange={(v) => broadcastMutation.mutate(Number(v))}>
            <SelectTrigger className="w-56">
              <SelectValue placeholder="選擇要直播的 VM" />
            </SelectTrigger>
            <SelectContent>
              {broadcastCandidates.map((v) => (
                <SelectItem key={v.vmid} value={String(v.vmid)}>
                  {v.name || `VM ${v.vmid}`} ({v.vmid})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <span className="text-xs text-muted-foreground">
            直播為唯讀，全班可觀看你的畫面
          </span>
        </div>
      )}

      {studentsQuery.isLoading ? (
        <p className="text-sm text-muted-foreground">載入中…</p>
      ) : students.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed py-16 text-center">
          <Users className="h-8 w-8 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">此群組沒有學生</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {students.map((student) => (
            <StudentCard
              key={student.user_id}
              student={student}
              onWatch={(vmid) => watchMutation.mutate(vmid)}
              watching={watchMutation.isPending}
            />
          ))}
        </div>
      )}

      <ClassroomWatchDialog
        sessionId={watch?.sessionId ?? null}
        title={watch?.title}
        canControl={watch?.canControl}
        open={watch !== null}
        onOpenChange={(open) => {
          if (!open && watch) {
            ClassroomAPI.stopSession(watch.sessionId).catch(() => {})
            setWatch(null)
          }
        }}
      />
    </div>
  )
}

function StudentCard({
  student,
  onWatch,
  watching,
}: {
  student: ClassroomStudent
  onWatch: (vmid: number) => void
  watching: boolean
}) {
  const qemuVms = (student.vms ?? []).filter((v) => v.vm_type !== "lxc")
  const primaryVm = qemuVms.find((v) => v.status === "running") ?? qemuVms[0]

  return (
    <div className="flex flex-col gap-3 rounded-xl border bg-card p-4 shadow-sm">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">
            {student.full_name || student.email}
          </p>
          <p className="truncate text-xs text-muted-foreground">
            {student.email}
          </p>
        </div>
        <span
          className={cn(
            "inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium",
            student.online
              ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
              : "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400",
          )}
        >
          {student.online ? (
            <Wifi className="h-3 w-3" />
          ) : (
            <WifiOff className="h-3 w-3" />
          )}
          {student.online ? "在線" : "離線"}
        </span>
      </div>

      <div className="flex items-center justify-between">
        {primaryVm ? (
          <span className="flex items-center gap-1.5 text-xs">
            <span
              className={cn(
                "h-2 w-2 rounded-full",
                primaryVm.status === "running"
                  ? "bg-emerald-500"
                  : "bg-zinc-400",
              )}
            />
            {primaryVm.name || `VM ${primaryVm.vmid}`}
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">無 VM</span>
        )}

        <Button
          size="sm"
          variant="outline"
          disabled={primaryVm?.status !== "running" || watching}
          onClick={() => primaryVm && onWatch(primaryVm.vmid)}
          className="h-7 px-2.5 text-xs"
        >
          <Monitor className="mr-1 h-3.5 w-3.5" />
          觀看
        </Button>
      </div>
    </div>
  )
}
