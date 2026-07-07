import { useQuery } from "@tanstack/react-query"
import type { HeatmapEntry } from "@/client"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { TeachingAPI } from "@/services/teaching"

function cellColor(entry: HeatmapEntry): string {
  if (entry.activity === "stopped") return "bg-gray-300 dark:bg-gray-700"
  if (entry.activity === "stale") return "bg-gray-500"
  if (entry.cpu_percent >= 80) return "bg-red-500"
  if (entry.cpu_percent >= 50) return "bg-orange-400"
  if (entry.cpu_percent >= 10) return "bg-green-500"
  return "bg-green-200"
}

function formatUptime(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return h > 0 ? `${h} 小時 ${m} 分` : `${m} 分`
}

export default function HeatmapPanel({ groupId }: { groupId: string }) {
  const { data: entries } = useQuery({
    queryKey: ["teaching-heatmap", groupId],
    queryFn: () => TeachingAPI.getHeatmap(groupId),
    refetchInterval: 30_000,
  })

  return (
    <Card>
      <CardHeader>
        <CardTitle>學生進度熱圖（30 秒自動更新）</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex gap-3 text-xs text-muted-foreground mb-4">
          <span>■ 灰＝關機</span>
          <span className="text-green-600">■ 綠＝運行</span>
          <span className="text-orange-500">■ 橘/紅＝高 CPU</span>
          <span className="text-gray-500">■ 深灰＝長期無動靜</span>
        </div>
        <TooltipProvider>
          <div className="grid grid-cols-6 md:grid-cols-10 gap-2">
            {(entries ?? []).map((entry) => (
              <Tooltip key={entry.vmid}>
                <TooltipTrigger asChild>
                  <div
                    className={cn(
                      "aspect-square rounded flex items-center justify-center",
                      "text-[10px] text-white font-medium cursor-default",
                      cellColor(entry),
                    )}
                  >
                    {entry.vmid}
                  </div>
                </TooltipTrigger>
                <TooltipContent>
                  <div className="text-xs space-y-0.5">
                    <div>
                      {entry.owner_name ?? "—"}（{entry.name ?? entry.vmid}）
                    </div>
                    <div>狀態：{entry.status}</div>
                    <div>
                      CPU：{entry.cpu_percent}%　RAM：{entry.mem_percent}%
                    </div>
                    <div>開機時長：{formatUptime(entry.uptime_seconds)}</div>
                    {entry.activity === "stale" && <div>⚠ 長期無動靜</div>}
                  </div>
                </TooltipContent>
              </Tooltip>
            ))}
          </div>
        </TooltipProvider>
        {(entries ?? []).length === 0 && (
          <div className="text-center py-8 text-muted-foreground">
            此群組沒有學生 VM
          </div>
        )}
      </CardContent>
    </Card>
  )
}
