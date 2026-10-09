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
    practice: "クイック練習",
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
    light: "ライト",
    unnamedCourse: "名称未設定のコース"
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
    later: "後で通知"
  },
  router: {
    config: {
      title: "設定"
    },
    about: {
      title: "このアプリについて"
    }
  },
  common: {
    save: "保存",
    refresh: "更新",
    loading: "読み込み中..."
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
    success: "ログインしました",
    failure: "ログインに失敗しました: {error}"
  },
  home: {
    status: {
      leaseRefreshFailed:
        "接続の認証更新に失敗しました。自動的に再試行します。有効期限が切れた場合は再接続してください。",
      running: "接続済み",
      stopped: "未接続",
      error: "接続エラー"
    },
    button: {
      stop: "切断"
    },
    connect: {
      title: "SkyLab に接続",
      description:
        "安全な接続を作成し、割り当てられた仮想マシンにアクセスします。",
      button: "接続",
      connecting: "安全な接続を作成中",
      authenticating: "ログイン待機中",
      authHint: "ブラウザーでログインを完了すると自動接続します"
    },
    machines: {
      unavailable: "接続できません",
      noTargets:
        "安全な接続は有効ですが、SSH または RDP の接続先がありません。マシンの状態と IP アドレスを確認してください。"
    },
    empty: {
      notLoggedIn: "ログインしていません。先に SkyLab にログインしてください。"
    },
    tunnels: {
      connectSsh: "SSH 接続",
      connectRdp: "RDP 接続"
    }
  },
  resources: {
    webTitle: "マイリソース",
    course: {
      runningCount: "{running}/{total} 実行中"
    },
    personal: {
      title: "個人リソース"
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
  }
};
