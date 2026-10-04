export default {
  update: {
    title: "新しいバージョンがあります",
    message:
      "SkyLab Connect {version} が公開されました。最新版をダウンロードしてください。",
    download: "更新をダウンロード",
    later: "後で通知"
  },
  app: {
    title: "SkyLab Connect",
    description: "SkyLab の仮想マシンに接続します"
  },
  router: {
    home: { title: "ホーム" },
    resources: { title: "リソース" },
    logger: { title: "ログ" },
    config: { title: "設定" },
    about: { title: "このアプリについて" },
    login: { title: "ログイン" }
  },
  common: {
    save: "保存",
    cancel: "キャンセル",
    confirm: "確認",
    refresh: "更新",
    copy: "コピー",
    copied: "コピーしました",
    loading: "読み込み中...",
    yes: "はい",
    no: "いいえ"
  },
  sessionWarning: {
    autoStopTitle: "仮想マシンはまもなく自動停止します",
    autoStopBody:
      "VM #{vmid} は約 {minutes} 分後に停止します。実行を延長しますか？",
    expiryTitle: "リソースの期限が近づいています",
    expiryBody:
      "VM #{vmid} は約 {hours} 時間後に期限切れとなります。必要なデータをバックアップしてください。",
    extend: "利用時間を延長",
    later: "後で通知",
    gotIt: "確認しました",
    doNotShow: "今後表示しない"
  },
  login: {
    title: "SkyLab にログイン",
    connectTitle: "マシンに接続",
    connectDescription:
      "接続を押し、ブラウザーでログインを完了すると安全な接続が自動的に開始されます。",
    connect: "接続",
    waitingShort: "確認中",
    firstUseHint:
      "初回はブラウザーでのログインが必要です。完了後、自動的に戻ります。",
    description: "下のボタンを押すとブラウザーが開きます。",
    startButton: "ブラウザーでログイン",
    cancelButton: "キャンセル",
    logoutButton: "ログアウト",
    waiting: "ブラウザーでの確認を待っています...",
    success: "ログインしました",
    failure: "ログインに失敗しました: {error}",
    alreadyLoggedIn: "ログイン済み"
  },
  home: {
    status: {
      leaseRefreshFailed:
        "接続の認証更新に失敗しました。自動的に再試行します。有効期限が切れた場合は再接続してください。",
      running: "接続済み",
      stopped: "未接続",
      error: "接続エラー",
      uptime: "接続時間 {time}"
    },
    button: { start: "接続", stop: "切断", refresh: "更新" },
    connect: {
      title: "SkyLab に接続",
      description:
        "安全な接続を作成し、割り当てられた仮想マシンにアクセスします。",
      button: "接続",
      connecting: "安全な接続を作成中",
      authenticating: "ログイン待機中",
      secureHint: "WireGuard 暗号化 · ワンクリック",
      authHint: "ブラウザーでログインを完了すると自動接続します"
    },
    machines: {
      summary: "接続済み · {machines} 台 · {courses} コース環境",
      unavailable: "接続できません",
      noTargets:
        "安全な接続は有効ですが、SSH または RDP の接続先がありません。マシンの状態と IP アドレスを確認してください。"
    },
    empty: {
      notLoggedIn: "ログインしていません。先に SkyLab にログインしてください。",
      goLogin: "ログインへ",
      goResources: "リソースを表示"
    },
    tunnels: {
      title: "利用可能な接続",
      empty: "接続後にトンネル情報が表示されます",
      action: "操作",
      service: "サービス",
      endpoint: "ローカル接続先",
      machines: "到達可能なマシン",
      ready: "準備完了",
      groupSummary: "{machines} 台 · {connections} 接続",
      connectSsh: "SSH 接続",
      connectRdp: "RDP 接続",
      machineStopped: "マシンは停止しています",
      invalidPort: "ローカルポートの設定が無効です"
    }
  },
  resources: {
    title: "仮想マシン",
    webTitle: "マイリソース",
    webSubtitle: "割り当てられた仮想マシンとコンテナーを表示して接続します",
    refresh: "更新",
    summary: "{courses} コース環境、合計 {total} 台",
    connect: "接続",
    customEnvironment: "カスタム環境",
    owner: "所有者: {owner}",
    kind: {
      personal: "個人申請",
      shared: "共有リソース",
      teaching_class: "クラス用マシン",
      quick_practice: "クイック練習",
      course: "コース実習"
    },
    window: {
      notStarted: "利用開始時刻: {time}",
      ended: "利用期間は {time} に終了しました"
    },
    metrics: { total: "マシン数", courseGroups: "コース環境" },
    course: {
      kind: "コース",
      title: "コース用マシン",
      description: "コース別にマシンを確認できます",
      machineCount: "{count} 台 · グループ管理",
      runningCount: "{running}/{total} 実行中"
    },
    personal: {
      title: "個人リソース",
      description: "個別に申請または割り当てられたマシン"
    },
    status: {
      running: "実行中",
      stopped: "停止",
      paused: "一時停止",
      scheduled: "予約済み",
      provisioning: "作成中",
      starting: "起動中",
      deleting: "削除中",
      failed: "失敗",
      deleted: "削除済み",
      unknown: "不明"
    },
    table: {
      name: "名前",
      vmid: "VMID",
      type: "種類",
      status: "状態",
      node: "ノード",
      ip: "プライベート IP",
      environment: "環境",
      expiry: "期限"
    },
    empty:
      "割り当てられた仮想マシンはありません。SkyLab Web から申請してください。"
  },
  config: {
    title: "設定",
    back: "接続画面に戻る",
    language: {
      label: "表示言語",
      zhTW: "繁體中文",
      enUS: "English",
      ja: "日本語"
    },
    autoStart: {
      label: "自動起動",
      tips: "OS の起動時に SkyLab Connect を非表示で開始します。"
    },
    backend: {
      label: "バックエンド URL",
      tips: "/login を含まない SkyLab サーバーのルート URL。"
    },
    account: {
      label: "アカウント",
      loggedIn: "ログイン済み",
      notLoggedIn: "未ログイン",
      logout: "ログアウト"
    },
    saveSuccess: "保存しました"
  },
  about: {
    name: "SkyLab Connect",
    description: "WireGuard を使用して SkyLab の仮想マシンに安全に接続します。",
    features: {
      oneClick: "ワンクリック接続",
      bundled: "WireGuard 暗号化トンネル",
      secure: "許可された VM のみ"
    },
    version: "バージョン",
    openDataDir: "データフォルダーを開く"
  },
  logger: {
    tab: { appLog: "アプリログ" },
    message: {
      openSuccess: "ログを開きました",
      refreshSuccess: "更新しました"
    },
    autoRefresh: "自動更新",
    autoRefreshTime: "{time} 秒後に更新",
    search: { placeholder: "ログを検索..." },
    loading: { text: "読み込み中..." },
    content: { empty: "ログはありません" }
  }
};
