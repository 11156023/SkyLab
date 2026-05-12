export default {
  app: {
    title: "Campus Cloud Connect",
    description: "Connect to your campus cloud virtual machines"
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
    title: "Sign in to Campus Cloud",
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
    empty: {
      notLoggedIn: "Not signed in. Please sign in to Campus Cloud first.",
      noTunnels: "No tunnels available.",
      goLogin: "Go to sign-in",
      goResources: "View my resources"
    },
    tunnels: {
      title: "Available VM connections",
      empty: "Tunnel details will appear once connected",
      action: "Action",
      connectSsh: "SSH Connect"
    }
  },
  resources: {
    title: "My Virtual Machines",
    refresh: "Refresh",
    table: {
      name: "Name",
      vmid: "VMID",
      type: "Type",
      status: "Status",
      node: "Node",
      ip: "Private IP",
      environment: "Env"
    },
    empty:
      "No virtual machines assigned. Please request one on Campus Cloud web."
  },
  config: {
    title: "Settings",
    language: {
      label: "Language",
      zhCN: "Traditional Chinese",
      enUS: "English"
    },
    autoStart: {
      label: "Launch at startup",
      tips: "Start Campus Cloud Connect hidden when the OS boots."
    },
    backend: {
      label: "Backend URL",
      tips: "Campus Cloud server address."
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
    name: "Campus Cloud Connect",
    description:
      "Securely reach your Campus Cloud virtual machines via frp reverse tunnels.",
    features: {
      oneClick: "One-click connect",
      bundled: "Bundled frpc",
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
