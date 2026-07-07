import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { ClassroomService } from "../../services/classroom";
import { useClassroomSocket } from "../../hooks/useClassroomSocket";
import ClassroomWatchDialog from "./ClassroomWatchDialog";
import LiveBanner from "./LiveBanner";

const TakeoverContext = createContext({ takenOverVmids: new Set() });

/** 查詢某台 VM 是否正被老師接管（供 VncDialog 顯示覆蓋層） */
export function useClassroomTakeover(vmid) {
  const { takenOverVmids } = useContext(TakeoverContext);
  return vmid != null && takenOverVmids.has(vmid);
}

/**
 * 學生端全域層：常駐信令連線，處理直播橫幅 / 觀看視窗與「老師接管中」狀態。
 * 掛在 DashboardLayout，對所有登入者生效（非群組成員自然收不到事件）。
 */
export default function ClassroomStudentLayer({ children }) {
  const [liveSessionId, setLiveSessionId] = useState(null);
  const [bannerDismissed, setBannerDismissed] = useState(false);
  const [watchOpen, setWatchOpen] = useState(false);
  const [takenOverVmids, setTakenOverVmids] = useState(() => new Set());

  const refreshLive = useCallback(async () => {
    try {
      const res = await ClassroomService.getLive();
      setLiveSessionId(res.session?.id ?? null);
      if (!res.session) setBannerDismissed(false);
    } catch {
      setLiveSessionId(null);
    }
  }, []);

  const handleEvent = useCallback((event) => {
    switch (event.type) {
      case "live_started":
        setLiveSessionId(event.session_id ?? null);
        setBannerDismissed(false);
        break;
      case "live_stopped":
        setLiveSessionId(null);
        setWatchOpen(false);
        break;
      case "takeover_started":
        if (event.vmid != null) {
          setTakenOverVmids((prev) => new Set(prev).add(event.vmid));
        }
        break;
      case "takeover_stopped":
        if (event.vmid != null) {
          setTakenOverVmids((prev) => {
            const next = new Set(prev);
            next.delete(event.vmid);
            return next;
          });
        }
        break;
      case "watch_force_closed":
        setWatchOpen(false);
        break;
      default:
        break;
    }
  }, []);

  useClassroomSocket(handleEvent);

  // 初次掛載時查一次是否已有進行中的直播
  useEffect(() => {
    refreshLive();
  }, [refreshLive]);

  const showBanner = liveSessionId !== null && !bannerDismissed;
  const ctx = useMemo(() => ({ takenOverVmids }), [takenOverVmids]);

  return (
    <TakeoverContext.Provider value={ctx}>
      {showBanner && (
        <LiveBanner
          onWatch={() => setWatchOpen(true)}
          onDismiss={() => setBannerDismissed(true)}
        />
      )}
      {children}
      {watchOpen && liveSessionId !== null && (
        <ClassroomWatchDialog
          sessionId={liveSessionId}
          title="老師直播"
          onClose={() => setWatchOpen(false)}
        />
      )}
    </TakeoverContext.Provider>
  );
}
