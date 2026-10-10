export default {
  workspace: {
    navigation: "主要導覽",
    connectionInfo: "連線資訊",
    openWeb: "開啟 Web 平台",
    waitingGateway: "等待伺服器回應",
    waitingGatewayHint:
      "通道已啟動，但伺服器還沒有回應。若持續沒有回應，請檢查網路或聯絡管理員。",
    tunnelActive: "通道已啟動",
    tunnelActiveHint:
      "無法取得伺服器回應時間，正在改用連線測試確認機器連得到。",
    protocol: "通訊協定",
    interface: "網路介面",
    handshake: "伺服器最後回應",
    noHandshake: "尚未回應",
    handshakeUnavailable: "無法取得",
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
    noMatches: "沒有符合條件的機器",
    view: "檢視方式",
    clearFilters: "清除篩選",
    resourceError: "無法更新資源",
    appearance: "外觀",
    dark: "深色",
    light: "淺色",
    system: "系統",
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
    cancel: "取消",
    save: "儲存",
    refresh: "重新整理",
    loading: "載入中…",
    on: "開啟",
    off: "關閉"
  },
  unsavedGuard: {
    title: "尚未儲存的變更",
    message: "這一頁還有尚未儲存的變更，離開後修改將會遺失。",
    leave: "捨棄變更並離開"
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
      stop: "中斷連線"
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
    general: "一般",
    server: "伺服器",
    discard: "還原",
    saveFailed: "儲存失敗：{error}",
    title: "設定",
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
      tips: "SkyLab 伺服器根網址，不包含 /login。",
      logoutNotice: "儲存後會先登出並中斷目前的連線，再到新的伺服器重新登入。",
      confirmTitle: "變更後端網址？",
      confirmMessage:
        "SkyLab Connect 會先登出並中斷目前的安全連線，再改用新的伺服器，之後需要重新登入。",
      confirmButton: "登出並變更",
      error: {
        required: "請輸入後端網址。",
        invalid: "網址格式不正確，例如 https://skylab-tw.com。",
        insecure: "必須使用 https://（本機測試可用 http://localhost）。",
        extra: "網址不能包含帳號密碼、查詢參數（?）或 #。"
      }
    },
    account: {
      label: "帳號",
      loggedIn: "已登入",
      notLoggedIn: "尚未登入",
      logout: "登出",
      loginHint: "在「我的資源」按「開始連線」，會開啟瀏覽器登入 SkyLab。"
    },
    saveSuccess: "儲存成功"
  },
  about: {
    licenseTitle: "授權與原始碼",
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
  },
  errors: {
    /* 主程序錯誤碼的說明（electron/core/BusinessError.ts）；support 是共用的回報方式 */
    support:
      "若持續發生，請到「關於」按「開啟資料目錄」，把 logs 資料夾裡的記錄檔交給管理員。",
    B1000: "發生未預期的錯誤。@:errors.support",
    B1001: "登入狀態已過期，請重新登入。",
    B1002: "等候太久沒有完成登入，請按「開始連線」再試一次。",
    B1005: "伺服器暫時無法處理這個要求，請稍後再試。若持續發生，請聯絡管理員。",
    B1006: "找不到連線所需的 WireGuard 元件，請重新安裝 SkyLab Connect。",
    B1007: "無法讀取這台電腦上的連線金鑰。@:errors.support",
    B1008: "無法建立安全連線，請再試一次。@:errors.support",
    B1009: "無法安裝連線所需的 WireGuard 元件，請再試一次。@:errors.support",
    B1010:
      "無法下載或安裝更新，請稍後再試，也可以到 SkyLab 網頁下載最新版安裝程式。",
    B1011:
      "需要系統管理員權限才能變更安全連線。請再試一次，並在 Windows 詢問是否允許變更時選「是」。",
    B1012: "Windows 沒有完成安全連線的設定，請再試一次。@:errors.support",
    B1013: "安全連線的授權已到期，請按「開始連線」重新連線。",
    B1014: "偵測到上次留下的安全連線，請按「開始連線」重新建立。",
    B1015: "伺服器的網路設定已更新，請按「開始連線」重新套用。",
    B1016: "無法中斷安全連線，請再試一次。@:errors.support",
    B1017: "連不到 SkyLab 伺服器。請確認網路連線，或到「設定」檢查後端網址。",
    B1018: "無法開啟，請再試一次。@:errors.support"
  }
};
