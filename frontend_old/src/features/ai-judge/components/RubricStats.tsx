/**
 * RubricStats - Statistics panel for rubric analysis
 */

import { AlertTriangle, CheckCircle, Clock } from "lucide-react"

import { cn } from "@/lib/utils"

type RubricStatsProps = {
  totalItems: number
  autoCount: number
  partialCount: number
  manualCount: number
  className?: string
}

export function RubricStats({
  totalItems,
  autoCount,
  partialCount,
  manualCount,
  className,
}: RubricStatsProps) {
  const total = totalItems || autoCount + partialCount + manualCount

  return (
    <div className={cn("grid grid-cols-2 gap-3 sm:grid-cols-4", className)}>
      <div className="rounded-xl bg-card/50 p-3 text-center">
        <p className="text-2xl font-bold">{total}</p>
        <p className="text-xs text-muted-foreground">共幾題</p>
      </div>
      <div className="rounded-xl bg-green-50 p-3 text-center dark:bg-green-900/20">
        <div className="flex items-center justify-center gap-1">
          <CheckCircle className="h-4 w-4 text-green-600" />
          <span className="text-2xl font-bold text-green-700 dark:text-green-400">
            {autoCount}
          </span>
        </div>
        <p className="text-xs text-green-600 dark:text-green-500">
          可自動偵測 ({total > 0 ? Math.round((autoCount / total) * 100) : 0}%)
        </p>
      </div>
      <div className="rounded-xl bg-yellow-50 p-3 text-center dark:bg-yellow-900/20">
        <div className="flex items-center justify-center gap-1">
          <AlertTriangle className="h-4 w-4 text-yellow-600" />
          <span className="text-2xl font-bold text-yellow-700 dark:text-yellow-400">
            {partialCount}
          </span>
        </div>
        <p className="text-xs text-yellow-600 dark:text-yellow-500">
          部分可偵測 ({total > 0 ? Math.round((partialCount / total) * 100) : 0}
          %)
        </p>
      </div>
      <div className="rounded-xl bg-red-50 p-3 text-center dark:bg-red-900/20">
        <div className="flex items-center justify-center gap-1">
          <Clock className="h-4 w-4 text-red-600" />
          <span className="text-2xl font-bold text-red-700 dark:text-red-400">
            {manualCount}
          </span>
        </div>
        <p className="text-xs text-red-600 dark:text-red-500">
          需人工評閱 ({total > 0 ? Math.round((manualCount / total) * 100) : 0}
          %)
        </p>
      </div>
    </div>
  )
}
