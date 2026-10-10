import { describeError, NOT_LOGGED_IN, toastError } from "@/utils/errors";
import { on, onListener, send } from "@/utils/ipcUtils";
import { ElMessage } from "element-plus";
import { defineStore } from "pinia";
import { ipcRouters, listeners } from "../../electron/core/IpcRouter";
import router from "../router";

interface AppState {
  loggedIn: boolean;
  loginInProgress: boolean;
  language: string;
  autoStart: boolean;
  backendUrl: string;
  /** 設定存檔進行中（換後端網址時含先登出），設定頁據此停用儲存鈕 */
  settingsSaving: boolean;
  resourcesLoading: boolean;
  /** 載入資源失敗的錯誤碼（畫面用 describeError 翻譯），空字串＝沒有錯誤 */
  resourcesErrorCode: string;
  updateInfo: SkyLabUpdateInfo | null;
  updateChecking: boolean;
  updateCheckError: boolean;
  updateCheckedAt: number;
  updateInstalling: boolean;
  updateProgress: SkyLabUpdateProgress | null;
  /** 安裝更新失敗的錯誤碼，空字串＝沒有錯誤 */
  updateInstallErrorCode: string;
  tunnelStatus: TunnelStatusInfo;
  resources: SkyLabResource[];
  quickPracticeSessions: SkyLabQuickPracticeSession[];
  sessionStatuses: SkyLabSessionStatus[];
  /** vmids the user already snoozed so we don't re-pop while still warning. */
  dismissedWarnings: number[];
  /** vmid → warning key (auto_stop_at or expiry_at ISO string); persisted in localStorage. */
  permanentDismissals: Record<number, string>;
}

const DEFAULT_TUNNEL_STATUS: TunnelStatusInfo = {
  running: false,
  lastStartTime: -1,
  connectionError: null,
  tunnels: []
};

/** Poll cadence for the session-status warning system; matches the web hook
 * (which is itself anchored to the backend's 30 min ``practice_warning_minutes``). */
const SESSION_POLL_INTERVAL_MS = 30_000;
const RESOURCE_REFRESH_INTERVAL_MS = 60_000;
const UPDATE_POLL_INTERVAL_MS = 60 * 60_000;
const LS_KEY = "session_warning_dismissed";
let sessionPollTimer: ReturnType<typeof setInterval> | null = null;
let updatePollTimer: ReturnType<typeof setInterval> | null = null;
let authExpiryListenerRegistered = false;
let lastResourceRefreshAt = 0;
/** 等登出回覆後才送出的設定（換後端網址時，見 saveSettings） */
let pendingSettingsPatch: SkyLabSettingsPatch | null = null;

function loadPermanentDismissals(): Record<number, string> {
  try {
    return JSON.parse(localStorage.getItem(LS_KEY) ?? "{}") as Record<
      number,
      string
    >;
  } catch {
    return {};
  }
}

function savePermanentDismissals(store: Record<number, string>) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(store));
  } catch {
    // Keep warning processing alive even when localStorage is unavailable.
  }
}

function warningKey(status: SkyLabSessionStatus): string {
  return status.auto_stop_at ?? status.expiry_at ?? "";
}

