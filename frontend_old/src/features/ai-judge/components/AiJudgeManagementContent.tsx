import { Brain, FileCode2, FileText, PlayCircle } from "lucide-react"
import { useState } from "react"

import type { GroupMemberPublic } from "@/client"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { AiJudgeContent } from "@/features/ai-judge/components/AiJudgeContent"
import { AiJudgeExecutionContent } from "@/features/ai-judge/components/AiJudgeExecutionContent"
import { AiJudgeScriptsContent } from "@/features/ai-judge/components/AiJudgeScriptsContent"

type AiJudgeManagementTab = "rubrics" | "scripts" | "execution"

type RunnableMember = GroupMemberPublic & {
  vm_cpu_usage_pct?: number | null
  vm_ram_usage_pct?: number | null
  vm_disk_usage_pct?: number | null
}

export function AiJudgeManagementContent({
  groupId,
  members,
}: {
  groupId: string
  members: RunnableMember[]
}) {
  const [activeTab, setActiveTab] = useState<AiJudgeManagementTab>("rubrics")

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <Brain className="h-5 w-5 text-blue-500" />
          <h2 className="text-xl font-bold tracking-tight">AI 評分管理</h2>
        </div>
        <p className="text-sm text-muted-foreground">
          管理群組評分表、收集腳本與腳本執行。
        </p>
      </div>

      <Tabs
        value={activeTab}
        onValueChange={(value) => setActiveTab(value as AiJudgeManagementTab)}
        className="space-y-6"
      >
        <TabsList className="grid h-auto w-full grid-cols-3 p-1 md:w-[520px]">
          <TabsTrigger value="rubrics" className="gap-1.5">
            <FileText className="h-4 w-4" />
            評分表
          </TabsTrigger>
          <TabsTrigger value="scripts" className="gap-1.5">
            <FileCode2 className="h-4 w-4" />
            收集腳本
          </TabsTrigger>
          <TabsTrigger value="execution" className="gap-1.5">
            <PlayCircle className="h-4 w-4" />
            腳本執行
          </TabsTrigger>
        </TabsList>

        <TabsContent value="rubrics" className="space-y-6">
          <AiJudgeContent
            groupId={groupId}
            onScriptCreated={() => setActiveTab("scripts")}
          />
        </TabsContent>

        <TabsContent value="scripts" className="space-y-6">
          <AiJudgeScriptsContent
            groupId={groupId}
            onScriptApproved={() => setActiveTab("execution")}
          />
        </TabsContent>

        <TabsContent value="execution" className="space-y-6">
          <AiJudgeExecutionContent groupId={groupId} members={members} />
        </TabsContent>
      </Tabs>
    </div>
  )
}
