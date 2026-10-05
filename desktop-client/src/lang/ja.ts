export default {
  workspace: {
    navigation: "メインナビゲーション",
    connectionInfo: "接続情報",
    openWeb: "Web を開く",
    waitingGateway: "ゲートウェイの応答待ち",
    waitingGatewayHint:
      "トンネルは起動しましたが、WireGuard ハンドシェイクは未受信です。応答がない場合はネットワークを確認するか、管理者に連絡してください。",
    tunnelActive: "トンネル起動中",
    tunnelActiveHint:
      "WireGuard のハンドシェイク情報を読み取れません。接続先への通信を確認しています。",
    protocol: "プロトコル",
    interface: "インターフェース",
    handshake: "最終ハンドシェイク",
    noHandshake: "未受信",
    handshakeUnavailable: "読み取り権限がありません",
    close: "閉じる",
    details: "マシンの詳細",
    detailsFor: "{name} の詳細",
    owner: "所有者",
    startsAt: "開始時刻",
    access: "アクセス権限",
    window_ended: "利用期間が終了しました",
    window_not_started: "利用期間はまだ開始されていません",
    readOnly: "閲覧のみ",
    connectFirst: "先に安全な接続を確立してください",
    disconnecting: "切断中",
    connected: "安全に接続済み",
    all: "すべて",
    course: "授業",
    personal: "個人",
    filter: "リソース分類",
    search: "マシン名・IP で検索",
    grid: "カード表示",
    list: "リスト表示",
    toggleTheme: "テーマを切り替え",
    machineCount: "{count} 台",
    noMatches: "一致するマシンがありません",
    resourceError: "リソースを更新できません。再読み込みしてください。",
    appearance: "外観",
    dark: "ダーク",
    light: "ライト"
  },
  update: {
    settingsTitle: "ソフトウェア更新",
    currentVersion: "現在のバージョン",
    available: "更新があります",
    availableVersion: "新しいバージョン {version} があります",
    upToDate: "最新版を使用しています",
    check: "更新を確認",
    checkError: "更新を確認できません。後でもう一度お試しください。",
    install: "ダウンロードしてインストール",
    confirmMessage:
      "インストーラーをダウンロードして検証した後、起動して現在の接続を切断します。続行しますか？",
    downloading: "更新をダウンロード中",
    verifying: "インストーラーを検証中",
    launching: "インストーラーを起動中",
    installError: "更新に失敗しました",
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
    openDataDir: "データフォルダーを開く",
    license: "ライセンス",
    licenseName: "GNU Affero General Public License v3.0",
    licenseHint:
      "SkyLab はオープンソースソフトウェアです。改変してネットワーク経由で第三者に提供する場合は改変後のソースコードを公開する必要があります。商用ライセンスも提供しています。",
    repository: "ソースコード",
    thirdPartyNotices: "サードパーティライセンス",
    components: {
      title: "オープンソースコンポーネント",
      hint: "このアプリケーションが直接依存する {count} 個のパッケージです。ビルド時に package.json から生成されます。",
      package: "パッケージ",
      version: "バージョン",
      license: "ライセンス"
    }
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
