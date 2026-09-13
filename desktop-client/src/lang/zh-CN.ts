export default {
  app: {
    title: "SkyLab Connect",
    description: "校園雲端虛擬機連線工具"
  },
  router: {
    home: { title: "主頁" },
    resources: { title: "我的資源" },
    logger: { title: "日誌" },
    config: { title: "設定" },
    about: { title: "關於" },
    login: { title: "登入" }
  },
  common: {
    save: "儲存",
    cancel: "取消",
    confirm: "確定",
    refresh: "重新整理",
    copy: "複製",
    copied: "已複製",
    loading: "載入中...",
    yes: "是",
    no: "否"
  },
  sessionWarning: {
    autoStopTitle: "VM 即將自動關機",
    autoStopBody:
      "VM #{vmid} 將在約 {minutes} 分鐘後自動關機。需要繼續使用嗎？",
    expiryTitle: "資源即將到期",
    expiryBody:
      "VM #{vmid} 將在約 {hours} 小時後到期並停用。請及早備份資料；如需延長使用期限，請向管理員申請。",
    extend: "延長使用時間",
    later: "稍後再說",
    gotIt: "知道了",
    doNotShow: "不再顯示此提醒"
  },
  login: {
    title: "登入 SkyLab",
    connectTitle: "一鍵連線到你的機器",
    connectDescription:
      "按下連線後，首次使用會開啟瀏覽器完成登入，接著自動建立安全連線。",
    connect: "開始連線",
    waitingShort: "驗證中",
    firstUseHint: "首次使用需要在瀏覽器登入；完成後會自動回到這裡。",
    description: "點擊下方按鈕，會開啟瀏覽器完成登入。完成後請回到此視窗。",
    startButton: "開啟瀏覽器登入",
    cancelButton: "取消登入",
    logoutButton: "登出",
    waiting: "等待瀏覽器完成驗證...",
    success: "登入成功",
    failure: "登入失敗：{error}",
    alreadyLoggedIn: "已登入"
  },
  home: {
    status: {
      running: "已連線",
      stopped: "未連線",
      error: "連線錯誤",
      uptime: "已連線 {time}"
    },
    button: {
      start: "啟動連線",
      stop: "停止連線",
      refresh: "重新整理"
    },
    connect: {
      title: "連線到 SkyLab",
      description: "按一下建立安全連線，完成後就能直接查看並連接你的虛擬機。",
      button: "開始連線",
      connecting: "正在建立安全連線",
      authenticating: "等待登入驗證",
      secureHint: "WireGuard 加密 · 一鍵完成",
      authHint: "請在瀏覽器完成登入 · 完成後自動連線"
    },
    machines: {
      summary: "連線已建立 · {machines} 台機器 · {courses} 個課程環境",
      unavailable: "無可用連線"
    },
    empty: {
      notLoggedIn: "尚未登入，請先登入 SkyLab 帳號。",
      noTunnels: "目前沒有可用的虛擬機隧道。",
      goLogin: "前往登入",
      goResources: "查看我的資源"
    },
    tunnels: {
      title: "可用的虛擬機連線",
      empty: "連線啟動後會顯示虛擬機清單",
      action: "操作",
      service: "服務",
      endpoint: "本機端點",
      machines: "可連線機器",
      ready: "可連線",
      groupSummary: "{machines} 台機器 · {connections} 個連線",
      connectSsh: "SSH 連線",
      connectRdp: "RDP 連線",
      machineStopped: "機器尚未開機",
      invalidPort: "此連線的本機 Port 設定無效"
    }
  },
  resources: {
    title: "我的虛擬機",
    webTitle: "我的資源",
    webSubtitle: "查看並連接已配置的虛擬機和容器",
    refresh: "重新整理",
    summary: "共 {total} 台機器，分屬 {courses} 個課程環境",
    connect: "連線",
    customEnvironment: "自訂環境",
    metrics: {
      total: "機器總數",
      courseGroups: "課程環境"
    },
    course: {
      kind: "課程",
      title: "課程機器",
      description: "依課程整組檢視，展開後可查看各台機器",
      machineCount: "{count} 台機器 · 整組管理",
      runningCount: "{running}/{total} 執行中"
    },
    personal: {
      title: "個人資源",
      description: "由個人申請或單獨配置的機器"
    },
    status: {
      running: "執行中",
      stopped: "已停止",
      paused: "已暫停",
      provisioning: "建立中",
      failed: "建立失敗",
      unknown: "狀態未知"
    },
    table: {
      name: "名稱",
      vmid: "VMID",
      type: "類型",
      status: "狀態",
      node: "節點",
      ip: "內網 IP",
      environment: "環境",
      expiry: "到期日"
    },
    empty: "目前沒有任何虛擬機，請至 SkyLab 網頁申請。"
  },
  config: {
    title: "設定",
    back: "返回連線畫面",
    language: {
      label: "介面語言",
      zhCN: "繁體中文",
      enUS: "English"
    },
    autoStart: {
      label: "開機自動啟動",
      tips: "開機時自動啟動 SkyLab Connect 並隱藏視窗。"
    },
    backend: {
      label: "後端網址",
      tips: "SkyLab 伺服器位址。"
    },
    account: {
      label: "帳號",
      loggedIn: "已登入",
      notLoggedIn: "尚未登入",
      logout: "登出"
    },
    saveSuccess: "儲存成功"
  },
  about: {
    name: "SkyLab Connect",
    description: "透過 WireGuard 加密網路安全連線至您的 SkyLab 虛擬機。",
    features: {
      oneClick: "一鍵連線",
      bundled: "WireGuard 加密通道",
      secure: "僅對已授權的虛擬機開放"
    },
    version: "版本",
    openDataDir: "開啟資料目錄"
  },
  logger: {
    tab: {
      appLog: "應用日誌",
      frpcLog: "連線日誌"
    },
    message: {
      openSuccess: "開啟日誌成功",
      refreshSuccess: "重新整理成功"
    },
    autoRefresh: "自動重新整理",
    autoRefreshTime: "{time} 秒後自動重新整理",
    search: { placeholder: "搜尋日誌..." },
    loading: { text: "載入中..." },
    content: { empty: "目前沒有日誌" }
  }
};