export const useAppStore = defineStore("app", {
  state: (): AppState => ({
    loggedIn: false,
    loginInProgress: false,
    language: "zh-TW",
    autoStart: false,
    backendUrl: "https://skylab-tw.com",
    settingsSaving: false,
    resourcesLoading: false,
    resourcesErrorCode: "",
    updateInfo: null,
    updateChecking: false,
    updateCheckError: false,
    updateCheckedAt: 0,
    updateInstalling: false,
    updateProgress: null,
    updateInstallErrorCode: "",
    tunnelStatus: { ...DEFAULT_TUNNEL_STATUS },
    resources: [],
    quickPracticeSessions: [],
    sessionStatuses: [],
    dismissedWarnings: [],
    permanentDismissals: loadPermanentDismissals()
  }),
  getters: {
    /** First not-yet-dismissed warning, used to drive the global alert. */
    activeWarning(state): SkyLabSessionStatus | null {
      return (
        state.sessionStatuses.find(s => {
          if (!s.should_warn) return false;
          if (state.dismissedWarnings.includes(s.vmid)) return false;
          const key = warningKey(s);
          if (state.permanentDismissals[s.vmid] === key) return false;
          return true;
        }) ?? null
      );
    }
  },
  actions: {
    registerListeners() {
      if (!authExpiryListenerRegistered) {
        window.addEventListener("skylab:auth-expired", () => {
          this.handleSessionExpired();
        });
        authExpiryListenerRegistered = true;
      }
      on(ipcRouters.AUTH.getAuthState, data => {
        this.loggedIn = !!data.loggedIn;
        this.loginInProgress = !!data.loginInProgress;
      });
      on(
        ipcRouters.AUTH.logout,
        () => {
          this.loggedIn = false;
          this.resources = [];
          this.quickPracticeSessions = [];
          this.resourcesLoading = false;
          this.resourcesErrorCode = "";
          this.loginInProgress = false;
          this.tunnelStatus = { ...DEFAULT_TUNNEL_STATUS };
          this.stopSessionPolling();
          /* 換網址觸發的登出：舊伺服器的通道與 token 都收掉了，現在才存新網址；留在設定頁看結果 */
          if (pendingSettingsPatch) {
            const patch = pendingSettingsPatch;
            pendingSettingsPatch = null;
            send(ipcRouters.SETTINGS.saveSettings, patch);
            return;
          }
          if (router.currentRoute.value.name !== "Home") {
            void router.replace({ name: "Home" });
          }
        },
        code => {
          pendingSettingsPatch = null;
          this.settingsSaving = false;
          toastError(code);
        }
      );
      on(ipcRouters.SETTINGS.getSettings, data => {
        if (data) {
          this.language = data.language || "zh-TW";
          this.autoStart = !!data.launchAtStartup;
          this.backendUrl = data.backendUrl || this.backendUrl;
        }
      });
      on(
        ipcRouters.SETTINGS.saveSettings,
        data => {
          this.settingsSaving = false;
          if (data) {
            this.language = data.language || this.language;
            this.autoStart = !!data.launchAtStartup;
            this.backendUrl = data.backendUrl || this.backendUrl;
          }
        },
        /* 存檔失敗：設定頁會顯示原因；這裡把先行套用的語言／開機啟動拉回實際存著的值 */
        () => {
          this.settingsSaving = false;
          this.refreshSettings();
        }
      );
      on(
        ipcRouters.UPDATE.check,
        (info: SkyLabUpdateInfo | null) => {
          this.updateChecking = false;
          this.updateCheckError = !info;
          if (info) {
            this.updateInfo = info;
            this.updateCheckedAt = Date.now();
          }
        },
        () => {
          this.updateChecking = false;
          this.updateCheckError = true;
        }
      );
      on(
        ipcRouters.UPDATE.install,
        () => {
          this.updateInstalling = false;
        },
        code => {
          this.updateInstalling = false;
          this.updateInstallErrorCode = code;
        }
      );
      onListener(listeners.updateProgress, (progress: SkyLabUpdateProgress) => {
        this.updateProgress = progress;
      });
      on(
        ipcRouters.RESOURCE.listMyResources,
        data => {
          this.resourcesLoading = false;
          this.resourcesErrorCode = "";
          if (!this.loggedIn) return;
          this.resources = Array.isArray(data) ? data : [];
          lastResourceRefreshAt = Date.now();
        },
        code => {
          this.resourcesLoading = false;
          /* 登入失效時 store 已登出並清掉清單，不再另外顯示載入失敗 */
          if (!this.loggedIn) return;
          this.resourcesErrorCode = code;
        }
      );
      on(
        ipcRouters.RESOURCE.listMyQuickPracticeSessions,
        data => {
          if (!this.loggedIn) return;
          this.quickPracticeSessions = Array.isArray(data) ? data : [];
        },
        () => {
          this.quickPracticeSessions = [];
        }
      );
      on(
        ipcRouters.SESSION.getSessionStatuses,
        data => {
          const next: SkyLabSessionStatus[] = Array.isArray(data) ? data : [];
          this.sessionStatuses = next;
          // Forget in-memory dismissals once should_warn goes false.
          this.dismissedWarnings = this.dismissedWarnings.filter(vmid => {
            const status = next.find(s => s.vmid === vmid);
            return status && status.should_warn;
          });
          // Clear stale permanent dismissals when the warning key changes.
          let changed = false;
          const updated = { ...this.permanentDismissals };
          for (const s of next) {
            if (s.vmid in updated && updated[s.vmid] !== warningKey(s)) {
              delete updated[s.vmid];
              changed = true;
            }
          }
          if (changed) {
            this.permanentDismissals = updated;
            savePermanentDismissals(updated);
          }
          if (
            Date.now() - lastResourceRefreshAt >=
            RESOURCE_REFRESH_INTERVAL_MS
          ) {
            this.refreshResources();
          }
        },
        /* 背景輪詢：失敗就保留上一次的狀態，不跳 toast（離線時才不會每 30 秒跳一次） */
        () => undefined
      );
      on(ipcRouters.SESSION.extendSession, () => {
        // Refresh statuses immediately so the dialog dismisses naturally.
        this.refreshSessionStatuses();
      });
      /* 開網址／資料夾／授權聲明只在失敗時需要回饋；集中在這裡監聽，避免多個頁面重複跳 toast */
      for (const router of [
        ipcRouters.SYSTEM.openUrl,
        ipcRouters.SYSTEM.openAppData,
        ipcRouters.SYSTEM.openThirdPartyNotices
      ]) {
        on(router, () => undefined);
      }
      onListener(listeners.watchTunnel, (data: TunnelStatusInfo) => {
        this.tunnelStatus = data;
      });
    },
    refreshAuth() {
      send(ipcRouters.AUTH.getAuthState);
    },
    refreshSettings() {
      send(ipcRouters.SETTINGS.getSettings);
    },
    /**
     * 設定頁按儲存：語言、開機自動啟動先套用到畫面再存檔（失敗時由 saveSettings 的錯誤處理拉回）。
     * 有換後端網址、且已登入或通道開著時，要先在舊伺服器登出（停通道、撤銷 token），回覆後才存：
     * 反過來的話，主程序存網址時會先清掉 token，登出就看不到 token、不會去停通道。
     */
    saveSettings(patch: SkyLabSettingsPatch) {
      if (this.settingsSaving) return;
      this.settingsSaving = true;
      if (patch.language) this.language = patch.language;
      if (typeof patch.launchAtStartup === "boolean") {
        this.autoStart = patch.launchAtStartup;
      }
      if (patch.backendUrl && (this.loggedIn || this.tunnelStatus.running)) {
        pendingSettingsPatch = patch;
        this.logout();
      } else {
        send(ipcRouters.SETTINGS.saveSettings, patch);
      }
    },
    checkForUpdates(force = false) {
      if (this.updateChecking || this.updateInstalling) return;
      if (!force && Date.now() - this.updateCheckedAt < 5 * 60_000) return;
      this.updateChecking = true;
      this.updateCheckError = false;
      send(ipcRouters.UPDATE.check);
    },
    startUpdatePolling() {
      if (updatePollTimer) return;
      this.checkForUpdates();
      updatePollTimer = setInterval(() => {
        this.checkForUpdates(true);
      }, UPDATE_POLL_INTERVAL_MS);
    },
    installUpdate() {
      if (this.updateInstalling || !this.updateInfo?.updateAvailable) return;
      this.updateInstalling = true;
      this.updateProgress = null;
      this.updateInstallErrorCode = "";
      send(ipcRouters.UPDATE.install);
    },
    refreshResources() {
      if (!this.loggedIn || this.resourcesLoading) return;
      this.resourcesLoading = true;
      this.resourcesErrorCode = "";
      send(ipcRouters.RESOURCE.listMyResources);
      send(ipcRouters.RESOURCE.listMyQuickPracticeSessions);
    },
    refreshSessionStatuses() {
      if (!this.loggedIn) return;
      send(ipcRouters.SESSION.getSessionStatuses);
    },
    extendSession(vmid: number) {
      send(ipcRouters.SESSION.extendSession, { vmid });
    },
    dismissWarning(vmid: number) {
      if (!this.dismissedWarnings.includes(vmid)) {
        this.dismissedWarnings.push(vmid);
      }
    },
    dismissWarningPermanent(vmid: number) {
      const status = this.sessionStatuses.find(s => s.vmid === vmid);
      if (!status) return;
      const key = warningKey(status);
      this.permanentDismissals = { ...this.permanentDismissals, [vmid]: key };
      savePermanentDismissals(this.permanentDismissals);
      this.dismissWarning(vmid);
    },
    /** Begin / restart the polling timer. Idempotent. */
    startSessionPolling() {
      if (sessionPollTimer) return;
      this.refreshSessionStatuses();
      sessionPollTimer = setInterval(() => {
        this.refreshSessionStatuses();
      }, SESSION_POLL_INTERVAL_MS);
    },
    stopSessionPolling() {
      if (sessionPollTimer) {
        clearInterval(sessionPollTimer);
        sessionPollTimer = null;
      }
      this.sessionStatuses = [];
      this.dismissedWarnings = [];
    },
    handleSessionExpired() {
      /* 同時失敗的請求都會走到這裡，只在第一次（還是登入狀態時）提示 */
      if (this.loggedIn) ElMessage.warning(describeError(NOT_LOGGED_IN));
      this.loggedIn = false;
      this.loginInProgress = false;
      this.resources = [];
      this.quickPracticeSessions = [];
      this.resourcesLoading = false;
      this.resourcesErrorCode = "";
      this.stopSessionPolling();
      send(ipcRouters.TUNNEL.stop);
      if (router.currentRoute.value.name !== "Home") {
        void router.replace({ name: "Home" });
      }
    },
    logout() {
      send(ipcRouters.AUTH.logout);
    }
  }
});
