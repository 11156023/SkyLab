export default {
  workspace: {
    navigation: "Main navigation",
    connectionInfo: "Connection details",
    openWeb: "Open web platform",
    waitingGateway: "Waiting for server",
    waitingGatewayHint:
      "The tunnel has started, but the server hasn't responded yet. If it still doesn't respond, check your network or contact an administrator.",
    tunnelActive: "Tunnel active",
    tunnelActiveHint:
      "The server's last response time isn't available. Checking that your machines are reachable instead.",
    protocol: "Protocol",
    interface: "Interface",
    handshake: "Last server response",
    noHandshake: "No response yet",
    handshakeUnavailable: "Unavailable",
    close: "Close",
    details: "Machine details",
    detailsFor: "Details for {name}",
    owner: "Owner",
    startsAt: "Starts at",
    access: "Access",
    window_ended: "Access period ended",
    window_not_started: "Access period has not started",
    readOnly: "View only",
    connectFirst: "Connect to SkyLab first",
    disconnecting: "Disconnecting",
    connected: "Securely connected",
    all: "All",
    course: "Courses",
    practice: "Quick practice",
    personal: "Personal",
    filter: "Resource category",
    search: "Search machine or IP",
    grid: "Card view",
    list: "List view",
    toggleTheme: "Toggle color theme",
    machineCount: "{count} machines",
    noMatches: "No machines match",
    view: "View",
    clearFilters: "Clear filters",
    resourceError: "Couldn't update resources",
    appearance: "Appearance",
    dark: "Dark",
    light: "Light",
    system: "System",
    unnamedCourse: "Untitled course"
  },
  update: {
    settingsTitle: "Software update",
    currentVersion: "Current version",
    available: "Update available",
    availableVersion: "Version {version} is available",
    upToDate: "You're up to date",
    check: "Check for updates",
    checkError: "Could not check for updates. Try again later.",
    install: "Download and install",
    confirmMessage:
      "The app will download and verify the installer, then open it and disconnect the current session. Continue?",
    downloading: "Downloading update",
    verifying: "Verifying installer",
    launching: "Opening installer",
    title: "Update available",
    later: "Remind me later"
  },
  router: {
    config: {
      title: "Settings"
    },
    about: {
      title: "About"
    }
  },
  common: {
    cancel: "Cancel",
    save: "Save",
    refresh: "Refresh",
    loading: "Loading…",
    on: "On",
    off: "Off"
  },
  unsavedGuard: {
    title: "Unsaved changes",
    message: "This page has unsaved changes. They will be lost if you leave.",
    leave: "Discard and leave"
  },
  sessionWarning: {
    autoStopTitle: "VM will auto-stop soon",
    autoStopBody:
      "VM #{vmid} will be powered off in about {minutes} minutes. Keep it running?",
    expiryTitle: "Resource expiring soon",
    expiryBody:
      "VM #{vmid} will expire and be deactivated in about {hours} hours. Please back up your data; contact an admin if you need to extend the lease.",
    extend: "Extend session",
    later: "Remind me later",
    gotIt: "Got it",
    doNotShow: "Don't show this again"
  },
  login: {
    success: "Signed in",
    failure: "Sign-in failed: {error}"
  },
  home: {
    status: {
      leaseRefreshFailed:
        "Session renewal failed and will retry automatically. Reconnect if the session expires.",
      running: "Connected",
      stopped: "Disconnected",
      error: "Connection error"
    },
    button: {
      stop: "Disconnect"
    },
    connect: {
      title: "Connect to SkyLab",
      description:
        "Create a secure connection, then view and access your virtual machines directly.",
      button: "Connect",
      connecting: "Creating secure connection",
      authenticating: "Waiting for sign-in",
      authHint: "Complete sign-in in your browser · Connects automatically"
    },
    machines: {
      unavailable: "No connection",
      noTargets:
        "The secure connection is active, but no SSH or RDP targets are available. Check that a machine is running and has a reachable IP. If the problem persists, ask an administrator to check the VPN subnet."
    },
    empty: {
      notLoggedIn: "Not signed in. Please sign in to SkyLab first."
    },
    tunnels: {
      connectSsh: "SSH Connect",
      connectRdp: "RDP Connect"
    }
  },
  resources: {
    webTitle: "My Resources",
    course: {
      runningCount: "{running}/{total} running"
    },
    personal: {
      title: "Personal resources"
    },
    status: {
      running: "Running",
      stopped: "Stopped",
      paused: "Paused",
      scheduled: "Scheduled",
      provisioning: "Provisioning",
      starting: "Starting",
      deleting: "Deleting",
      failed: "Failed",
      deleted: "Deleted",
      unknown: "Unknown"
    },
    table: {
      name: "Name",
      vmid: "VMID",
      status: "Status",
      node: "Node",
      ip: "Private IP",
      environment: "Env",
      expiry: "Expires"
    },
    empty: "No virtual machines assigned. Please request one on SkyLab web."
  },
  config: {
    general: "General",
    server: "Server",
    discard: "Restore",
    saveFailed: "Could not save: {error}",
    title: "Settings",
    language: {
      label: "Language",
      zhTW: "Traditional Chinese",
      enUS: "English",
      ja: "Japanese"
    },
    autoStart: {
      label: "Launch at startup",
      tips: "Start SkyLab Connect hidden when the OS boots."
    },
    backend: {
      label: "Backend URL",
      tips: "SkyLab server root URL, without /login.",
      logoutNotice:
        "Saving signs you out and disconnects first, then you sign in to the new server.",
      confirmTitle: "Change the backend URL?",
      confirmMessage:
        "SkyLab Connect will sign out and close the current secure connection before switching servers. You will need to sign in again.",
      confirmButton: "Sign out and change",
      error: {
        required: "Enter the backend URL.",
        invalid: "This URL is not valid, e.g. https://skylab-tw.com.",
        insecure:
          "Use https:// (http://localhost is allowed for local testing).",
        extra: "The URL must not include credentials, a query (?) or #."
      }
    },
    account: {
      label: "Account",
      loggedIn: "Signed in",
      notLoggedIn: "Not signed in",
      logout: "Sign out",
      loginHint:
        "Click Connect on My Resources to sign in to SkyLab in your browser."
    },
    saveSuccess: "Saved"
  },
  about: {
    licenseTitle: "License & source code",
    name: "SkyLab Connect",
    description: "Securely reach your SkyLab virtual machines over WireGuard.",
    features: {
      oneClick: "One-click connect",
      bundled: "WireGuard encrypted tunnel",
      secure: "Authorized VMs only"
    },
    version: "Version",
    openDataDir: "Open data directory",
    license: "License",
    licenseName: "GNU Affero General Public License v3.0",
    licenseHint:
      "SkyLab is open source. If you modify it and offer it to others over a network you must publish your changes; commercial licensing is available.",
    repository: "Source code",
    thirdPartyNotices: "Third-party notices",
    components: {
      title: "Open-source components",
      hint: "The {count} packages this application depends on directly, generated from package.json at build time.",
      package: "Package",
      version: "Version",
      license: "License"
    }
  },
  errors: {
    /* 主程序錯誤碼的說明（electron/core/BusinessError.ts）；support 是共用的回報方式 */
    support:
      "If this keeps happening, go to About, select “Open data directory”, and send the files in the logs folder to your administrator.",
    B1000: "Something went wrong. @:errors.support",
    B1001: "Your sign-in has expired. Please sign in again.",
    B1002: "Sign-in wasn't completed in time. Select “Connect” to try again.",
    B1005:
      "The server couldn't handle the request right now. Try again later, and contact your administrator if it keeps happening.",
    B1006:
      "The WireGuard component needed to connect wasn't found. Reinstall SkyLab Connect.",
    B1007:
      "The connection key on this computer couldn't be read. @:errors.support",
    B1008:
      "The secure connection couldn't be created. Try again. @:errors.support",
    B1009:
      "The WireGuard component needed to connect couldn't be installed. Try again. @:errors.support",
    B1010:
      "The update couldn't be downloaded or installed. Try again later, or download the latest installer from the SkyLab website.",
    B1011:
      "Administrator permission is needed to change the secure connection. Try again and choose “Yes” when Windows asks for permission.",
    B1012:
      "Windows didn't finish setting up the secure connection. Try again. @:errors.support",
    B1013:
      "The secure connection's authorization has expired. Select “Connect” to reconnect.",
    B1014:
      "A secure connection from an earlier session is still active. Select “Connect” to set it up again.",
    B1015:
      "The server's network settings changed. Select “Connect” to apply them.",
    B1016:
      "The secure connection couldn't be disconnected. Try again. @:errors.support",
    B1017:
      "Can't reach the SkyLab server. Check your network connection, or check the Backend URL in Settings.",
    B1018: "Couldn't open it. Try again. @:errors.support"
  }
};
