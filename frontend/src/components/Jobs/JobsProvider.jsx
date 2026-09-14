import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../contexts/AuthContext";
import { AuthStorage } from "../../services/auth";
import {
  dismissPrompt as dismissDesktopPrompt,
  getPermission as getDesktopPermission,
  isEnabled as isDesktopPrefEnabled,
  isPageInBackground,
  isPromptDismissed as isDesktopPromptDismissed,
  isSupported as isDesktopSupported,
  requestPermission as requestDesktopPermission,
  setEnabled as setDesktopPref,
  showNotification as showDesktopNotification,
} from "../../services/browserNotifications";
import { CoursesService } from "../../services/courses";
import { connectJobsWebSocket, JobsService } from "../../services/jobs";
import {
  isPushSupported,
  isPushSubscribed,
  sendTestPush,
  subscribePush,
  unsubscribePush,
} from "../../services/webPush";
import JobDetailDialog from "./JobDetailDialog";
import { JOB_KIND_LABEL_KEYS } from "./JobRow";
import { diffJobSnapshot } from "./jobSnapshotDiff";

const NOTIFY_ONLY_MINE_KEY = "jobs:notifyOnlyMine";
const DESKTOP_PROMPT_TOAST_ID = "desktop-notifications-prompt";
// 登入後稍等再問通知權限，避免跟首頁載入的其他 toast 擠在一起
const DESKTOP_PROMPT_DELAY_MS = 2500;

/* 沿用學生首頁提醒中心時代的 key，保留使用者既有的已讀紀錄 */
function reminderStorageKey(user) {
  return `skylab:student-reminders:v1:${user?.id ?? user?.email ?? "student"}`;
}

function loadReadReminderIds(user) {
  try {
    const stored = JSON.parse(window.localStorage.getItem(reminderStorageKey(user)) ?? "[]");
    return Array.isArray(stored) ? stored : [];
  } catch {
    return [];
  }
}

/**
 * 終態任務的通知內容；哪些任務算「剛轉成終態」由 jobSnapshotDiff 決定，
 * 這裡只負責把狀態翻成文案。不認得的狀態回 null。
 */
function describeJobTransition(job, t) {
  const kindLabel = JOB_KIND_LABEL_KEYS[job.kind] ? t(JOB_KIND_LABEL_KEYS[job.kind]) : job.kind;
  const detail = job.message ?? job.title;

  switch (job.status) {
    case "completed":
      return { level: "success", title: t("JobsProvider.jobCompleted", { kindLabel }), description: job.title };
    case "failed":
      return { level: "error", title: t("JobsProvider.jobFailed", { kindLabel }), description: detail };
    case "blocked":
      return { level: "warning", title: t("JobsProvider.jobBlocked", { kindLabel }), description: detail };
    case "cancelled":
      /* info 藍：取消是中性事實通知，用 richColors 家族色才不會像樣式壞掉 */
      return { level: "info", title: t("JobsProvider.jobCancelled", { kindLabel }), description: job.title };
    default:
      return null;
  }
}

/* Service Worker → 頁面的訊息型別（與 public/sw.js 對應） */
const SW_OPEN_JOB_MESSAGE = "skylab:open-job";
const SW_NAVIGATE_MESSAGE = "skylab:navigate";

/** 推播訂閱狀態：unknown（尚未查）| subscribed | unsubscribed | unsupported | disabled | error */
function describePushResult(result) {
  switch (result) {
    case "subscribed":
      return "subscribed";
    case "unsupported":
    case "disabled":
      return result;
    default:
      return "unsubscribed";
  }
}

function notifyJobTransition(job, onView, t, { desktopFallback = true } = {}) {
  const info = describeJobTransition(job, t);
  if (!info) return;

  const action = { label: t("JobsProvider.viewAction"), onClick: () => onView(job.id) };
  const options = { description: info.description, action };
  switch (info.level) {
    case "success":
      toast.success(info.title, options);
      break;
    case "error":
      toast.error(info.title, options);
      break;
    case "warning":
      toast.warning(info.title, options);
      break;
    case "info":
      toast.info(info.title, options);
      break;
    default:
      toast(info.title, options);
  }

  // 桌面（系統）通知只在使用者不在這個分頁／視窗時補一則，
  // 人在頁面上時 toast 已經看得到，避免同一件事跳兩次。
  // 已訂閱 Web Push 時背景通知交給後端推播（Service Worker 顯示），頁面不再自己發。
  if (desktopFallback && isPageInBackground()) {
    showDesktopNotification(info.title, {
      body: info.description,
      tag: job.id,
      onClick: () => onView(job.id),
    });
  }
}

const JobsContext = createContext(null);

