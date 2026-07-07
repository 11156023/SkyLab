import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Users } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import useAuth from "@/hooks/useAuth"
import { PairAPI } from "@/services/teaching"

export default function PairInvitesCard({
  onJoin,
}: {
  onJoin: (sessionId: string) => void
}) {
  const { user } = useAuth()
  const queryClient = useQueryClient()

  const { data: sessions } = useQuery({
    queryKey: ["pair-sessions-mine"],
    queryFn: () => PairAPI.mine(),
    refetchInterval: 15_000,
  })

  const endMutation = useMutation({
    mutationFn: (sessionId: string) => PairAPI.end(sessionId),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["pair-sessions-mine"] }),
  })

  if (!sessions?.length) return null

  return (
    <Card className="border-primary/40">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Users className="h-4 w-4" />
          協作邀請
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {sessions.map((s) => {
          const isOwner = s.owner_id === user?.id
          return (
            <div
              key={s.id}
              className="flex items-center justify-between rounded border p-3"
            >
              <div className="text-sm">
                {isOwner
                  ? `你邀請 ${s.invitee_name ?? "成員"} 協作 VM ${s.vmid}`
                  : `${s.owner_name ?? "成員"} 邀請你協作 VM ${s.vmid}`}
              </div>
              <div className="space-x-2">
                <Button size="sm" onClick={() => onJoin(s.id)}>
                  加入
                </Button>
                {isOwner && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => endMutation.mutate(s.id)}
                  >
                    結束
                  </Button>
                )}
              </div>
            </div>
          )
        })}
      </CardContent>
    </Card>
  )
}
