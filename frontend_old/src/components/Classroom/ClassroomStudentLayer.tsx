import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react"

import { useClassroomSocket } from "@/hooks/useClassroomSocket"
import { ClassroomAPI } from "@/services/classroom"
import { ClassroomWatchDialog } from "./ClassroomWatchDialog"
import { LiveBanner } from "./LiveBanner"

interface TakeoverContextValue {
  /** 目前被老師接管中的 vmid 集合 */
  takenOverVmids: Set<number>
}

const TakeoverContext = createContext<TakeoverContextValue>({
  takenOverVmids: new Set(),
})

export function useClassroomTakeover(vmid: number | null | undefined): boolean {
  const { takenOverVmids } = useContext(TakeoverContext)
  return vmid != null && takenOverVmids.has(vmid)
}

/**
 * 學生端全域層：常駐信令連線，處理直播橫幅 / 觀看視窗與「老師接管中」覆蓋。
 * 掛在 AppLayout，對所有登入者生效（非群組成員自然收不到事件）。
 */
export function ClassroomStudentLayer({ children }: { children: ReactNode }) {
  const [liveSessionId, setLiveSessionId] = useState<string | null>(null)
  const [bannerDismissed, setBannerDismissed] = useState(false)
  const [watchOpen, setWatchOpen] = useState(false)
  const [takenOverVmids, setTakenOverVmids] = useState<Set<number>>(
    () => new Set(),
  )

  const refreshLive = useCallback(async () => {
    try {
      const res = await ClassroomAPI.getLive()
      setLiveSessionId(res.session?.id ?? null)
      if (!res.session) {
        setBannerDismissed(false)
      }
    } catch {
      setLiveSessionId(null)
    }
  }, [])

  const handleEvent = useCallback(
    (event: { type: string; session_id?: string; vmid?: number }) => {
      switch (event.type) {
        case "live_started":
          setLiveSessionId(event.session_id ?? null)
          setBannerDismissed(false)
          break
        case "live_stopped":
          setLiveSessionId(null)
          setWatchOpen(false)
          break
        case "takeover_started":
          if (event.vmid != null) {
            setTakenOverVmids((prev) => new Set(prev).add(event.vmid!))
          }
          break
        case "takeover_stopped":
          if (event.vmid != null) {
            setTakenOverVmids((prev) => {
              const next = new Set(prev)
              next.delete(event.vmid!)
              return next
            })
          }
          break
        case "watch_force_closed":
          setWatchOpen(false)
          break
      }
    },
    [],
  )

  useClassroomSocket(handleEvent)

  // 初次掛載時查一次是否已有進行中的直播
  useEffect(() => {
    void refreshLive()
  }, [refreshLive])

  const showBanner = liveSessionId !== null && !bannerDismissed

  const ctx = useMemo(() => ({ takenOverVmids }), [takenOverVmids])

  return (
    <TakeoverContext.Provider value={ctx}>
      {showBanner && (
        <LiveBanner
          onWatch={() => setWatchOpen(true)}
          onDismiss={() => setBannerDismissed(true)}
        />
      )}
      {children}
      <ClassroomWatchDialog
        sessionId={liveSessionId}
        title="老師直播"
        open={watchOpen && liveSessionId !== null}
        onOpenChange={setWatchOpen}
      />
    </TakeoverContext.Provider>
  )
}
