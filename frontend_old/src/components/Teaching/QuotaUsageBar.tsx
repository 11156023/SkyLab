import { useQuery } from "@tanstack/react-query"
import { Card, CardContent } from "@/components/ui/card"
import { cn } from "@/lib/utils"
import { QuotaAPI } from "@/services/quotas"

function Meter({
  label,
  used,
  max,
  unit,
}: {
  label: string
  used: number
  max: number
  unit: string
}) {
  const pct = max > 0 ? Math.min(100, (used / max) * 100) : 0
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className={pct >= 90 ? "text-red-600 font-medium" : ""}>
          {used} / {max} {unit}
        </span>
      </div>
      <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
        <div
          className={cn(
            "h-full rounded-full transition-all",
            pct >= 90
              ? "bg-red-500"
              : pct >= 70
                ? "bg-orange-400"
                : "bg-primary",
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

export default function QuotaUsageBar() {
  const { data } = useQuery({
    queryKey: ["my-quota-usage"],
    queryFn: () => QuotaAPI.myUsage(),
    staleTime: 60_000,
  })

  if (!data) return null

  return (
    <Card>
      <CardContent className="grid grid-cols-2 md:grid-cols-4 gap-4 pt-6">
        <Meter
          label="CPU"
          used={data.used_cpu_cores}
          max={data.quota.max_cpu_cores}
          unit="cores"
        />
        <Meter
          label="記憶體"
          used={Math.round(data.used_memory_mb / 1024)}
          max={Math.round(data.quota.max_memory_mb / 1024)}
          unit="GB"
        />
        <Meter
          label="磁碟"
          used={data.used_disk_gb}
          max={data.quota.max_disk_gb}
          unit="GB"
        />
        <Meter
          label="實例"
          used={data.used_instances}
          max={data.quota.max_instances}
          unit="台"
        />
      </CardContent>
    </Card>
  )
}
