export default {
  workspace: {
    navigation: "主要導覽",
    connectionInfo: "連線資訊",
    openWeb: "開啟 Web 平台",
    waitingGateway: "等待 Gateway 回應",
    waitingGatewayHint:
      "通道已啟動，尚未收到 WireGuard 握手。若持續無回應，請檢查網路或聯絡管理員。",
    tunnelActive: "通道已啟動",
    tunnelActiveHint: "目前無法讀取 WireGuard 握手資訊，正在確認機器連通性。",
    protocol: "通訊協定",
    interface: "網路介面",
    handshake: "最近一次握手",
    noHandshake: "尚未收到",
    handshakeUnavailable: "無權限讀取",
    close: "關閉",
    details: "機器詳情",
    detailsFor: "{name} 詳情",
    owner: "擁有者",
    startsAt: "開始時間",
    access: "使用權限",
    window_ended: "使用時段已結束",
    window_not_started: "使用時段尚未開始",
    readOnly: "僅供檢視",
    connectFirst: "請先建立安全連線",
    disconnecting: "正在中斷連線",
    connected: "已安全連線",
    all: "全部資源",
    course: "課程環境",
    practice: "快速練習",
    personal: "個人資源",
    filter: "資源分類",
    search: "搜尋機器或 IP",
    grid: "卡片檢視",
    list: "列表檢視",
    toggleTheme: "切換明暗主題",
    machineCount: "{count} 台",
    noMatches: "找不到符合條件的機器",
    resourceError: "無法更新資源，請重新整理",
    appearance: "外觀",
    dark: "深色",
    light: "亮色",
    unnamedCourse: "未命名課程"
  },
  update: {
    settingsTitle: "軟體更新",
    currentVersion: "目前版本",
    available: "有可用更新",
    availableVersion: "新版本 {version} 已發布",
    upToDate: "目前已是最新版",
    check: "檢查更新",
    checkError: "暫時無法檢查更新，請稍後重試。",
    install: "下載並安裝",
    confirmMessage:
      "App 會下載並驗證安裝程式，然後啟動安裝並中斷目前連線。要繼續嗎？",
    downloading: "正在下載更新",
    verifying: "正在驗證安裝程式",
    launching: "正在啟動安裝程式",
    installError: "更新失敗",
    title: "發現新版本",
    later: "稍後提醒"
  },
  router: {
    config: {
      title: "設定"
    },
    about: {
      title: "關於"
    }
  },
  common: {
    save: "儲存",
    refresh: "重新整理",
    loading: "載入中..."
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
    success: "登入成功",
    failure: "登入失敗：{error}"
  },
  home: {
    status: {
      leaseRefreshFailed:
        "連線授權更新失敗，將自動重試；授權到期後須重新連線。",
      running: "已連線",
      stopped: "未連線",
      error: "連線錯誤"
    },
    button: {
      stop: "停止連線"
    },
    connect: {
      title: "連線到 SkyLab",
      description: "按一下建立安全連線，完成後就能直接查看並連接你的虛擬機。",
      button: "開始連線",
      connecting: "正在建立安全連線",
      authenticating: "等待登入驗證",
      authHint: "請在瀏覽器完成登入 · 完成後自動連線"
    },
    machines: {
      unavailable: "無可用連線",
      noTargets:
        "安全連線已建立，但目前沒有可用的 SSH／RDP 目標。請確認機器已啟動並取得可連線的 IP；若仍無法使用，請聯絡管理員檢查 VPN 網段設定。"
    },
    empty: {
      notLoggedIn: "尚未登入，請先登入 SkyLab 帳號。"
    },
    tunnels: {
      connectSsh: "SSH 連線",
      connectRdp: "RDP 連線"
    }
  },
  resources: {
    webTitle: "我的資源",
    course: {
      runningCount: "{running}/{total} 執行中"
    },
    personal: {
      title: "個人資源"
    },
    status: {
      running: "執行中",
      stopped: "已停止",
      paused: "已暫停",
      scheduled: "已排程",
      provisioning: "建立中",
      starting: "啟動中",
      deleting: "刪除中",
      failed: "建立失敗",
      deleted: "已刪除",
      unknown: "狀態未知"
    },
    table: {
      name: "名稱",
      vmid: "VMID",
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
      zhTW: "繁體中文",
      enUS: "English",
      ja: "日本語"
    },
    autoStart: {
      label: "開機自動啟動",
      tips: "開機時自動啟動 SkyLab Connect 並隱藏視窗。"
    },
    backend: {
      label: "後端網址",
      tips: "SkyLab 伺服器根網址，不包含 /login。"
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
    openDataDir: "開啟資料目錄",
    license: "授權",
    licenseName: "GNU Affero General Public License v3.0",
    licenseHint:
      "SkyLab 是開源軟體；修改後對外提供網路服務時須公開修改後的原始碼，也可洽談商業授權。",
    repository: "原始碼",
    thirdPartyNotices: "第三方授權聲明",
    components: {
      title: "開源元件",
      hint: "本程式直接使用的 {count} 個套件，由建置時的 package.json 產生。",
      package: "套件",
      version: "版本",
      license: "授權"
    }
  }
};
