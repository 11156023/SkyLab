import { on, onListener, send } from "@/utils/ipcUtils";
import { defineStore } from "pinia";
import { ipcRouters, listeners } from "../../electron/core/IpcRouter";
import GlobalConstant from "../../electron/core/GlobalConstant";
import router from "../router";

interface AppState {
  loggedIn: boolean;
  loginInProgress: boolean;
  loginError: string;
  loginErrorCode: string;
  connectionStarting: boolean;
  language: string;
  autoStart: boolean;
  backendUrl: string;
  resourcesLoading: boolean;
  resourcesError: string;
  /** 背景輪詢連不上後端；顯示常駐提示，下次成功就清掉（不跳錯誤訊息）。 */
  serverUnreachable: boolean;
  tunnelStatus: TunnelStatusInfo;
  resources: SkyLabResource[];
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
const LS_KEY = "session_warning_dismissed";
let sessionPollTimer: ReturnType<typeof setInterval> | null = null;
let authExpiryListenerRegistered = false;
let lastResourceRefreshAt = 0;
const registeredStores = new WeakSet<object>();

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
    loginError: "",
    loginErrorCode: "",
    connectionStarting: false,
    language: "zh-TW",
    autoStart: false,
    backendUrl: GlobalConstant.DEFAULT_BACKEND_URL,
    resourcesLoading: false,
    resourcesError: "",
    serverUnreachable: false,
    tunnelStatus: { ...DEFAULT_TUNNEL_STATUS },
    resources: [],
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
      if (registeredStores.has(this)) return;
      registeredStores.add(this);
      // Own the auth flow for the lifetime of the app, including navigation
      // away from Home while the browser is waiting for approval.
      on(
        ipcRouters.AUTH.startLogin,
        () => {},
        (code, message) => {
          if (!this.loginInProgress) return;
          this.loginInProgress = false;
          this.loginError = message;
          this.loginErrorCode = code;
        }
      );
      window.electronIpcRenderer.on("auth:event", (_event, response) => {
        if (response?.bizCode !== "A1000" || !this.loginInProgress) return;
        const payload = response.data;
        if (
          !payload ||
          !["login-success", "login-failure", "login-cancelled"].includes(
            payload.type
          )
        )
          return;
        this.loginInProgress = false;
        this.loginError = "";
        this.loginErrorCode = "";
        if (payload.type === "login-success") {
          this.loggedIn = true;
          this.refreshResources();
          this.connectionStarting = true;
          send(ipcRouters.TUNNEL.start);
        } else if (payload.type === "login-failure") {
          this.loginError = payload.error || "unknown";
          this.loginErrorCode = payload.code || "B1000";
        }
      });
      on(
        ipcRouters.TUNNEL.start,
        () => {
          this.connectionStarting = false;
          send(ipcRouters.TUNNEL.getStatus);
        },
        (_code, message) => {
          this.connectionStarting = false;
          this.tunnelStatus = {
            ...this.tunnelStatus,
            connectionError: message
          };
        }
      );
      on(ipcRouters.TUNNEL.getStatus, (data: TunnelStatusInfo) => {
        if (data) this.tunnelStatus = data;
      });
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
      on(ipcRouters.AUTH.logout, () => {
        this.loggedIn = false;
        this.resources = [];
        this.resourcesLoading = false;
        this.resourcesError = "";
        this.loginInProgress = false;
        this.loginError = "";
        this.loginErrorCode = "";
        this.connectionStarting = false;
        this.tunnelStatus = { ...DEFAULT_TUNNEL_STATUS };
        this.stopSessionPolling();
        if (router.currentRoute.value.name !== "Home") {
          void router.replace({ name: "Home" });
        }
      });
      on(ipcRouters.SETTINGS.getSettings, data => {
        if (data) {
          this.language = data.language || "zh-TW";
          this.autoStart = !!data.launchAtStartup;
          this.backendUrl = data.backendUrl || this.backendUrl;
        }
      });
      on(ipcRouters.SETTINGS.saveSettings, data => {
        if (data) {
          this.language = data.language || this.language;
          this.autoStart = !!data.launchAtStartup;
          this.backendUrl = data.backendUrl || this.backendUrl;
        }
      });
      on(
        ipcRouters.RESOURCE.listMyResources,
        data => {
          this.resourcesLoading = false;
          this.resourcesError = "";
          this.serverUnreachable = false;
          if (!this.loggedIn) return;
          this.resources = Array.isArray(data) ? data : [];
          lastResourceRefreshAt = Date.now();
        },
        (_code, message) => {
          this.resourcesLoading = false;
          this.resourcesError = message;
        }
      );
      on(
        ipcRouters.SESSION.getSessionStatuses,
        data => {
          this.serverUnreachable = false;
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
        bizCode => {
          // 背景輪詢每 30 秒一次：後端暫時連不上時不跳錯誤訊息（否則每 30 秒
          // 一則），改成常駐提示並照常重試。登入過期另由 skylab:auth-expired 處理。
          if (bizCode !== "B1001") this.serverUnreachable = true;
        }
      );
      on(ipcRouters.SESSION.extendSession, () => {
        // Refresh statuses immediately so the dialog dismisses naturally.
        this.refreshSessionStatuses();
      });
      onListener(listeners.watchTunnel, (data: TunnelStatusInfo) => {
        this.tunnelStatus = data;
      });
    },
    refreshAuth() {
      send(ipcRouters.AUTH.getAuthState);
    },
    startLogin() {
      if (this.loginInProgress) return;
      this.loginError = "";
      this.loginErrorCode = "";
      this.tunnelStatus = { ...this.tunnelStatus, connectionError: null };
      this.loginInProgress = true;
      send(ipcRouters.AUTH.startLogin);
    },
    cancelLogin() {
      this.loginInProgress = false;
      this.loginError = "";
      this.loginErrorCode = "";
      send(ipcRouters.AUTH.cancelLogin);
    },
    refreshSettings() {
      send(ipcRouters.SETTINGS.getSettings);
    },
    refreshResources() {
      if (!this.loggedIn || this.resourcesLoading) return;
      this.resourcesLoading = true;
      this.resourcesError = "";
      send(ipcRouters.RESOURCE.listMyResources);
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
      this.serverUnreachable = false;
    },
    handleSessionExpired() {
      // 只在這次過期時停一次通道：token 已清掉後 tunnel/stop 向後端 disconnect
      // 也會回 B1001，若每次都再送 stop 就會無限循環打後端、狂跳錯誤訊息。
      const wasLoggedIn = this.loggedIn;
      this.loggedIn = false;
      this.loginInProgress = false;
      this.connectionStarting = false;
      this.resources = [];
      this.resourcesLoading = false;
      this.resourcesError = "";
      this.stopSessionPolling();
      if (wasLoggedIn) send(ipcRouters.TUNNEL.stop);
      if (router.currentRoute.value.name !== "Home") {
        void router.replace({ name: "Home" });
      }
    },
    logout() {
      send(ipcRouters.AUTH.logout);
    }
  }
});
