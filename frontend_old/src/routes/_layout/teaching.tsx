import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { useState } from "react"
import BatchSpecPanel from "@/components/Teaching/BatchSpecPanel"
import ConfigPushPanel from "@/components/Teaching/ConfigPushPanel"
import HeatmapPanel from "@/components/Teaching/HeatmapPanel"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { requireGroupManagerUser } from "@/features/auth/guards"
import { groupListQueryOptions } from "@/features/groups/queryOptions"

export const Route = createFileRoute("/_layout/teaching")({
  component: TeachingPage,
  beforeLoad: () => requireGroupManagerUser(),
  head: () => ({ meta: [{ title: "教學面板 - SkyLab" }] }),
})

function TeachingPage() {
  const [groupId, setGroupId] = useState<string>("")

  const groupsQuery = useQuery(groupListQueryOptions())
  const groups = groupsQuery.data?.data ?? []

  return (
    <div className="container mx-auto p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">教學面板</h1>
        <Select value={groupId} onValueChange={setGroupId}>
          <SelectTrigger className="w-64">
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

      {groupId ? (
        <Tabs defaultValue="heatmap">
          <TabsList>
            <TabsTrigger value="heatmap">學生進度熱圖</TabsTrigger>
            <TabsTrigger value="push">配置文件分發</TabsTrigger>
            <TabsTrigger value="spec">批次調整規格</TabsTrigger>
          </TabsList>
          <TabsContent value="heatmap">
            <HeatmapPanel groupId={groupId} />
          </TabsContent>
          <TabsContent value="push">
            <ConfigPushPanel groupId={groupId} />
          </TabsContent>
          <TabsContent value="spec">
            <BatchSpecPanel groupId={groupId} />
          </TabsContent>
        </Tabs>
      ) : (
        <div className="text-center py-16 text-muted-foreground">
          請先選擇一個群組
        </div>
      )}
    </div>
  )
}
