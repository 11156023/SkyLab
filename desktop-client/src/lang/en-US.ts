export default {
  workspace: {
    navigation: "Main navigation",
    connectionInfo: "Connection details",
    openWeb: "Open web platform",
    waitingGateway: "Waiting for gateway",
    waitingGatewayHint:
      "The tunnel has started, but no WireGuard handshake has been received. Check your network or contact an administrator if this continues.",
    tunnelActive: "Tunnel active",
    tunnelActiveHint:
      "WireGuard handshake details are unavailable. Checking machine reachability.",
    protocol: "Protocol",
    interface: "Interface",
    handshake: "Last handshake",
    noHandshake: "Not received yet",
    handshakeUnavailable: "Permission required",
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
    noMatches: "No machines match your search",
    resourceError: "Could not update resources. Please refresh.",
    appearance: "Appearance",
    dark: "Dark",
    light: "Light",
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
    installError: "Update failed",
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
    save: "Save",
    refresh: "Refresh",
    loading: "Loading..."
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
    title: "Settings",
    back: "Back to connection",
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
      tips: "SkyLab server root URL, without /login."
    },
    account: {
      label: "Account",
      loggedIn: "Signed in",
      notLoggedIn: "Not signed in",
      logout: "Sign out"
    },
    saveSuccess: "Saved"
  },
  about: {
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
  }
};
