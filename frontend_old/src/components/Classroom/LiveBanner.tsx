import { Radio, X } from "lucide-react"

import { Button } from "@/components/ui/button"

interface LiveBannerProps {
  onWatch: () => void
  onDismiss: () => void
}

/** 學生端：老師開始直播時顯示的橫幅，點擊開啟觀看視窗。 */
export function LiveBanner({ onWatch, onDismiss }: LiveBannerProps) {
  return (
    <div className="flex items-center gap-3 border-b border-rose-600/40 bg-rose-50 px-6 py-2.5 dark:border-rose-700/50 dark:bg-rose-900/20">
      <span className="relative flex h-2.5 w-2.5 shrink-0">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-rose-500 opacity-75" />
        <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-rose-600" />
      </span>
      <Radio className="h-4 w-4 shrink-0 text-rose-600 dark:text-rose-400" />
      <span className="text-sm text-rose-800 dark:text-rose-300">
        老師正在直播畫面
      </span>
      <Button
        size="sm"
        onClick={onWatch}
        className="h-7 bg-rose-600 px-3 text-xs text-white hover:bg-rose-700"
      >
        觀看直播
      </Button>
      <button
        type="button"
        onClick={onDismiss}
        className="ml-auto text-rose-600/70 hover:text-rose-700 dark:text-rose-400/70 dark:hover:text-rose-300"
        title="關閉提示"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  )
}
