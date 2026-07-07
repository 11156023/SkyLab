import { useMutation, useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"
import { GroupsService } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import useAuth from "@/hooks/useAuth"
import { PairAPI } from "@/services/teaching"

interface Candidate {
  id: string
  email: string
  full_name: string | null
}

interface Props {
  vmid: number
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: (sessionId: string) => void
}

export default function PairInviteDialog({
  vmid,
  open,
  onOpenChange,
  onCreated,
}: Props) {
  const { user } = useAuth()
  const [inviteeId, setInviteeId] = useState("")

  // 同群組成員：列出我的群組後逐群取成員，去重並排除自己
  const { data: members } = useQuery({
    queryKey: ["pair-invite-candidates"],
    queryFn: async () => {
      const groups = (await GroupsService.listGroups()).data ?? []
      const details = await Promise.all(
        groups.map((g) => GroupsService.getGroup({ groupId: g.id })),
      )
      const seen = new Map<string, Candidate>()
      for (const detail of details) {
        for (const m of detail.members ?? []) {
          seen.set(m.user_id, {
            id: m.user_id,
            email: m.email,
            full_name: m.full_name ?? null,
          })
        }
      }
      if (user?.id) seen.delete(user.id)
      return [...seen.values()]
    },
    enabled: open,
  })

  const createMutation = useMutation({
    mutationFn: () => PairAPI.create({ vmid, invitee_user_id: inviteeId }),
    onSuccess: (data) => {
      toast.success("協作邀請已送出，雙方可進入同一畫面")
      onOpenChange(false)
      onCreated(data.id)
    },
    onError: (err: unknown) => {
      const detail = (err as { body?: { detail?: string } })?.body?.detail
      toast.error(detail || "建立協作失敗")
    },
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>邀請協作（Pair Mode）</DialogTitle>
          <DialogDescription>
            邀請同群組成員與你共同操作這台 VM，雙方皆可輸入。
          </DialogDescription>
        </DialogHeader>
        <Select value={inviteeId} onValueChange={setInviteeId}>
          <SelectTrigger>
            <SelectValue placeholder="選擇同群組成員" />
          </SelectTrigger>
          <SelectContent>
            {(members ?? []).map((m) => (
              <SelectItem key={m.id} value={m.id}>
                {m.full_name || m.email}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            onClick={() => createMutation.mutate()}
            disabled={!inviteeId || createMutation.isPending}
          >
            送出邀請
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
