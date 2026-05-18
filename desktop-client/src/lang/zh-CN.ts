export default {
  app: {
    title: "Campus Cloud Connect",
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
    autoStopBody: "VM #{vmid} 將在約 {minutes} 分鐘後自動關機。需要繼續使用嗎？",
    expiryTitle: "資源即將到期",
    expiryBody:
      "VM #{vmid} 將在約 {hours} 小時後到期並停用。請及早備份資料；如需延長使用期限，請向管理員申請。",
    extend: "延長使用時間",
    later: "稍後再說",
    gotIt: "知道了",
    doNotShow: "不再顯示此提醒"
  },
  login: {
    title: "登入 Campus Cloud",
    description:
      "點擊下方按鈕，會開啟瀏覽器完成登入。完成後請回到此視窗。",
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
    empty: {
      notLoggedIn: "尚未登入，請先登入 Campus Cloud 帳號。",
      noTunnels: "目前沒有可用的虛擬機隧道。",
      goLogin: "前往登入",
      goResources: "查看我的資源"
    },
    tunnels: {
      title: "可用的虛擬機連線",
      empty: "連線啟動後會顯示虛擬機清單",
      action: "操作",
      connectSsh: "SSH 連線"
    }
  },
  resources: {
    title: "我的虛擬機",
    refresh: "重新整理",
    table: {
      name: "名稱",
      vmid: "VMID",
      type: "類型",
      status: "狀態",
      node: "節點",
      ip: "內網 IP",
      environment: "環境"
    },
    empty: "目前沒有任何虛擬機，請至 Campus Cloud 網頁申請。"
  },
  config: {
    title: "設定",
    language: {
      label: "介面語言",
      zhCN: "繁體中文",
      enUS: "English"
    },
    autoStart: {
      label: "開機自動啟動",
      tips: "開機時自動啟動 Campus Cloud Connect 並隱藏視窗。"
    },
    backend: {
      label: "後端網址",
      tips: "Campus Cloud 伺服器位址。"
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
    name: "Campus Cloud Connect",
    description:
      "透過 frp 反向代理安全連線至您的 Campus Cloud 虛擬機。",
    features: {
      oneClick: "一鍵連線",
      bundled: "免安裝 frpc",
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
