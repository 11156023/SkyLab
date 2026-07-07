import { Hand } from "lucide-react"

/** 學生自己的主控台頁：老師接管中時覆蓋顯示，提示輸入已被鎖定。 */
export function TakeoverOverlay() {
  return (
    <div className="pointer-events-none absolute inset-0 z-30 flex flex-col items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="flex flex-col items-center gap-3 rounded-xl border border-amber-500/40 bg-zinc-900/90 px-8 py-6 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-amber-500/20">
          <Hand className="h-6 w-6 text-amber-400" />
        </div>
        <p className="text-base font-medium text-white">老師正在接管此畫面</p>
        <p className="text-sm text-zinc-400">你的鍵盤與滑鼠操作暫時停用</p>
      </div>
    </div>
  )
}
