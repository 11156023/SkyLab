export const ipcRouters: IpcRouters = {
  AUTH: {
    startLogin: {
      path: "auth/startLogin",
      controller: "authController.startLogin"
    },
    logout: {
      path: "auth/logout",
      controller: "authController.logout"
    },
    getAuthState: {
      path: "auth/getAuthState",
      controller: "authController.getAuthState"
    }
  },
  RESOURCE: {
    listMyResources: {
      path: "resource/listMyResources",
      controller: "resourceController.listMyResources"
    }
  },
  SESSION: {
    getSessionStatuses: {
      path: "session/getSessionStatuses",
      controller: "resourceController.getSessionStatuses"
    },
    extendSession: {
      path: "session/extendSession",
      controller: "resourceController.extendSession"
    }
  },
  TUNNEL: {
    start: {
      path: "tunnel/start",
      controller: "tunnelController.start"
    },
    stop: {
      path: "tunnel/stop",
      controller: "tunnelController.stop"
    },
    refresh: {
      path: "tunnel/refresh",
      controller: "tunnelController.refresh"
    },
    getStatus: {
      path: "tunnel/getStatus",
      controller: "tunnelController.getStatus"
    }
  },
  SETTINGS: {
    getSettings: {
      path: "settings/getSettings",
      controller: "settingsController.getSettings"
    },
    saveSettings: {
      path: "settings/saveSettings",
      controller: "settingsController.saveSettings"
    },
    getLanguage: {
      path: "settings/getLanguage",
      controller: "settingsController.getLanguage"
    },
    saveLanguage: {
      path: "settings/saveLanguage",
      controller: "settingsController.saveLanguage"
    }
  },
  LOG: {
    getAppLogContent: {
      path: "log/getAppLogContent",
      controller: "logController.getAppLogContent"
    },
    openAppLogFile: {
      path: "log/openAppLogFile",
      controller: "logController.openAppLogFile"
    }
  },
  SYSTEM: {
    openUrl: {
      path: "system/openUrl",
      controller: "systemController.openUrl"
    },
    relaunchApp: {
      path: "system/relaunchApp",
      controller: "systemController.relaunchApp"
    },
    openAppData: {
      path: "system/openAppData",
      controller: "systemController.openAppData"
    },
    openSsh: {
      path: "system/openSsh",
      controller: "systemController.openSsh"
    },
    openRdp: {
      path: "system/openRdp",
      controller: "systemController.openRdp"
    }
  }
};

export const listeners: Listeners = {
  watchTunnel: {
    listenerMethod: "wireGuardTunnelService.watchTunnel",
    channel: "tunnel:watch"
  }
};
