export default {
  workspace: {
    navigation: "メインナビゲーション",
    connectionInfo: "接続情報",
    openWeb: "Web を開く",
    waitingGateway: "サーバーの応答待ち",
    waitingGatewayHint:
      "トンネルは起動しましたが、サーバーからまだ応答がありません。応答がないままの場合は、ネットワークを確認するか管理者に連絡してください。",
    tunnelActive: "トンネル起動中",
    tunnelActiveHint:
      "サーバーの最終応答時刻を取得できないため、接続テストでマシンに届くか確認しています。",
    protocol: "プロトコル",
    interface: "インターフェース",
    handshake: "サーバーの最終応答",
    noHandshake: "まだ応答なし",
    handshakeUnavailable: "取得できません",
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
    noMatches: "条件に合うマシンがありません",
    view: "表示形式",
    clearFilters: "絞り込みを解除",
    resourceError: "リソースを更新できません",
    appearance: "外観",
    dark: "ダーク",
    light: "ライト",
    system: "システム",
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
    cancel: "キャンセル",
    save: "保存",
    refresh: "更新",
    loading: "読み込み中…",
    on: "オン",
    off: "オフ"
  },
  unsavedGuard: {
    title: "未保存の変更",
    message:
      "このページには未保存の変更があります。移動すると変更は失われます。",
    leave: "破棄して移動"
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
    general: "一般",
    server: "サーバー",
    discard: "元に戻す",
    saveFailed: "保存できませんでした：{error}",
    title: "設定",
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
      tips: "/login を含まない SkyLab サーバーのルート URL。",
      logoutNotice:
        "保存すると、ログアウトして接続を切断してから新しいサーバーに切り替えます。",
      confirmTitle: "バックエンド URL を変更しますか？",
      confirmMessage:
        "SkyLab Connect はログアウトして現在の安全な接続を切断してから、新しいサーバーに切り替えます。再度ログインが必要です。",
      confirmButton: "ログアウトして変更",
      error: {
        required: "バックエンド URL を入力してください。",
        invalid: "URL の形式が正しくありません（例：https://skylab-tw.com）。",
        insecure:
          "https:// を使用してください（ローカルテストのみ http://localhost 可）。",
        extra: "URL に認証情報、クエリ（?）、# を含めることはできません。"
      }
    },
    account: {
      label: "アカウント",
      loggedIn: "ログイン済み",
      notLoggedIn: "未ログイン",
      logout: "ログアウト",
      loginHint:
        "「マイリソース」で「接続」を押すと、ブラウザで SkyLab にログインします。"
    },
    saveSuccess: "保存しました"
  },
  about: {
    licenseTitle: "ライセンスとソースコード",
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
  errors: {
    /* 主程序錯誤碼的說明（electron/core/BusinessError.ts）；support 是共用的回報方式 */
    support:
      "繰り返し発生する場合は、「このアプリについて」の「データフォルダーを開く」から、logs フォルダー内のファイルを管理者に送ってください。",
    B1000: "予期しないエラーが発生しました。@:errors.support",
    B1001: "ログインの有効期限が切れました。もう一度ログインしてください。",
    B1002:
      "ログインが時間内に完了しませんでした。「接続」を押してもう一度お試しください。",
    B1005:
      "サーバーが現在この要求を処理できません。しばらくしてから再試行し、繰り返し発生する場合は管理者に連絡してください。",
    B1006:
      "接続に必要な WireGuard コンポーネントが見つかりません。SkyLab Connect を再インストールしてください。",
    B1007: "このコンピューターの接続キーを読み取れません。@:errors.support",
    B1008:
      "安全な接続を作成できませんでした。もう一度お試しください。@:errors.support",
    B1009:
      "接続に必要な WireGuard コンポーネントをインストールできませんでした。もう一度お試しください。@:errors.support",
    B1010:
      "更新をダウンロードまたはインストールできませんでした。しばらくしてから再試行するか、SkyLab の Web サイトから最新のインストーラーをダウンロードしてください。",
    B1011:
      "安全な接続を変更するには管理者権限が必要です。もう一度お試しいただき、Windows の確認画面で「はい」を選んでください。",
    B1012:
      "Windows で安全な接続の設定を完了できませんでした。もう一度お試しください。@:errors.support",
    B1013:
      "安全な接続の認証の有効期限が切れました。「接続」を押して再接続してください。",
    B1014:
      "前回のセッションの安全な接続が残っています。「接続」を押して作り直してください。",
    B1015:
      "サーバーのネットワーク設定が更新されました。「接続」を押して適用してください。",
    B1016:
      "安全な接続を切断できませんでした。もう一度お試しください。@:errors.support",
    B1017:
      "SkyLab サーバーに接続できません。ネットワーク接続を確認するか、「設定」でバックエンド URL を確認してください。",
    B1018: "開けませんでした。もう一度お試しください。@:errors.support"
  }
};
