export default {
  app: {
    title: "SkyLab Connect",
    description: "Connect to your SkyLab virtual machines"
  },
  router: {
    home: { title: "Home" },
    resources: { title: "Resources" },
    logger: { title: "Logs" },
    config: { title: "Settings" },
    about: { title: "About" },
    login: { title: "Login" }
  },
  common: {
    save: "Save",
    cancel: "Cancel",
    confirm: "Confirm",
    refresh: "Refresh",
    copy: "Copy",
    copied: "Copied",
    loading: "Loading...",
    yes: "Yes",
    no: "No"
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
    title: "Sign in to SkyLab",
    connectTitle: "Connect to your machines",
    connectDescription:
      "Press connect. On first use, sign in in your browser and the secure connection will start automatically.",
    connect: "Connect",
    waitingShort: "Verifying",
    firstUseHint:
      "First use requires browser sign-in. You will return here automatically when it is complete.",
    description:
      "Click the button below; your browser will open to complete sign-in.",
    startButton: "Open browser to sign in",
    cancelButton: "Cancel",
    logoutButton: "Sign out",
    waiting: "Waiting for browser verification...",
    success: "Signed in",
    failure: "Sign-in failed: {error}",
    alreadyLoggedIn: "Signed in"
  },
  home: {
    status: {
      running: "Connected",
      stopped: "Disconnected",
      error: "Connection error",
      uptime: "Connected {time}"
    },
    button: {
      start: "Connect",
      stop: "Disconnect",
      refresh: "Refresh"
    },
    connect: {
      title: "Connect to SkyLab",
      description:
        "Create a secure connection, then view and access your virtual machines directly.",
      button: "Connect",
      connecting: "Creating secure connection",
      authenticating: "Waiting for sign-in",
      secureHint: "WireGuard encrypted · One click",
      authHint: "Complete sign-in in your browser · Connects automatically"
    },
    machines: {
      summary:
        "Connected · {machines} machines · {courses} course environments",
      unavailable: "No connection"
    },
    empty: {
      notLoggedIn: "Not signed in. Please sign in to SkyLab first.",
      noTunnels: "No tunnels available.",
      goLogin: "Go to sign-in",
      goResources: "View my resources"
    },
    tunnels: {
      title: "Available VM connections",
      empty: "Tunnel details will appear once connected",
      action: "Action",
      service: "Service",
      endpoint: "Local endpoint",
      machines: "Reachable machines",
      ready: "Ready",
      groupSummary: "{machines} machines · {connections} connections",
      connectSsh: "SSH Connect",
      connectRdp: "RDP Connect",
      machineStopped: "The machine is not running",
      invalidPort: "This connection has an invalid local port"
    }
  },
  resources: {
    title: "My Virtual Machines",
    webTitle: "My Resources",
    webSubtitle:
      "View and connect to your assigned virtual machines and containers",
    refresh: "Refresh",
    summary: "{total} machines across {courses} course environments",
    connect: "Connect",
    customEnvironment: "Custom environment",
    metrics: {
      total: "Machines",
      courseGroups: "Course environments"
    },
    course: {
      kind: "Course",
      title: "Course machines",
      description: "Review machines by course and expand a group for details",
      machineCount: "{count} machines · grouped management",
      runningCount: "{running}/{total} running"
    },
    personal: {
      title: "Personal resources",
      description: "Individually requested or assigned machines"
    },
    status: {
      running: "Running",
      stopped: "Stopped",
      paused: "Paused",
      provisioning: "Provisioning",
      failed: "Failed",
      unknown: "Unknown"
    },
    table: {
      name: "Name",
      vmid: "VMID",
      type: "Type",
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
      zhCN: "Traditional Chinese",
      enUS: "English"
    },
    autoStart: {
      label: "Launch at startup",
      tips: "Start SkyLab Connect hidden when the OS boots."
    },
    backend: {
      label: "Backend URL",
      tips: "SkyLab server address."
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
    openDataDir: "Open data directory"
  },
  logger: {
    tab: { appLog: "App log", frpcLog: "Tunnel log" },
    message: {
      openSuccess: "Log opened",
      refreshSuccess: "Refreshed"
    },
    autoRefresh: "Auto refresh",
    autoRefreshTime: "Refreshing in {time}s",
    search: { placeholder: "Search logs..." },
    loading: { text: "Loading..." },
    content: { empty: "No logs" }
  }
};
