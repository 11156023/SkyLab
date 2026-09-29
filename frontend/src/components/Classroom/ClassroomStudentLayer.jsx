import {
  Suspense,
  createContext,
  lazy,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useTranslation } from "react-i18next";
import { ClassroomService } from "../../services/classroom";
import { useClassroomSocket } from "../../hooks/useClassroomSocket";
import LiveBanner from "./LiveBanner";

// 觀看對話框帶著 noVNC：掛在每一頁的 layout 上，只有老師開直播、學生點觀看時才載入
const ClassroomWatchDialog = lazy(() => import("./ClassroomWatchDialog"));

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
  const { t } = useTranslation("components");
  const [liveSessionId, setLiveSessionId] = useState(null);
  const [bannerDismissed, setBannerDismissed] = useState(false);
  const [watchOpen, setWatchOpen] = useState(false);
  const [takenOverVmids, setTakenOverVmids] = useState(() => new Set());
  // 事件回呼只綁一次，用 ref 讀目前的直播 id
  const liveSessionIdRef = useRef(null);
  liveSessionIdRef.current = liveSessionId;

  /* resyncTakeover：斷線重連時一併校正「老師接管中」狀態。
     後端若在 /classroom/live 回傳 taken_over_vmids 就以它為準；
     沒有這個欄位或查詢失敗時退回清空，避免覆蓋層卡住到重新整理。 */
  const refreshLive = useCallback(async ({ resyncTakeover = false } = {}) => {
    try {
      const res = await ClassroomService.getLive();
      setLiveSessionId(res.session?.id ?? null);
      if (!res.session) setBannerDismissed(false);
      if (Array.isArray(res.taken_over_vmids)) {
        setTakenOverVmids(new Set(res.taken_over_vmids));
      } else if (resyncTakeover) {
        setTakenOverVmids(new Set());
      }
    } catch {
      setLiveSessionId(null);
      if (resyncTakeover) setTakenOverVmids(new Set());
    }
  }, []);

  const handleEvent = useCallback((event) => {
    switch (event.type) {
      case "live_started":
        setLiveSessionId(event.session_id ?? null);
        setBannerDismissed(false);
        break;
      case "live_stopped":
        /* 同時在兩個班級時可能有兩場直播；別場結束不影響目前這場。
           目前這場結束則重查，讓仍在進行的另一場接上。 */
        if (
          event.session_id != null &&
          liveSessionIdRef.current != null &&
          event.session_id !== liveSessionIdRef.current
        ) {
          break;
        }
        setLiveSessionId(null);
        setWatchOpen(false);
        refreshLive();
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
  }, [refreshLive]);

  const { connected } = useClassroomSocket(handleEvent);

  // 初次掛載時查一次是否已有進行中的直播
  useEffect(() => {
    refreshLive();
  }, [refreshLive]);

  /* 斷線期間推播全部漏掉，畫面會停在斷線那一刻的狀態。
     重新連上（false → true）時重查一次，把直播與接管狀態補回正確值
     （後端重啟會直接結束所有 session，不會補送 takeover_stopped）。 */
  const wasConnectedRef = useRef(false);
  useEffect(() => {
    if (connected && !wasConnectedRef.current) refreshLive({ resyncTakeover: true });
    wasConnectedRef.current = connected;
  }, [connected, refreshLive]);

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
        <Suspense fallback={null}>
          <ClassroomWatchDialog
            key={liveSessionId}
            sessionId={liveSessionId}
            title={t("ClassroomStudentLayer.watchDialogTitle")}
            onClose={() => setWatchOpen(false)}
          />
        </Suspense>
      )}
    </TakeoverContext.Provider>
  );
}
