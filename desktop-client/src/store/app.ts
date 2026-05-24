import { on, onListener, send } from "@/utils/ipcUtils";
import { defineStore } from "pinia";
import { ipcRouters, listeners } from "../../electron/core/IpcRouter";

interface AppState {
  loggedIn: boolean;
  loginInProgress: boolean;
  language: string;
  autoStart: boolean;
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
const LS_KEY = "session_warning_dismissed";
let sessionPollTimer: ReturnType<typeof setInterval> | null = null;

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
    language: "zh-CN",
    autoStart: false,
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
      on(ipcRouters.AUTH.getAuthState, data => {
        this.loggedIn = !!data.loggedIn;
        this.loginInProgress = !!data.loginInProgress;
      });
      on(ipcRouters.SETTINGS.getSettings, data => {
        if (data) {
          this.language = data.language || "zh-CN";
          this.autoStart = !!data.launchAtStartup;
        }
      });
      on(ipcRouters.SETTINGS.saveSettings, data => {
        if (data) {
          this.language = data.language || this.language;
          this.autoStart = !!data.launchAtStartup;
        }
      });
      on(ipcRouters.RESOURCE.listMyResources, data => {
        this.resources = Array.isArray(data) ? data : [];
      });
      on(ipcRouters.SESSION.getSessionStatuses, data => {
        const next: SkyLabSessionStatus[] = Array.isArray(data)
          ? data
          : [];
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
      });
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
    refreshSettings() {
      send(ipcRouters.SETTINGS.getSettings);
    },
    refreshResources() {
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
    },
    logout() {
      send(ipcRouters.AUTH.logout);
      this.loggedIn = false;
      this.stopSessionPolling();
    }
  }
});