/** 任務狀態（執行中清單、通知設定、開詳情），由 JobsProvider 提供 */
export function useJobs() {
  return useContext(JobsContext);
}

/**
 * 全站任務狀態來源（無 UI）：
 * - /ws/jobs WebSocket 即時推送為主，REST 每 15 秒輪詢為 fallback
 * - 任務進入終態（完成／失敗／受阻／取消）時彈 toast，不論當前頁面
 * - 桌面（系統）通知：登入後以 toast 詢問一次瀏覽器通知權限；開啟後，
 *   使用者不在本分頁／視窗時，任務終態與新提醒會再發一則系統通知
 * - 掛載共用的 JobDetailDialog；顯示用的按鈕（JobsButton）放在 Sidebar 底部
 */
export default function JobsProvider({ children }) {
  const { t } = useTranslation("components");
  const { user } = useAuth();
  const navigate = useNavigate();
  const [items, setItems] = useState(null); // 執行中任務；null = 尚未載入
  const [focusJobId, setFocusJobId] = useState(null);
  const [notifyOnlyMine, setNotifyOnlyMineState] = useState(
    () => localStorage.getItem(NOTIFY_ONLY_MINE_KEY) === "1",
  );
  const [reminders, setReminders] = useState(null); // 提醒；null = 尚未載入
  const [readReminderIds, setReadReminderIds] = useState(() => loadReadReminderIds(user));

  const isAdmin = Boolean(user?.is_superuser || user?.role === "admin");
  const myUserId = user?.id ?? null;
  // 使用 ref 送進 WS callback，避免 closure 抓舊設定導致 effect 重連
  const filterRef = useRef({ enabled: false, myUserId: null });
  filterRef.current = { enabled: notifyOnlyMine && isAdmin, myUserId };
  // 上一次 WS snapshot 的基準（各 job 狀態 + 時間高水位），用於 diff 觸發通知
  const snapshotBaselineRef = useRef(null);
  // 上一次 WS snapshot 中的提醒 id，用於偵測「新出現」的提醒發桌面通知
  const prevReminderIdsRef = useRef(null);

  /* 桌面（系統）通知：瀏覽器權限狀態 + 使用者偏好 */
  const [desktopPermission, setDesktopPermission] = useState(() => getDesktopPermission());
  const [desktopPrefEnabled, setDesktopPrefEnabled] = useState(() => isDesktopPrefEnabled());
  // WS callback 以 [] 依賴掛載，點擊提醒通知的處理要走 ref 才拿得到最新的 user／navigate
  const reminderClickRef = useRef(() => {});
  const readReminderIdsRef = useRef(readReminderIds);
  readReminderIdsRef.current = readReminderIds;

  /* Web Push（分頁關掉也能收到）：訂閱狀態；WS callback 用 ref 讀，決定要不要自己發背景通知 */
  const [pushState, setPushState] = useState(() => (isPushSupported() ? "unknown" : "unsupported"));
  const pushSubscribedRef = useRef(false);
  pushSubscribedRef.current = pushState === "subscribed";
  const [searchParams, setSearchParams] = useSearchParams();

  /* REST fallback：每 15 秒抓一次執行中任務（WS 為主） */
  const load = useCallback(async () => {
    try {
      const res = await JobsService.list({ statuses: ["running"], limit: 200, historyDays: 30 });
      setItems(res?.items ?? []);
    } catch {
      // 靜默失敗，維持現有畫面；WS 重連後會補上
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(() => {
      if (!document.hidden) load();
    }, 15000);
    return () => clearInterval(timer);
  }, [load]);

  /* WebSocket 即時推送。token 用函式取：access token 過期後 refresh 會換新值，
     重連時要拿當下的，否則後端 1008 拒絕、前端每 5 秒重連卻永遠連不上。 */
  useEffect(() => {
    if (!AuthStorage.getAccessToken()) return;
    return connectJobsWebSocket(() => AuthStorage.getAccessToken(), (snapshot) => {
      const all = snapshot?.items ?? [];
      setItems(all.filter((j) => j.status === "running"));

      // /ws/jobs 的 snapshot 會附帶個人提醒（約每 30 秒重算一次）；
      // 缺欄位（舊後端）時維持 REST 載入的結果
      if (Array.isArray(snapshot?.reminders)) {
        setReminders(snapshot.reminders);

        // ── 新出現且未讀的提醒 → 桌面通知（首次 snapshot 只建 baseline）──
        const prevIds = prevReminderIdsRef.current;
        if (prevIds !== null && !pushSubscribedRef.current && isPageInBackground()) {
          for (const reminder of snapshot.reminders) {
            if (prevIds.has(reminder.id)) continue;
            if (readReminderIdsRef.current.includes(reminder.id)) continue;
            showDesktopNotification(reminder.title, {
              body: reminder.description,
              tag: reminder.id,
              onClick: () => reminderClickRef.current(reminder),
            });
          }
        }
        prevReminderIdsRef.current = new Set(snapshot.reminders.map((r) => r.id));
      }

      // ── Diff: 比對上一次 snapshot，找出剛轉成終態的任務 ──
      // 首次連線只建立基準；之後除了狀態變化，也涵蓋「兩次推送之間
      // 就建立並完成」的任務（首次看到即終態、時間晚於高水位），
      // 否則範本轉換／刪除這類幾秒內結束的任務永遠不會通知。
      const { transitions, baseline } = diffJobSnapshot(all, snapshotBaselineRef.current);
      snapshotBaselineRef.current = baseline;
      const { enabled, myUserId } = filterRef.current;
      for (const j of transitions) {
        // admin 開「只通知自己」：跳過非本人的 job
        if (enabled && j.user_id !== myUserId) continue;
        notifyJobTransition(j, setFocusJobId, t, { desktopFallback: !pushSubscribedRef.current });
      }
    });
  }, []);

  /* 由推播通知點進來：Service Worker 聚焦既有分頁後送訊息，或開新視窗帶 ?job= */
  useEffect(() => {
    const jobId = searchParams.get("job");
    if (!jobId) return;
    setFocusJobId(jobId);
    const next = new URLSearchParams(searchParams);
    next.delete("job");
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  useEffect(() => {
    if (typeof navigator === "undefined" || !navigator.serviceWorker) return undefined;
    const onMessage = (event) => {
      const data = event.data;
      if (!data || typeof data !== "object") return;
      if (data.type === SW_OPEN_JOB_MESSAGE && data.jobId) {
        setFocusJobId(data.jobId);
      } else if (data.type === SW_NAVIGATE_MESSAGE && typeof data.url === "string") {
        // 推播帶的是站內相對路徑；防禦性地去掉 origin 避免整頁重載
        try {
          const url = new URL(data.url, window.location.origin);
          if (url.origin === window.location.origin) navigate(url.pathname + url.search + url.hash);
        } catch {
          // 非法 URL 直接忽略
        }
      }
    };
    navigator.serviceWorker.addEventListener("message", onMessage);
    return () => navigator.serviceWorker.removeEventListener("message", onMessage);
  }, [navigate]);

  /**
   * 推播訂閱與後端對齊：權限已授予且偏好開啟時，登入後（或換帳號後）重新訂閱一次。
   * subscribePush 會沿用瀏覽器既有訂閱、只向後端 upsert，所以重複呼叫沒有副作用；
   * 這也讓同一台瀏覽器換帳號登入時，endpoint 歸屬跟著換到新使用者。
   */
  useEffect(() => {
    if (!isPushSupported()) return undefined;
    let cancelled = false;
    (async () => {
      if (getDesktopPermission() !== "granted" || !isDesktopPrefEnabled()) {
        const subscribed = await isPushSubscribed();
        if (!cancelled) setPushState(subscribed ? "subscribed" : "unsubscribed");
        return;
      }
      const result = await subscribePush();
      if (!cancelled) setPushState(describePushResult(result));
    })();
    return () => {
      cancelled = true;
    };
  }, [user?.id]);

  /* 提醒（機器期限、審核結果、近期課堂任務）：登入後載入一次，popover 開啟時再刷新 */
  const refreshReminders = useCallback(async () => {
    try {
      const res = await CoursesService.listReminders();
      setReminders(Array.isArray(res) ? res : []);
    } catch {
      // 靜默失敗：沒有提醒權限或暫時取不到時，維持現有清單
      setReminders((current) => current ?? []);
    }
  }, []);

  /* 依身分（而非 user 物件引用）重載：token refresh 會換新 user 物件，不該重打 API */
  useEffect(() => {
    refreshReminders();
    setReadReminderIds(loadReadReminderIds(user));
  }, [refreshReminders, user?.id]);

  const persistReadReminderIds = useCallback((ids) => {
    setReadReminderIds(ids);
    try {
      window.localStorage.setItem(reminderStorageKey(user), JSON.stringify(ids));
    } catch {
      // localStorage 不可用時，已讀狀態僅本次瀏覽生效
    }
  }, [user]);

  const markReminderRead = useCallback((id) => {
    setReadReminderIds((current) => {
      if (current.includes(id)) return current;
      const next = [...current, id];
      try {
        window.localStorage.setItem(reminderStorageKey(user), JSON.stringify(next));
      } catch {
        // 同上，靜默降級
      }
      return next;
    });
  }, [user]);

  const markAllRemindersRead = useCallback(() => {
    persistReadReminderIds((reminders ?? []).map((item) => item.id));
  }, [persistReadReminderIds, reminders]);

  const setNotifyOnlyMine = useCallback((next) => {
    setNotifyOnlyMineState(next);
    try {
      localStorage.setItem(NOTIFY_ONLY_MINE_KEY, next ? "1" : "0");
    } catch {
      // localStorage 不可用時該設定僅本次瀏覽生效
    }
  }, []);

  /* 點擊提醒的桌面通知：標已讀並跳到目標頁 */
  reminderClickRef.current = (reminder) => {
    markReminderRead(reminder.id);
    if (reminder.target) navigate(reminder.target);
  };

  /* 使用者可能在瀏覽器的網站設定改過權限：popover 開啟時重新讀一次 */
  const syncDesktopPermission = useCallback(() => {
    setDesktopPermission(getDesktopPermission());
  }, []);

  /**
   * 開啟桌面通知：向瀏覽器要權限（必須由使用者點擊觸發）。
   * 拿到權限就立刻發一則示範通知，讓使用者確認系統通知真的會出現。
   */
  const enableDesktopNotifications = useCallback(async () => {
    const result = await requestDesktopPermission();
    setDesktopPermission(getDesktopPermission());
    if (result === "granted") {
      setDesktopPref(true);
      setDesktopPrefEnabled(true);
      toast.dismiss(DESKTOP_PROMPT_TOAST_ID);
      // 有推播就走整條鏈（後端 → 推播服務 → Service Worker）發示範通知，
      // 順便驗證分頁關掉後也收得到；沒有推播才由頁面自己發
      const pushResult = isPushSupported() ? await subscribePush() : "unsupported";
      setPushState(describePushResult(pushResult));
      let demoSent = false;
      if (pushResult === "subscribed") {
        try {
          await sendTestPush();
          demoSent = true;
        } catch {
          // 後端送不出去時退回頁面自己發
        }
      }
      if (!demoSent) {
        showDesktopNotification(t("JobsProvider.desktopEnabledTitle"), {
          body: t("JobsProvider.desktopEnabledBody"),
          tag: "skylab-desktop-notifications-enabled",
        });
      }
      toast.success(t("JobsProvider.desktopEnabledTitle"));
    } else if (result === "denied") {
      toast.dismiss(DESKTOP_PROMPT_TOAST_ID);
      toast.error(t("JobsProvider.desktopDenied"));
    } else if (result === "insecure") {
      toast.dismiss(DESKTOP_PROMPT_TOAST_ID);
      toast.error(t("JobsProvider.desktopInsecure"));
    }
    return result;
  }, [t]);

  const disableDesktopNotifications = useCallback(() => {
    setDesktopPref(false);
    setDesktopPrefEnabled(false);
    if (isPushSupported()) {
      unsubscribePush().then(() => setPushState("unsubscribed"));
    }
  }, []);

  /* 登入後詢問一次通知權限：只在瀏覽器還沒決定、且使用者沒按過「稍後再說」時 */
  useEffect(() => {
    if (!isDesktopSupported()) return undefined;
    if (getDesktopPermission() !== "default") return undefined;
    if (!isDesktopPrefEnabled() || isDesktopPromptDismissed()) return undefined;

    const timer = setTimeout(() => {
      toast(t("JobsProvider.desktopPromptTitle"), {
        id: DESKTOP_PROMPT_TOAST_ID,
        description: t("JobsProvider.desktopPromptDescription"),
        duration: Infinity,
        action: {
          label: t("JobsProvider.desktopPromptAllow"),
          onClick: () => {
            enableDesktopNotifications();
          },
        },
        cancel: {
          label: t("JobsProvider.desktopPromptLater"),
          onClick: () => dismissDesktopPrompt(),
        },
      });
    }, DESKTOP_PROMPT_DELAY_MS);
    return () => clearTimeout(timer);
  }, [enableDesktopNotifications, t]);

  const desktopNotifications = {
    supported: isDesktopSupported(),
    permission: desktopPermission,
    enabled: desktopPermission === "granted" && desktopPrefEnabled,
    enable: enableDesktopNotifications,
    disable: disableDesktopNotifications,
    sync: syncDesktopPermission,
    // Web Push 狀態：subscribed 代表分頁關掉也收得到
    push: pushState,
  };

  return (
    <JobsContext.Provider
      value={{
        items,
        isAdmin,
        notifyOnlyMine,
        setNotifyOnlyMine,
        openJob: setFocusJobId,
        reminders,
        readReminderIds,
        refreshReminders,
        markReminderRead,
        markAllRemindersRead,
        desktopNotifications,
      }}
    >
      {children}
      <JobDetailDialog jobId={focusJobId} onClose={() => setFocusJobId(null)} />
    </JobsContext.Provider>
  );
}
