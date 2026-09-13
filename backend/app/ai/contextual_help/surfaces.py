"""每個畫面的靜態定義：這一頁在做什麼、有哪些欄位、每個欄位的規則。

三條寫作規則，違反了就是在幫倒忙：

1. **不寫版面位置。** 沒有「右上角」「往下捲」「左側清單」。版面會調整，寫死的
   位置過期之後比沒有位置更糟——使用者會照著一個不存在的地方找。要指認元素就用
   ``id``，要描述分組就用 ``sections`` 的邏輯名稱。
2. **不寫操作順序。** 「接著去防火牆開埠」屬於流程，不屬於畫面說明；那種知識要
   寫在頁面本身的 inline hint 裡。
3. **只寫查得到的事。** ``constraints`` 必須對得上前端真正的驗證規則，否則助手會
   理直氣壯地講錯。

``purpose`` 與 ``sections`` 的內容來自各頁既有的標題、副標與分頁名稱，不是另外
發明的一套說法，這樣畫面改字時比較容易發現這裡也要跟著改。
"""

from __future__ import annotations

from collections.abc import Iterable

from app.ai.contextual_help.schemas import ElementSpec, SurfaceSpec
from app.ai.navigation.catalog import can_access, resolve_user_role
from app.models import User

# ── 申請虛擬機 / 容器 ────────────────────────────────────────────────
# constraints 逐條對應 RequestFormPage.validate()，改動驗證規則時這裡要一起改。
_REQUEST_FORM_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(
        id="request.hostname",
        role="text",
        label="資源名稱",
        help="這台機器在平台上的識別名稱。",
        constraints=(
            "必填",
            "只能使用小寫英文字母、數字與連字符",
            "不能以連字符開頭或結尾",
        ),
    ),
    ElementSpec(
        id="request.os",
        role="select",
        label="作業系統",
        help=(
            "可以套用老師或官方準備好的環境範本，也可以從作業系統映像自行安裝。"
            "需要 GPU、圖形介面或 Windows 時要選虛擬機範本；一般輕量服務"
            "（網站、資料庫、開發環境）用容器範本或映像檔即可。一次申請一台。"
        ),
        constraints=("必填",),
    ),
    ElementSpec(
        id="request.username",
        role="text",
        label="使用者名稱",
        help="登入這台機器要用的帳號。",
        constraints=(
            "虛擬機必填",
            "Windows 範本的登入帳號固定為 Admin，不需要填",
            "LXC 容器的登入帳號固定為 root，不需要填",
        ),
    ),
    ElementSpec(
        id="request.password",
        role="text",
        label="密碼",
        help="登入這台機器要用的密碼，一律由申請人自己輸入。",
        constraints=("必填", "至少 8 個字元"),
        sensitive=True,
    ),
    ElementSpec(
        id="request.cores",
        role="number",
        label="CPU 核心數",
        help="已帶入所選範本的建議規格，可以依需求調整。",
    ),
    ElementSpec(
        id="request.memory",
        role="number",
        label="記憶體 (RAM)",
        help="已帶入所選範本的建議規格，可以依需求調整。",
    ),
    ElementSpec(
        id="request.disk",
        role="number",
        label="硬碟空間 (Disk)",
        help="已帶入所選範本的建議規格，可以依需求調整。",
        constraints=("不可小於範本本身的大小",),
    ),
    ElementSpec(
        id="request.gpu",
        role="select",
        label="選擇 GPU",
        help=(
            "GPU 會依所選的租借時段重新計算可用性，送出前系統還會再做一次即時檢查。"
        ),
        constraints=(
            "所選範本需要 GPU 時必填",
            "要先決定租借時段，才會載入該時段可用的 GPU",
        ),
    ),
    ElementSpec(
        id="request.vgpu",
        role="select",
        label="vGPU 規格",
        help="依需要的 GPU 記憶體選擇，預設是最小的可用規格。",
    ),
    ElementSpec(
        id="request.mode",
        role="toggle",
        label="使用時段模式",
        help=(
            "立即模式在送出申請後就開始部署，不需要選開始時間；"
            "預約模式則要指定起訖時間。"
        ),
    ),
    ElementSpec(
        id="request.start_at",
        role="date",
        label="開始時間",
        constraints=("預約模式必填", "不可超過系統允許的最遠日期"),
    ),
    ElementSpec(
        id="request.end_at",
        role="date",
        label="結束時間",
        constraints=(
            "預約模式必填",
            "必須晚於開始時間",
            "不可超過系統允許的最遠日期",
            "立即模式若有設定結束時間，不可早於現在",
        ),
    ),
    ElementSpec(
        id="request.reason",
        role="textarea",
        label="申請原因",
        help="說明這台機器要拿來做什麼，審核時會看這一欄。",
        constraints=("必填", "至少 10 個字元"),
    ),
    ElementSpec(
        id="request.submit",
        role="button",
        label="送出申請",
        help="送出後由管理員審核，通過才會開始建立。",
    ),
)

# ── 規格調整（資源詳細頁的「規格」分頁）────────────────────────────────
_SPEC_CHANGE_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(
        id="spec.cores",
        role="number",
        label="CPU 核心數",
        help="要調整成的核心數。",
    ),
    ElementSpec(
        id="spec.memory",
        role="number",
        label="記憶體 (RAM)",
        help="要調整成的記憶體大小。",
    ),
    ElementSpec(
        id="spec.reason",
        role="textarea",
        label="調整原因",
        help="管理員審核時會看這一欄。管理員自己調整規格則不需要送申請。",
        constraints=("必填", "至少 10 個字元"),
    ),
)

# ── 反向代理 ─────────────────────────────────────────────────────────
_REVERSE_PROXY_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(
        id="proxy.subdomain",
        role="text",
        label="網址開頭",
        help="自訂的名稱，會接在所選的網址結尾前面。",
    ),
    ElementSpec(
        id="proxy.zone",
        role="select",
        label="網址結尾",
        help="可選的網域由管理員在網域管理設定，這裡只能從已開放的清單挑。",
    ),
    ElementSpec(
        id="proxy.vm",
        role="select",
        label="綁定的 VM",
        help="這個網址要把流量送到哪一台機器。",
    ),
    ElementSpec(
        id="proxy.port",
        role="number",
        label="服務 Port",
        help=(
            "服務在機器裡跑在哪個 Port。常見預設值：Node.js 3000、Flask 5000、"
            "Nginx 80。"
        ),
    ),
    ElementSpec(
        id="proxy.https",
        role="toggle",
        label="安全連線 (https)",
        help="開啟時系統會自動申請免費憑證。",
    ),
)


# ── 系統管理底下的七個設定頁 ───────────────────────────────────────
# 原「系統設定」的分頁，2026-09 各自升格為獨立頁面。label 一律沿用畫面上的文字，
# help 取自各欄既有的 hint / placeholder / 確認訊息。

# ── PVE 連線 ─────────────────────────────────────────────────────────
_PVE_CONNECTIONS_ELEMENTS: tuple[ElementSpec, ...] = (
    # 連線清單上的動作
    ElementSpec(
        id="pve.add_connection", role="button", label="新增連線",
        section="PVE 連線清單",
        help="建立一組 PVE 連線。第一組建立時會自動成為預設連線。",
    ),
    ElementSpec(
        id="pve.connection_test", role="button", label="測試",
        section="PVE 連線清單",
        help="用這組連線目前存的設定實際連一次 PVE，確認連得上。",
    ),
    ElementSpec(
        id="pve.connection_sync", role="button", label="同步",
        section="PVE 連線清單",
        help="從這組 PVE 重新抓一次節點與 Storage 清單，完成後會回報同步到幾個。",
    ),
    ElementSpec(
        id="pve.connection_edit", role="button", label="編輯",
        section="PVE 連線清單", help="修改這組連線的設定。",
    ),
    ElementSpec(
        id="pve.connection_delete", role="button", label="刪除",
        section="PVE 連線清單",
        help="刪除這組連線，它底下的節點與 Storage 記錄會一併移除。",
    ),
    # 連線設定表單
    ElementSpec(
        id="pve.connection_name", role="text", label="名稱",
        section="連線設定", help="這組連線的識別名稱，例：機房A。",
        constraints=("必填",),
    ),
    ElementSpec(
        id="pve.host", role="text", label="Host",
        section="連線設定", help="PVE 的位址，例：192.168.100.2。",
        constraints=("必填",),
    ),
    ElementSpec(
        id="pve.port", role="number", label="Port",
        section="連線設定", constraints=("1 到 65535",),
    ),
    ElementSpec(
        id="pve.api_user", role="text", label="API 使用者",
        section="連線設定", constraints=("必填",),
    ),
    ElementSpec(
        id="pve.password", role="text", label="密碼",
        section="連線設定", sensitive=True,
        help="編輯既有連線時留空表示不變更。",
    ),
    ElementSpec(
        id="pve.api_timeout", role="number", label="API Timeout（秒）",
        section="連線設定",
    ),
    ElementSpec(
        id="pve.verify_ssl", role="toggle", label="驗證 SSL 憑證",
        section="連線設定",
    ),
    ElementSpec(
        id="pve.ca_cert", role="textarea", label="CA 憑證 PEM",
        section="連線設定", help="編輯既有連線時留空表示不變更。",
    ),
    ElementSpec(
        id="pve.enable_connection", role="toggle", label="啟用此連線",
        section="連線設定",
    ),
    ElementSpec(
        id="pve.set_default", role="toggle", label="設為預設連線",
        section="連線設定",
    ),
    ElementSpec(
        id="pve.save_connection", role="button", label="儲存連線",
        section="連線設定",
    ),
    # 此叢集的資源設定
    ElementSpec(
        id="pve.pool_name", role="text", label="Pool 名稱",
        section="此叢集的資源設定",
        help="pool、storage 與網段是各叢集獨立的設定；建立於這組連線的 VM / LXC "
             "會套用這裡的值。",
    ),
    ElementSpec(
        id="pve.task_check_interval", role="number",
        label="任務檢查間隔（秒）", section="此叢集的資源設定",
    ),
    ElementSpec(
        id="pve.local_subnet", role="text", label="內網網段",
        section="此叢集的資源設定", help="例：192.168.100.0/24。",
    ),
    ElementSpec(
        id="pve.default_node", role="text", label="預設節點",
        section="此叢集的資源設定", help="選填，未指定時優先使用這個節點。",
    ),
)

# ── 節點管理 ─────────────────────────────────────────────────────────
_NODES_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(
        id="nodes.enable", role="toggle", label="啟用",
        section="節點管理",
        help="停用後不再接收新 VM，既有 VM 不受影響。",
    ),
    ElementSpec(
        id="nodes.edit", role="button", label="編輯",
        section="節點管理", help="修改這個節點的連線位址、Port 與放置優先度。",
    ),
    ElementSpec(
        id="nodes.primary", role="readonly", label="主節點",
        section="節點管理", help="這組 PVE 連線的主要節點。",
    ),
    ElementSpec(
        id="nodes.online", role="readonly", label="在線",
        section="節點管理", help="同步時 PVE 回報這個節點可連線。",
    ),
    ElementSpec(
        id="nodes.offline", role="readonly", label="離線",
        section="節點管理", help="同步時 PVE 回報這個節點連不上。",
    ),
)

# ── Storage ─────────────────────────────────────────────────────────
_STORAGE_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(
        id="storage.speed_tier", role="select", label="速度等級",
        section="Storage",
        help="NVMe / SSD / HDD / 未知；放置演算法挑選 Storage 時會把速度等級列入排序。",
    ),
    ElementSpec(
        id="storage.user_priority", role="number", label="使用者優先度",
        section="Storage",
        help="數字越小越優先；放置演算法在其他條件相同時用它決定選哪個 Storage。",
    ),
    ElementSpec(
        id="storage.enable", role="toggle", label="啟用",
        section="Storage", help="停用的 Storage 不會被放置演算法選用。",
    ),
    ElementSpec(
        id="storage.shared", role="readonly", label="共享",
        section="Storage",
        help="共享 Storage 同時掛在多個節點上，這裡的變更會套用到所有掛載節點。",
    ),
)

# ── 資源排程 ─────────────────────────────────────────────────────────
_SCHEDULER_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(
        id="scheduler.cpu_overcommit", role="number", label="CPU 超配比",
        section="放置與超配",
    ),
    ElementSpec(
        id="scheduler.disk_overcommit", role="number", label="Disk 超配比",
        section="放置與超配",
    ),
    ElementSpec(
        id="scheduler.peak_cpu_margin", role="number", label="CPU 峰值餘裕",
        section="資源評估閾值",
    ),
    ElementSpec(
        id="scheduler.peak_memory_margin", role="number", label="RAM 峰值餘裕",
        section="資源評估閾值",
    ),
    ElementSpec(
        id="scheduler.loadavg_warn_per_core", role="number",
        label="LoadAvg 警戒 / 核", section="資源評估閾值",
    ),
    ElementSpec(
        id="scheduler.loadavg_max_per_core", role="number",
        label="LoadAvg 上限 / 核", section="資源評估閾值",
    ),
    ElementSpec(
        id="scheduler.loadavg_penalty_weight", role="number",
        label="LoadAvg 懲罰權重", section="資源評估閾值",
    ),
    ElementSpec(
        id="scheduler.resource_weight_cpu", role="number", label="資源權重 CPU",
        section="資源評估閾值",
    ),
    ElementSpec(
        id="scheduler.resource_weight_memory", role="number", label="資源權重 RAM",
        section="資源評估閾值",
    ),
    ElementSpec(
        id="scheduler.resource_weight_disk", role="number", label="資源權重 Disk",
        section="資源評估閾值",
    ),
    ElementSpec(
        id="scheduler.scheduled_boot_batch_size", role="number",
        label="開機批次大小", section="排程開機與時段",
    ),
    ElementSpec(
        id="scheduler.scheduled_boot_batch_interval", role="number",
        label="批次間隔（秒）", section="排程開機與時段",
    ),
    ElementSpec(
        id="scheduler.scheduled_boot_lead_time", role="number",
        label="提前開機（分）", section="排程開機與時段",
    ),
    ElementSpec(
        id="scheduler.window_grace_period", role="number",
        label="時段寬限（分）", section="排程開機與時段",
    ),
    ElementSpec(
        id="scheduler.practice_session_hours", role="number",
        label="練習時段（小時）", section="排程開機與時段",
    ),
    ElementSpec(
        id="scheduler.practice_warning_minutes", role="number",
        label="練習提醒（分）", section="排程開機與時段",
    ),
    ElementSpec(
        id="scheduler.expiry_warning_hours", role="number",
        label="到期提醒（小時）", section="排程開機與時段",
    ),
    ElementSpec(
        id="scheduler.save", role="button", label="儲存排程設定",
        help="一次送出這一頁的全部欄位。",
    ),
)

# ── 治理 ─────────────────────────────────────────────────────────────
_GOVERNANCE_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(
        id="governance.alerts_enabled", role="toggle", label="啟用警告",
        section="資源警告", help="定期檢查叢集/節點/VM 資源使用率。",
    ),
    ElementSpec(
        id="governance.alert_email_enabled", role="toggle", label="Email 通知",
        section="資源警告", help="警告建立時寄送 Email 給管理員。",
    ),
    ElementSpec(
        id="governance.alert_cpu_threshold", role="number", label="CPU 閾值（%）",
        section="資源警告", constraints=("50 到 100",),
    ),
    ElementSpec(
        id="governance.alert_memory_threshold", role="number", label="記憶體閾值（%）",
        section="資源警告", constraints=("50 到 100",),
    ),
    ElementSpec(
        id="governance.alert_disk_threshold", role="number", label="磁碟閾值（%）",
        section="資源警告", constraints=("50 到 100",),
    ),
    ElementSpec(
        id="governance.alert_cooldown_minutes", role="number", label="冷卻期（分鐘）",
        section="資源警告", help="同一目標同一指標在冷卻期內不重發警告。",
        constraints=("1 到 1440",),
    ),
    ElementSpec(
        id="governance.ttl_enabled", role="toggle", label="啟用 TTL 回收",
        section="TTL 生命週期", help="依資源到期日自動通知、關機與排入刪除。",
    ),
    ElementSpec(
        id="governance.expiry_warn_days", role="number", label="到期前通知（天）",
        section="TTL 生命週期", constraints=("1 到 30",),
    ),
    ElementSpec(
        id="governance.expiry_grace_delete_days", role="number", label="刪除寬限期（天）",
        section="TTL 生命週期", help="到期關機後保留幾天才排入刪除佇列。",
        constraints=("0 到 90",),
    ),
    ElementSpec(
        id="governance.idle_detection_enabled", role="toggle", label="啟用閒置偵測",
        section="閒置偵測", help="偵測長期低 CPU 的運行中資源。",
    ),
    ElementSpec(
        id="governance.idle_cpu_threshold_percent", role="number",
        label="閒置 CPU 閾值（%）", section="閒置偵測", constraints=("0.1 到 20",),
    ),
    ElementSpec(
        id="governance.idle_notify_after_hours", role="number",
        label="閒置通知（小時）", section="閒置偵測",
        help="偵測到閒置後持續達此時數才通知擁有者，須小於關機寬限期。",
        constraints=("1 到 720",),
    ),
    ElementSpec(
        id="governance.idle_grace_hours", role="number",
        label="關機寬限期（小時）", section="閒置偵測",
        help="自偵測到閒置起算，持續達此時數仍閒置才自動關機，須大於閒置通知時數。",
        constraints=("1 到 720",),
    ),
    ElementSpec(
        id="governance.workload_advisor_enabled", role="toggle", label="啟用自動判斷",
        section="VM / LXC 自動判斷",
        help="停用後申請表單僅能手動選擇資源類型。",
    ),
    ElementSpec(
        id="governance.mining_detection_enabled", role="toggle", label="啟用挖礦偵測",
        section="反挖礦偵測", help="定期掃描運行中資源的 CPU 特徵。",
    ),
    ElementSpec(
        id="governance.mining_auto_suspend", role="toggle", label="自動存證並暫停",
        section="反挖礦偵測",
        help="關閉後僅建立事件與通知，暫停由管理員手動執行。",
    ),
    ElementSpec(
        id="governance.snapshot_cleanup_enabled", role="toggle", label="啟用快照自動清理",
        section="快照治理", help="超過保留天數的非保護快照將被排程刪除。",
    ),
    ElementSpec(
        id="governance.snapshot_retention_days", role="number", label="保留天數",
        section="快照治理", constraints=("1 到 90",),
    ),
    ElementSpec(
        id="governance.student_snapshot_max_count", role="number", label="學生快照上限",
        section="快照治理", help="不含 skylab-init；達上限需先刪舊快照。",
        constraints=("1 到 10",),
    ),
    ElementSpec(
        id="governance.provision_max_concurrency", role="number", label="併發上限",
        section="克隆併發",
        help="同時執行的 VM/LXC 克隆數上限；變更於下一個排程週期生效。",
        constraints=("1 到 16",),
    ),
    ElementSpec(
        id="governance.save", role="button", label="儲存治理設定",
        help="一次送出這一頁的全部欄位。",
    ),
)

# ── 配額 ─────────────────────────────────────────────────────────────
_QUOTAS_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(
        id="quotas.global", role="list", label="全域預設配額",
        section="全域預設配額",
        help="沒有個人覆寫的使用者一律套用這組上限。調整只影響之後的新增與擴容，"
             "不會回頭處理既有資源。",
    ),
    ElementSpec(
        id="quotas.user", role="select", label="使用者", section="個別使用者覆寫",
        help="輸入姓名或 email 搜尋要設定覆寫的對象。",
    ),
    ElementSpec(
        id="quotas.create", role="button", label="新增配額",
        section="個別使用者覆寫",
        help="欄位會帶入目前的全域預設值，改成這位使用者專屬的上限即可。"
             "勾選「無限制」代表該項目不設上限。",
    ),
    ElementSpec(
        id="quotas.edit", role="button", label="編輯配額",
        section="個別使用者覆寫",
    ),
    ElementSpec(
        id="quotas.delete", role="button", label="刪除配額",
        section="個別使用者覆寫", help="刪除後這位使用者改回套用全域預設值。",
    ),
)

# ── LDAP ─────────────────────────────────────────────────────────────
_LDAP_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(
        id="ldap.enabled", role="toggle", label="啟用 LDAP 登入",
        section="LDAP / Active Directory 登入",
        help="啟用前建議先以「測試連線」驗證設定。",
    ),
    ElementSpec(
        id="ldap.server_uri", role="text", label="伺服器 URI",
        section="LDAP / Active Directory 登入",
        help="例：ldap://dc.example.edu:389 或 ldaps://...。", constraints=("必填",),
    ),
    ElementSpec(
        id="ldap.connect_timeout", role="number", label="連線逾時（秒）",
        section="LDAP / Active Directory 登入", constraints=("1 到 60",),
    ),
    ElementSpec(
        id="ldap.use_starttls", role="toggle", label="使用 StartTLS",
        section="LDAP / Active Directory 登入",
        help="在 ldap:// 連線上升級為加密連線（ldaps:// 不需要）。",
    ),
    ElementSpec(
        id="ldap.bind_password", role="text", label="服務帳號密碼",
        section="服務帳號與使用者搜尋", sensitive=True,
        help="服務帳號的密碼；已設定時留空表示不變更。",
    ),
    ElementSpec(
        id="ldap.user_search_base", role="text", label="使用者搜尋 Base DN",
        section="服務帳號與使用者搜尋", help="例：OU=Users,DC=example,DC=edu。",
    ),
    ElementSpec(
        id="ldap.user_filter_template", role="text", label="使用者過濾範本",
        section="服務帳號與使用者搜尋",
        help="{username} 會代入登入時輸入的帳號，例：(sAMAccountName={username}) 或 (uid={username})。",
    ),
    ElementSpec(
        id="ldap.email_attribute", role="text", label="Email 屬性",
        section="服務帳號與使用者搜尋", help="例：mail。",
    ),
    ElementSpec(
        id="ldap.name_attribute", role="text", label="姓名屬性",
        section="服務帳號與使用者搜尋", help="例：displayName。",
    ),
    ElementSpec(
        id="ldap.auto_create_users", role="toggle", label="自動建立帳號",
        section="帳號建立與角色對映",
        help="關閉後僅已存在的本地帳號可用 LDAP 登入。",
    ),
    ElementSpec(
        id="ldap.teacher_group_dn", role="text", label="教師群組 DN（選填）",
        section="帳號建立與角色對映", help="使用者屬於此群組時建立為 teacher 角色。",
    ),
    ElementSpec(
        id="ldap.admin_group_dn", role="text", label="管理員群組 DN（選填）",
        section="帳號建立與角色對映", help="使用者屬於此群組時建立為 admin 角色。",
    ),
    ElementSpec(
        id="ldap.test_connection", role="button", label="測試連線",
        help="用目前表單的設定實際連一次 LDAP，不會儲存。",
    ),
    ElementSpec(
        id="ldap.save", role="button", label="儲存 LDAP 設定",
    ),
)


# ── 資源管理（全站）─────────────────────────────────────────────────
_RESOURCE_MGMT_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(id="resmgmt.col_name", role="table", label="名稱", section="資源清單"),
    ElementSpec(
        id="resmgmt.col_env_os", role="table", label="環境 / 系統",
        section="資源清單",
    ),
    ElementSpec(id="resmgmt.col_status", role="table", label="狀態", section="資源清單"),
    ElementSpec(id="resmgmt.col_ip", role="table", label="IP 位址", section="資源清單"),
    ElementSpec(
        id="resmgmt.col_expiry", role="table", label="到期日",
        section="到期與節點",
    ),
    ElementSpec(id="resmgmt.col_node", role="table", label="節點", section="到期與節點"),
    ElementSpec(
        id="resmgmt.status_scheduled", role="readonly", label="已排程",
        section="資源清單",
    ),
    ElementSpec(
        id="resmgmt.status_provisioning", role="readonly", label="建立中",
        section="資源清單",
    ),
    ElementSpec(
        id="resmgmt.status_running", role="readonly", label="執行中",
        section="資源清單",
    ),
    ElementSpec(
        id="resmgmt.status_stopped", role="readonly", label="已關機",
        section="資源清單",
    ),
    ElementSpec(
        id="resmgmt.status_failed", role="readonly", label="建立失敗",
        section="資源清單",
    ),
    ElementSpec(
        id="resmgmt.power", role="button", label="電源控制", section="電源操作",
        help="對這台機器送出開機、關機、重啟等指令。機器尚未建立完成或未開機時"
             "有些操作不會生效。",
    ),
    ElementSpec(
        id="resmgmt.terminal", role="button", label="終端機", section="電源操作",
        help="開啟這台機器的終端機連線。",
    ),
    ElementSpec(
        id="resmgmt.console", role="button", label="控制台", section="電源操作",
        help="開啟這台機器的圖形控制台。",
    ),
    ElementSpec(
        id="resmgmt.view_detail", role="button", label="查看詳情",
        section="資源清單", help="進入這台機器的詳細頁。",
    ),
    ElementSpec(
        id="resmgmt.delete", role="button", label="刪除", section="批次操作",
        help="刪除後無法復原，機器上所有資料都會消失。",
    ),
    ElementSpec(
        id="resmgmt.batch_delete", role="button", label="批次刪除",
        section="批次操作",
        help="對所有勾選的虛擬機或容器送出刪除請求，此操作無法復原。",
    ),
    ElementSpec(
        id="resmgmt.group_row", role="list", label="整組管理", section="資源清單",
        help="同一組課程環境的機器會收合成一列，展開後可以逐台操作。",
    ),
)

# ── GPU 管理 ────────────────────────────────────────────────────────
_GPU_MGMT_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(
        id="gpu.col_mapping", role="table", label="Mapping", section="GPU 清單",
        help="PVE 上的 GPU mapping 名稱，申請機器選 GPU 時看到的就是它。",
    ),
    ElementSpec(id="gpu.col_description", role="table", label="描述", section="GPU 清單"),
    ElementSpec(
        id="gpu.col_node_pci", role="table", label="節點 / PCI",
        section="GPU 清單", help="這張卡插在哪個節點的哪個 PCI 位址。",
    ),
    ElementSpec(
        id="gpu.col_available_total", role="table", label="可用 / 總數",
        section="可用與使用中數量",
        help="還能配出去的數量與總數。SR-IOV 的卡以 VF 計算，直通的卡以裝置計算。",
    ),
    ElementSpec(
        id="gpu.col_vm_in_use", role="table", label="使用中 VM",
        section="使用中的 VM", help="目前佔用這張卡的機器。",
    ),
    ElementSpec(
        id="gpu.status_available", role="readonly", label="可用",
        section="可用與使用中數量", help="還有餘裕可以配給新的機器。",
    ),
    ElementSpec(
        id="gpu.status_full", role="readonly", label="已滿載",
        section="可用與使用中數量", help="已經配完，新的申請選不到這張卡。",
    ),
    ElementSpec(
        id="gpu.remove_mapping", role="button", label="移除映射",
        section="GPU 清單", help="把這組 GPU mapping 從平台移除。",
    ),
)

# ── IP 管理 ─────────────────────────────────────────────────────────
_IP_MGMT_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(
        id="ipmgmt.subnet", role="select", label="子網", section="子網設定",
        help="要檢視或設定的網段。",
    ),
    ElementSpec(
        id="ipmgmt.delete_subnet", role="button", label="刪除子網設定",
        section="子網設定",
    ),
    ElementSpec(
        id="ipmgmt.search", role="text", label="搜尋", section="IP 分配",
        help="可用 IP、VMID 或備註搜尋。",
    ),
)

# ── 背景任務 ────────────────────────────────────────────────────────
_JOBS_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(id="jobs.status_pending", role="readonly", label="等待中", section="進行中"),
    ElementSpec(id="jobs.status_running", role="readonly", label="執行中", section="進行中"),
    ElementSpec(
        id="jobs.status_blocked", role="readonly", label="受阻", section="進行中",
        help="任務卡住了，還沒有完成也還沒有失敗。",
    ),
    ElementSpec(id="jobs.status_completed", role="readonly", label="已完成", section="已完成"),
    ElementSpec(
        id="jobs.status_failed", role="readonly", label="失敗", section="失敗",
        help="任務執行出錯，展開可以看到錯誤內容。",
    ),
    ElementSpec(id="jobs.status_cancelled", role="readonly", label="已取消", section="已完成"),
)

# ── 稽核日誌 ────────────────────────────────────────────────────────
_AUDIT_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(
        id="audit.action_filter", role="select", label="操作", section="紀錄清單",
        help="依操作類型篩選，「全部操作」代表不篩選。",
    ),
    ElementSpec(
        id="audit.search", role="text", label="搜尋", section="紀錄清單",
        help="可用操作內容或 IP 搜尋。",
    ),
)


# ── 我的資源 ────────────────────────────────────────────────────────
_MY_RESOURCES_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(id="myres.status_scheduled", role="readonly", label="已排程", section="機器清單"),
    ElementSpec(id="myres.status_provisioning", role="readonly", label="建立中", section="機器清單"),
    ElementSpec(id="myres.status_running", role="readonly", label="執行中", section="機器清單"),
    ElementSpec(id="myres.status_stopped", role="readonly", label="已關機", section="機器清單"),
    ElementSpec(id="myres.status_paused", role="readonly", label="已暫停", section="機器清單"),
    ElementSpec(
        id="myres.status_partial_failed", role="readonly", label="需要處理",
        section="機器清單",
    ),
    ElementSpec(
        id="myres.status_stopping", role="readonly", label="準備回收",
        section="機器清單",
    ),
    ElementSpec(id="myres.status_reclaiming", role="readonly", label="回收中", section="機器清單"),
    ElementSpec(id="myres.status_deleting", role="readonly", label="刪除中", section="刪除申請"),
    ElementSpec(id="myres.status_failed", role="readonly", label="建立失敗", section="機器清單"),
    ElementSpec(id="myres.status_deleted", role="readonly", label="已刪除", section="刪除申請"),
    ElementSpec(
        id="myres.status_unknown", role="readonly", label="狀態未知",
        section="機器清單", help="平台目前讀不到這台機器在 PVE 上的狀態。",
    ),
    ElementSpec(id="myres.actions", role="button", label="動作", section="電源操作"),
)

# ── 我的申請 ────────────────────────────────────────────────────────
_MY_REQUESTS_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(id="myreq.status_pending", role="readonly", label="審核中", section="審核狀態"),
    ElementSpec(id="myreq.status_approved", role="readonly", label="已核准", section="審核狀態"),
    ElementSpec(
        id="myreq.status_rejected", role="readonly", label="已拒絕",
        section="審核狀態", help="被退回時會附上審核者填的原因。",
    ),
    ElementSpec(id="myreq.status_cancelled", role="readonly", label="已取消", section="審核狀態"),
    ElementSpec(id="myreq.status_expired", role="readonly", label="已過期", section="審核狀態"),
    ElementSpec(id="myreq.status_provisioning", role="readonly", label="開通中", section="申請清單"),
    ElementSpec(id="myreq.status_provisioned", role="readonly", label="已開通", section="申請清單"),
    ElementSpec(
        id="myreq.status_provision_failed", role="readonly", label="開通失敗",
        section="申請清單",
    ),
    ElementSpec(
        id="myreq.status_waiting_resources", role="readonly",
        label="等待資源釋出", section="申請清單",
        help="已經核准，但目前沒有足夠的資源可以開通，要等其他機器釋出。",
    ),
    ElementSpec(
        id="myreq.status_machine_error", role="readonly", label="機器異常",
        section="申請清單",
    ),
    ElementSpec(
        id="myreq.retry", role="button", label="重試", section="取消與重試",
        help="開通失敗的申請可以重新送進開通流程。",
    ),
    ElementSpec(
        id="myreq.cancel", role="button", label="取消", section="取消與重試",
        help="尚未進入開通階段的申請才能取消。",
    ),
)

# ── 帳號設定 ────────────────────────────────────────────────────────
_ACCOUNT_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(id="account.tab_profile", role="list", label="個人資料", section="個人資料"),
    ElementSpec(id="account.tab_password", role="list", label="密碼", section="密碼"),
    ElementSpec(
        id="account.tab_appearance", role="list", label="外觀", section="外觀",
        help="調整介面主題與顯示語言。",
    ),
    ElementSpec(
        id="account.tab_danger", role="list", label="危險區域", section="危險區域",
        help="不可復原的帳號操作放在這一區。",
    ),
)

# ── AI API ──────────────────────────────────────────────────────────
_AI_API_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(
        id="aiapi.key_name", role="text", label="金鑰名稱", section="申請",
        help="給自己辨認用的名稱，例如：課程專案用、測試用、我的 App。",
    ),
    ElementSpec(
        id="aiapi.purpose", role="textarea", label="申請目的", section="申請",
        help="說明要拿這把金鑰做什麼，審核時會看這一欄。",
    ),
    ElementSpec(
        id="aiapi.duration", role="select", label="金鑰有效期限", section="申請",
        help="可選 1 小時、1 天、1 週、1 個月或永不過期。",
    ),
    ElementSpec(
        id="aiapi.action_show", role="button", label="顯示", section="申請紀錄",
        help="顯示這把金鑰的完整內容。",
    ),
    ElementSpec(id="aiapi.action_hide", role="button", label="隱藏", section="申請紀錄"),
    ElementSpec(
        id="aiapi.action_refresh", role="button", label="刷新", section="申請紀錄",
        help="重新產生這把 API Key；舊的會失效。",
    ),
    ElementSpec(
        id="aiapi.action_delete", role="button", label="刪除", section="申請紀錄",
        help="刪除這把 API Key。",
    ),
    ElementSpec(
        id="aiapi.usage_proxy", role="chart", label="Proxy 用量", section="我的用量",
        help="直接呼叫 AI API 的 Token 用量。",
    ),
    ElementSpec(
        id="aiapi.usage_template", role="chart", label="Template 用量",
        section="我的用量", help="使用 AI Template API 的 Token 用量。",
    ),
)

# ── 機器範本 ────────────────────────────────────────────────────────
_TEMPLATES_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(id="templates.col_vmid", role="table", label="VMID", section="範本清單"),
    ElementSpec(id="templates.col_type", role="table", label="類型", section="範本清單"),
    ElementSpec(
        id="templates.col_visibility", role="table", label="可見範圍",
        section="可見範圍",
        help="設為「全部可見」後，學生就能在申請表單的資源設定選用；"
             "「私人」只有自己看得到。",
    ),
    ElementSpec(id="templates.col_version", role="table", label="版本", section="版本"),
    ElementSpec(
        id="templates.pve_missing", role="readonly", label="PVE 不存在",
        section="範本清單",
        help="PVE 端找不到這個範本，可能已被手動刪除。",
    ),
    ElementSpec(
        id="templates.menu_clone", role="button", label="克隆開通",
        section="建立與轉換", help="從這個範本複製出一台可以用的機器。",
    ),
    ElementSpec(
        id="templates.menu_edit", role="button", label="編輯 / 可見範圍",
        section="可見範圍",
    ),
    ElementSpec(
        id="templates.menu_manual", role="button", label="使用手冊",
        section="範本清單", help="查看與下載這個範本附的說明文件。",
    ),
    ElementSpec(
        id="templates.menu_retry", role="button", label="重新轉換",
        section="建立與轉換", help="轉換失敗時重新把來源機器轉成範本。",
    ),
    ElementSpec(
        id="templates.menu_start_cycle", role="button", label="開始更新循環",
        section="版本",
        help="把範本開回成一台可以修改的機器，改完再轉成新版。",
    ),
    ElementSpec(
        id="templates.menu_finish_cycle", role="button",
        label="完成更新（轉為新版）", section="版本",
        help="關機並把修改後的機器轉成新版範本。",
    ),
    ElementSpec(
        id="templates.menu_cancel_cycle", role="button", label="取消更新循環",
        section="版本",
    ),
    ElementSpec(
        id="templates.menu_delete", role="button", label="刪除範本",
        section="範本清單",
        help="PVE 端的範本磁碟會一併刪除，動作無法復原。如果還有從這個範本"
             "克隆出的機器（linked clone），系統會拒絕刪除。",
    ),
)


# ── 班級管理 ────────────────────────────────────────────────────────
_CLASS_MGMT_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(
        id="classmgmt.status_planning", role="readonly", label="準備中",
        section="班級總覽", help="還在準備，尚未送出機器配置。",
    ),
    ElementSpec(
        id="classmgmt.status_pending_review", role="readonly", label="等待審核",
        section="班級總覽", help="機器配置已送出，等管理員審核。",
    ),
    ElementSpec(
        id="classmgmt.status_provisioning", role="readonly", label="正在建立",
        section="上課環境",
    ),
    ElementSpec(
        id="classmgmt.status_partial_failed", role="readonly", label="需要處理",
        section="上課環境", help="有機器沒有成功建立，要處理過才能正常上課。",
    ),
    ElementSpec(
        id="classmgmt.status_active", role="readonly", label="可以上課",
        section="班級總覽",
    ),
    ElementSpec(
        id="classmgmt.status_archived", role="readonly", label="已結束",
        section="班級總覽",
    ),
    ElementSpec(
        id="classmgmt.filter_planning", role="button", label="準備中",
        section="班級總覽", help="篩選還沒送出機器配置的班級。",
    ),
    ElementSpec(
        id="classmgmt.filter_building", role="button", label="建置中",
        section="班級總覽",
        help="篩選已送出、正在等待審核或正在建立機器的班級；"
             "這段期間老師不需要做任何事。",
    ),
    ElementSpec(
        id="classmgmt.filter_partial_failed", role="button", label="需要處理",
        section="班級總覽",
        help="只有在真的有班級建機失敗時才會出現；點進去可以重試或退回編輯。",
    ),
    ElementSpec(
        id="classmgmt.filter_active", role="button", label="可以上課",
        section="班級總覽", help="篩選機器都建好、可以開始上課的班級。",
    ),
    ElementSpec(
        id="classmgmt.show_archived", role="button", label="顯示已結束",
        section="班級總覽",
        help="已結束的班級預設不列出，也不計入其他分頁的數字。",
    ),
    ElementSpec(
        id="classmgmt.boot_lead", role="readonly", label="提前開機",
        section="上課環境", help="機器會在上課時段前先開好。",
    ),
    ElementSpec(
        id="classmgmt.shutdown_grace", role="readonly", label="下課後關機",
        section="上課環境",
        help="下課後機器會多留這段時間才自動關機，讓學生有時間收尾。",
    ),
    ElementSpec(
        id="classmgmt.class_info_fold", role="button", label="班級資訊",
        section="班級總覽",
        help="班級總覽上一行收合的課表摘要，點開會展開學期、上課地點、"
             "課程期間、提前開機與下課後關機。",
    ),
    ElementSpec(
        id="classmgmt.more_menu", role="button", label="更多班級操作",
        section="班級總覽",
        help="頁首的 ⋯ 按鈕。編輯班級與課表、延長課程日期、封存並回收"
             "都收在這裡。",
    ),
    ElementSpec(
        id="classmgmt.extend_class", role="button", label="延長課程日期",
        section="班級總覽",
        help="在 ⋯ 選單裡。延長後會自動補上新的課次，機器到期日一併順延。",
    ),
    ElementSpec(
        id="classmgmt.archive_class", role="button", label="封存並回收",
        section="班級總覽",
        help="在 ⋯ 選單裡，動作不可逆：班級會結束，班上的機器會被刪除。"
             "回收失敗時可以從班級總覽的狀態面板重試。",
    ),
)

# ── 建立班級 ────────────────────────────────────────────────────────
_CLASS_SETUP_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(
        id="classsetup.step_basic", role="list", label="班級與課表", section="課表",
        help="先決定何時上課；代碼、時區與提前開機可以維持預設。",
    ),
    ElementSpec(
        id="classsetup.class_name", role="text", label="班級名稱", section="課表",
        help="例如：Linux Web 實務｜115-1。",
    ),
    ElementSpec(
        id="classsetup.location", role="text", label="上課地點", section="課表",
        help="例如：電腦教室 A，會顯示在學生的今日課表。",
    ),
    ElementSpec(
        id="classsetup.step_students", role="list", label="學生名單", section="學生",
        help="加入正式班級成員；建立名單後才能開通機器。",
    ),
    ElementSpec(
        id="classsetup.step_environment", role="list", label="教學環境", section="環境",
        help="選擇每位學生會拿到的機器。",
    ),
    ElementSpec(
        id="classsetup.step_tasks", role="list", label="每週任務", section="每週任務",
        help="安排 checkpoint 與每週內容。",
    ),
    ElementSpec(
        id="classsetup.step_review", role="list", label="確認建立", section="確認建立",
        help="做容量預檢後送出。送出後學生、環境與課表會鎖定並進入管理員審核。",
    ),
)

# ── 學習環境 ────────────────────────────────────────────────────
_COURSE_TPL_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(
        id="coursetpl.create", role="button", label="建立學習環境",
        section="模板清單",
    ),
    ElementSpec(id="coursetpl.status_published", role="readonly", label="已發布", section="模板清單"),
    ElementSpec(id="coursetpl.status_draft", role="readonly", label="草稿", section="模板清單"),
    ElementSpec(
        id="coursetpl.status_retired", role="readonly", label="已停用",
        section="模板清單",
    ),
    ElementSpec(
        id="coursetpl.retire", role="button", label="下架", section="模板清單",
        help="下架後不再提供給新的課程或練習選用。",
    ),
    ElementSpec(id="coursetpl.delete", role="button", label="刪除", section="模板清單"),
    ElementSpec(
        id="coursetpl.tab_basic", role="list", label="基本資料", section="基本資料",
    ),
    ElementSpec(
        id="coursetpl.tab_machines", role="list", label="機器配置", section="機器配置",
        help="定義這組環境包含哪些機器；每位學生最多三台。",
    ),
)

# ── 課程管理 ────────────────────────────────────────────────────────
_COURSE_CMS_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(
        id="cms.class_link", role="select", label="這份內容屬於哪個班級",
        section="內容編輯",
        help="連結後，學生首頁、課堂機器與 AI 任務才會對應到同一班。",
    ),
    ElementSpec(
        id="cms.new_path", role="text", label="新路徑標題", section="內容編輯",
        help="學習路徑是最外層的分組。",
    ),
    ElementSpec(
        id="cms.new_room", role="text", label="新房間標題", section="內容編輯",
        help="房間掛在學習路徑底下，可以綁定一個實驗模板。",
    ),
    ElementSpec(
        id="cms.pure_theory", role="toggle", label="純理論", section="內容編輯",
        help="純理論的房間不綁機器。",
    ),
    ElementSpec(
        id="cms.publish", role="button", label="發布", section="內容編輯",
        help="發布後學生才看得到。",
    ),
    ElementSpec(id="cms.unpublish", role="button", label="下架", section="內容編輯"),
    ElementSpec(id="cms.status_published", role="readonly", label="已發布", section="內容編輯"),
    ElementSpec(id="cms.status_draft", role="readonly", label="草稿", section="內容編輯"),
)

# ── 使用者管理 ──────────────────────────────────────────────────────
_ADMIN_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(id="admin.create_user", role="button", label="新增使用者", section="使用者清單"),
    ElementSpec(id="admin.edit_user", role="button", label="編輯", section="使用者清單"),
    ElementSpec(
        id="admin.delete_user", role="button", label="刪除", section="使用者清單",
        help="不能刪除自己的帳號。",
    ),
    ElementSpec(
        id="admin.password", role="text", label="密碼", section="使用者清單",
        sensitive=True, help="編輯既有使用者時留空表示不變更。",
        constraints=("至少 8 個字元",),
    ),
    ElementSpec(id="admin.status_active", role="readonly", label="啟用", section="啟用狀態"),
    ElementSpec(id="admin.status_inactive", role="readonly", label="停用", section="啟用狀態"),
)

# ── 網域管理 ────────────────────────────────────────────────────────
_DOMAIN_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(
        id="domain.api_token", role="text", label="API Token", section="供應商連線",
        sensitive=True,
        help="需具備 Zone / DNS 編輯權限。Token 只寫入不回讀，已設定時留空代表不變更。",
    ),
    ElementSpec(
        id="domain.record_name", role="text", label="紀錄名稱", section="DNS record",
        help="例：www 或 www.example.com。",
    ),
    ElementSpec(
        id="domain.record_content", role="text", label="紀錄內容",
        section="DNS record", help="例：140.131.x.x 或 gw.example.com。",
    ),
    ElementSpec(
        id="domain.proxied", role="toggle", label="經由 Cloudflare Proxy（橘色雲）",
        section="DNS record",
    ),
    ElementSpec(
        id="domain.ttl", role="number", label="TTL", section="DNS record",
        help="設 1 代表 Auto。",
    ),
    ElementSpec(
        id="domain.delete_record", role="button", label="刪除 DNS 紀錄",
        section="DNS record",
    ),
)

# ── 閘道 VM ─────────────────────────────────────────────────────────
_GATEWAY_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(
        id="gateway.ssh_connection", role="list", label="SSH 連線設定",
        section="連線設定", help="平台要透過 SSH 管理 Gateway VM 上的服務。",
    ),
    ElementSpec(
        id="gateway.ssh_public_key", role="readonly", label="SSH 公鑰",
        section="連線設定",
        help="把這把公鑰加入 Gateway VM 的 authorized_keys，平台才能透過 SSH 管理服務。",
    ),
    ElementSpec(
        id="gateway.regenerate_keypair", role="button", label="重新產生 Keypair",
        section="連線設定", help="產生新的金鑰對，舊的公鑰會失效。",
    ),
    ElementSpec(
        id="gateway.reset_host_key", role="button", label="重設 Host Key",
        section="連線設定",
        help="Gateway VM 重灌後 host key 變更導致連線被拒時使用。",
    ),
    ElementSpec(
        id="gateway.reload_config", role="button", label="重新載入設定檔",
        section="服務狀態",
    ),
    ElementSpec(
        id="gateway.service_logs", role="readonly", label="服務日誌",
        section="服務狀態", help="顯示最近 100 行。",
    ),
    ElementSpec(id="gateway.status_running", role="readonly", label="運行中", section="服務狀態"),
    ElementSpec(id="gateway.status_stopped", role="readonly", label="已停止", section="服務狀態"),
    ElementSpec(
        id="gateway.status_unavailable", role="readonly", label="無法取得狀態",
        section="服務狀態", help="平台連不到 Gateway VM，讀不到服務狀態。",
    ),
)

# ── 資源監控 ────────────────────────────────────────────────────────
_MONITORING_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(
        id="monitoring.active_alerts", role="list", label="活動警告",
        section="閾值警告", help="超過閾值的資源使用警告，每 30 秒更新。",
    ),
    ElementSpec(
        id="monitoring.node_usage", role="list", label="節點用量",
        section="節點趨勢", help="點擊節點列可以展開使用趨勢圖。",
    ),
    ElementSpec(id="monitoring.top_cpu", role="chart", label="CPU 用量 Top 5", section="叢集用量"),
    ElementSpec(id="monitoring.top_mem", role="chart", label="記憶體用量 Top 5", section="叢集用量"),
)

# ── AI API 金鑰管理 ─────────────────────────────────────────────────
_AI_API_KEYS_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(
        id="aikeys.filter", role="select", label="篩選", section="金鑰清單",
        help="可依狀態與使用者 Email 篩選。",
    ),
    ElementSpec(id="aikeys.status_active", role="readonly", label="啟用", section="啟用與失效"),
    ElementSpec(id="aikeys.status_inactive", role="readonly", label="失效", section="啟用與失效"),
    ElementSpec(
        id="aikeys.delete", role="button", label="刪除", section="金鑰清單",
        help="刪除這把金鑰，動作無法復原。",
    ),
)

# ── AI 使用監控 ─────────────────────────────────────────────────────
_AI_MONITORING_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(
        id="aimon.tab_proxy", role="list", label="Proxy 呼叫", section="Proxy 呼叫",
        help="直接呼叫 AI API 的紀錄。",
    ),
    ElementSpec(
        id="aimon.tab_template", role="list", label="Template 呼叫",
        section="Template 呼叫", help="平台內建 AI 功能的呼叫紀錄。",
    ),
    ElementSpec(
        id="aimon.tab_users", role="list", label="使用者用量",
        section="使用者用量",
    ),
    ElementSpec(id="aimon.stat_calls", role="readonly", label="呼叫次數", section="Proxy 呼叫"),
    ElementSpec(id="aimon.stat_tokens", role="readonly", label="Tokens 總計", section="Proxy 呼叫"),
    ElementSpec(id="aimon.stat_success", role="readonly", label="成功率", section="Proxy 呼叫"),
    ElementSpec(id="aimon.stat_latency", role="readonly", label="平均延遲", section="Proxy 呼叫"),
)


# ── 防火牆 ──────────────────────────────────────────────────────────
_FIREWALL_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(
        id="firewall.topology", role="list", label="拓撲圖", section="拓撲圖",
        help="機器與連線規則會畫在這張圖上；建立 VM 之後才會出現節點。",
    ),
    ElementSpec(
        id="firewall.add_connection", role="button", label="新增連線",
        section="連線規則",
        help="也可以直接把一個節點拖到另一個節點上建立連線。",
    ),
    ElementSpec(
        id="firewall.connection_labels", role="toggle", label="連線標籤",
        section="連線規則", help="切換是否在線上顯示規則說明。",
    ),
    ElementSpec(
        id="firewall.auto_arrange", role="button", label="自動排列",
        section="拓撲圖", help="重新排列節點，只影響檢視，不改動規則。",
    ),
    ElementSpec(
        id="firewall.delete_connection", role="button", label="刪除",
        section="連線規則", help="刪除這條連線規則。",
    ),
)

# ── 啟動快速練習 ────────────────────────────────────────────────────
_QUICK_TEMPLATE_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(
        id="quick.rule_fixed_config", role="readonly", label="固定配置",
        section="環境說明",
        help="套用老師發布的完整環境，不提供規格選擇；學生不能修改 CPU、"
             "記憶體、磁碟或機器數量。",
    ),
    ElementSpec(
        id="quick.rule_no_review", role="readonly", label="不用等待審核",
        section="環境說明", help="整組自動核准，送出後立即排入建立流程。",
    ),
    ElementSpec(
        id="quick.rule_duration", role="readonly", label="時數限制",
        section="環境說明", help="時間到由系統依練習政策停止環境。",
    ),
    ElementSpec(
        id="quick.machines", role="list", label="本次會建立的機器",
        section="機器配置", help="這組環境包含的每一台機器與它的規格。",
    ),
    ElementSpec(
        id="quick.environment_total", role="readonly", label="環境合計",
        section="機器配置", help="整組機器加起來會用掉的 CPU、記憶體與磁碟。",
    ),
    ElementSpec(
        id="quick.launch", role="button", label="啟動", section="啟動",
        help="送出後會一次建立整組機器。",
    ),
)

# ── 申請審核 ────────────────────────────────────────────────────────
_REQUEST_REVIEW_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(
        id="reqreview.type", role="readonly", label="申請類型", section="待審核",
        help="這裡集中三種申請：建立申請、規格調整與刪除請求。"
             "刪除請求只供檢視，不在這裡核准。",
    ),
    ElementSpec(
        id="reqreview.spec", role="readonly", label="規格 / 摘要", section="待審核",
        help="建立申請顯示要開的規格；規格調整顯示從什麼改成什麼。",
    ),
    ElementSpec(
        id="reqreview.node", role="readonly", label="節點", section="待審核",
        help="評估後預計配到哪個節點。",
    ),
    ElementSpec(
        id="reqreview.feasibility", role="readonly", label="可行性評估",
        section="待審核",
        help="建立申請會先算一次是否放得下。評估不可行時不能核准。",
    ),
    ElementSpec(
        id="reqreview.comment", role="textarea", label="審核備註", section="待審核",
        help="可填核准原因或退回說明，申請者看得到。",
    ),
    ElementSpec(id="reqreview.approve", role="button", label="核准", section="待審核"),
    ElementSpec(id="reqreview.reject", role="button", label="拒絕", section="待審核"),
    ElementSpec(
        id="reqreview.status_expired", role="readonly", label="已過期",
        section="已過期", help="申請的使用時段已經過去，不再需要審核。",
    ),
)

# ── 班級審核 ────────────────────────────────────────────────────────
_BATCH_REVIEW_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(
        id="batchreview.type", role="readonly", label="申請類型", section="待審核",
        help="班級批次代表同一個班級的多個機器節點，核准時一起處理。",
    ),
    ElementSpec(
        id="batchreview.progress", role="readonly", label="建立進度",
        section="已通過", help="核准後這一批機器已經建立了幾台、失敗了幾台。",
    ),
    ElementSpec(
        id="batchreview.recurrence", role="readonly", label="週期排程",
        section="待審核",
        help="這批機器會依課表週期性開機；可以展開看未來幾個開機時段再決定。",
    ),
    ElementSpec(
        id="batchreview.members", role="list", label="批次成員", section="待審核",
        help="這一批會替哪些學生建立機器。",
    ),
    ElementSpec(
        id="batchreview.comment", role="textarea", label="審核備註",
        section="待審核", help="可填核准原因或退回說明。",
    ),
    ElementSpec(id="batchreview.approve", role="button", label="核准", section="待審核"),
    ElementSpec(id="batchreview.reject", role="button", label="駁回", section="待審核"),
)

# ── AI API 審核 ─────────────────────────────────────────────────────
_AI_API_REVIEW_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(
        id="aireview.col_key_name", role="table", label="金鑰名稱",
        section="待審核",
    ),
    ElementSpec(
        id="aireview.col_purpose", role="table", label="用途", section="待審核",
        help="申請者填的使用目的，審核主要看這一欄。",
    ),
    ElementSpec(
        id="aireview.comment", role="textarea", label="審核備註",
        section="待審核", help="可留空；拒絕時填的原因會讓申請者知道下一步。",
    ),
    ElementSpec(
        id="aireview.approve", role="button", label="通過", section="待審核",
        help="通過後系統會直接核發可用的 base_url 與 api_key。",
    ),
    ElementSpec(id="aireview.reject", role="button", label="拒絕", section="待審核"),
    ElementSpec(
        id="aireview.not_reviewed", role="readonly", label="尚未審核",
        section="待審核",
    ),
)

# ── 首頁 ────────────────────────────────────────────────────────────
_DASHBOARD_ELEMENTS: tuple[ElementSpec, ...] = (
    ElementSpec(
        id="dashboard.issues", role="list", label="待確認的問題",
        section="總覽卡片",
        help="管理者身分會看到目前需要優先處理的事項，點進去就是對應的頁面。",
    ),
    ElementSpec(
        id="dashboard.assistant", role="button", label="維運助手",
        section="快速入口", help="管理者可以直接在首頁詢問維運助手。",
    ),
    ElementSpec(
        id="dashboard.courses", role="list", label="課程與練習",
        section="總覽卡片", help="學生身分會看到自己的課程關卡與練習環境。",
    ),
    ElementSpec(
        id="dashboard.checkpoints", role="list", label="學生完成度",
        section="總覽卡片",
        help="教師身分會看到學生的 checkpoint 完成度與近期課堂。",
    ),
)


_SURFACES: tuple[SurfaceSpec, ...] = (
    # ── 所有登入者 ──
    SurfaceSpec(
        id="dashboard",
        path="/dashboard",
        title="首頁",
        purpose=(
            "依身分顯示不同的總覽：學生看課程與練習關卡，教師看學生完成度與近期"
            "課堂，管理者看目前需要優先確認的問題。"
        ),
        sections=("總覽卡片", "快速入口"),
        elements=_DASHBOARD_ELEMENTS,
    ),
    SurfaceSpec(
        id="my-resources",
        path="/my-resources",
        title="我的資源",
        purpose="查看與管理申請通過的虛擬機和容器，包含開關機、連線與提出刪除。",
        sections=("機器清單", "電源操作", "連線方式", "刪除申請"),
        elements=_MY_RESOURCES_ELEMENTS,
    ),
    SurfaceSpec(
        id="resource-detail",
        path="/my-resources/:vmid",
        title="資源詳細",
        purpose=(
            "單一台機器的完整資訊與操作，也是提出規格調整申請的地方。"
            "規格調整送出後要等管理員審核；管理員可以直接調整，不必送申請。"
        ),
        sections=("總覽", "監控", "規格", "快照", "操作紀錄", "進階設定"),
        elements=_SPEC_CHANGE_ELEMENTS,
    ),
    SurfaceSpec(
        id="my-requests",
        path="/my-requests",
        title="我的申請",
        purpose="管理你的虛擬機與容器申請，並追蹤審核與建立進度。",
        sections=("申請清單", "審核狀態", "取消與重試"),
        elements=_MY_REQUESTS_ELEMENTS,
    ),
    SurfaceSpec(
        id="request-form",
        path="/my-requests",
        title="申請虛擬機 / 容器",
        purpose=(
            "填寫申請表單後送出，管理員審核通過後系統會自動建立資源。"
            "帳號與密碼一律由申請人自己輸入。"
        ),
        sections=("資源設定", "硬體配置", "使用時段", "申請原因"),
        elements=_REQUEST_FORM_ELEMENTS,
    ),
    SurfaceSpec(
        id="quick-template-form",
        path="/quick-template/:id",
        title="啟動快速練習",
        purpose=(
            "用固定配置一次建立整組練習機器，免人工審核，送出後直接開始建立。"
        ),
        sections=("環境說明", "機器配置", "啟動"),
        elements=_QUICK_TEMPLATE_ELEMENTS,
    ),
    SurfaceSpec(
        id="account",
        path="/account",
        title="帳號設定",
        purpose="修改個人資料與密碼，調整介面外觀。",
        sections=("個人資料", "密碼", "外觀", "危險區域"),
        elements=_ACCOUNT_ELEMENTS,
    ),
    SurfaceSpec(
        id="jobs",
        path="/jobs",
        title="背景任務",
        purpose="追蹤部署、申請與資源配置等長時間執行的任務，失敗的任務會顯示錯誤。",
        sections=("進行中", "已完成", "失敗"),
        elements=_JOBS_ELEMENTS,
    ),
    SurfaceSpec(
        id="firewall",
        path="/firewall",
        title="防火牆",
        purpose=(
            "以拓撲圖管理機器之間與對外的網路連線規則，"
            "在節點之間拉出連線就是建立一條規則。"
        ),
        sections=("拓撲圖", "連線規則"),
        elements=_FIREWALL_ELEMENTS,
    ),
    SurfaceSpec(
        id="reverse-proxy",
        path="/reverse-proxy",
        title="反向代理",
        purpose=(
            "讓別人透過一個好記的網址訪問你機器裡的網站或服務。"
            "前提是服務已經在機器裡跑起來，而且你知道它在哪個 Port；"
            "可用的網址結尾由管理員先在網域管理設定。"
        ),
        sections=("網址清單", "新增網址"),
        elements=_REVERSE_PROXY_ELEMENTS,
    ),
    SurfaceSpec(
        id="ai-api",
        path="/ai-api",
        title="AI API",
        purpose="申請 AI API 金鑰、查詢申請紀錄與個人 token 用量。",
        sections=("申請", "申請紀錄", "我的用量"),
        elements=_AI_API_ELEMENTS,
    ),
    # ── 教師與管理者 ──
    SurfaceSpec(
        id="templates",
        path="/templates",
        title="機器範本",
        purpose=(
            "把設定好的母機轉成範本。可見範圍設為「全部可見」後，"
            "學生就能在申請表單的資源設定裡選用。"
        ),
        sections=("範本清單", "建立與轉換", "可見範圍", "版本"),
        elements=_TEMPLATES_ELEMENTS,
        access="staff",
    ),
    SurfaceSpec(
        id="class-management",
        path="/class-management",
        title="班級管理",
        purpose="從尚未完成的班級繼續準備，或進入已就緒的班級開始上課。",
        sections=(
            "班級總覽", "加入學生", "上課環境", "每週內容",
            "上課監看", "資源熱力圖", "AI 檢查",
        ),
        access="staff",
        elements=_CLASS_MGMT_ELEMENTS,
    ),
    SurfaceSpec(
        id="class-setup",
        path="/class-setup",
        title="建立班級",
        purpose="依序完成課表、學生、環境與每週任務，每一步都會保存到正式班級。",
        sections=("課表", "學生", "環境", "每週任務", "確認建立"),
        access="staff",
        elements=_CLASS_SETUP_ELEMENTS,
    ),
    SurfaceSpec(
        id="course-template-management",
        path="/course-template-management",
        title="學習環境",
        purpose=(
            "定義一組固定的機器配置，提供給正式課程、快速練習或兩者共用。"
            "每位學生最多三台機器。"
        ),
        sections=("模板清單", "基本資料", "機器配置"),
        access="staff",
        elements=_COURSE_TPL_ELEMENTS,
    ),
    SurfaceSpec(
        id="course-cms",
        path="/course-cms",
        title="課程管理",
        purpose=(
            "建立學習路徑、房間（綁定實驗模板）與任務 Flag 題目；"
            "發布之後學生才看得到。"
        ),
        sections=("內容編輯", "學生進度"),
        access="staff",
        elements=_COURSE_CMS_ELEMENTS,
    ),
    # ── 僅管理者 ──
    SurfaceSpec(
        id="resource-mgmt",
        path="/resource-mgmt",
        title="資源管理",
        purpose="查看與管理系統中所有的虛擬機與 LXC 容器。",
        sections=("資源清單", "電源操作", "到期與節點", "批次操作"),
        elements=_RESOURCE_MGMT_ELEMENTS,
        access="admin",
    ),
    SurfaceSpec(
        id="request-review",
        path="/request-review",
        title="申請審核",
        purpose=(
            "集中審核建立申請、規格調整與刪除請求。"
            "AI API 金鑰申請有自己的審核頁，不在這裡。"
        ),
        sections=("待審核", "已通過", "已拒絕", "已過期", "全部"),
        access="admin",
        elements=_REQUEST_REVIEW_ELEMENTS,
    ),
    SurfaceSpec(
        id="batch-review",
        path="/batch-review",
        title="班級審核",
        purpose="審核教師為班級提交的機器配置申請，同一個班級的機器節點一起審。",
        sections=("待審核", "已通過", "已拒絕", "全部"),
        access="admin",
        elements=_BATCH_REVIEW_ELEMENTS,
    ),
    SurfaceSpec(
        id="gpu-mgmt",
        path="/gpu-mgmt",
        title="GPU 管理",
        purpose="查看叢集中所有 PCI Passthrough GPU 的指派狀態與 vGPU 規格。",
        sections=("GPU 清單", "可用與使用中數量", "使用中的 VM"),
        elements=_GPU_MGMT_ELEMENTS,
        access="admin",
    ),
    SurfaceSpec(
        id="monitoring",
        path="/monitoring",
        title="資源監控",
        purpose="查看叢集資源使用、節點趨勢與閾值警告。",
        sections=("叢集用量", "節點趨勢", "閾值警告"),
        access="admin",
        elements=_MONITORING_ELEMENTS,
    ),
    SurfaceSpec(
        id="audit",
        path="/audit",
        title="稽核日誌",
        purpose="查詢系統操作紀錄，包含危險操作與登入失敗。",
        sections=("紀錄清單", "危險操作", "登入失敗"),
        elements=_AUDIT_ELEMENTS,
        access="admin",
    ),
    # 原「系統設定」的七個分頁，2026-09 各自升格為系統管理底下的獨立頁面
    SurfaceSpec(
        id="pve-connections",
        path="/pve-connections",
        title="PVE 連線",
        purpose="管理 Proxmox VE 的連線入口，以及每個叢集自己的 pool、Storage 與網段設定。",
        sections=("PVE 連線清單", "連線設定", "此叢集的資源設定"),
        elements=_PVE_CONNECTIONS_ELEMENTS,
        access="admin",
    ),
    SurfaceSpec(
        id="scheduler",
        path="/scheduler",
        title="資源排程",
        purpose="跨叢集共用的放置、超配與排程開機參數。",
        sections=("放置與超配", "資源評估閾值", "排程開機與時段"),
        elements=_SCHEDULER_ELEMENTS,
        access="admin",
    ),
    SurfaceSpec(
        id="governance",
        path="/governance",
        title="治理",
        purpose="資源警告、TTL 回收、閒置與挖礦偵測、快照與克隆併發的治理政策。",
        sections=(
            "資源警告", "TTL 生命週期", "閒置偵測", "VM / LXC 自動判斷",
            "反挖礦偵測", "快照治理", "克隆併發",
        ),
        elements=_GOVERNANCE_ELEMENTS,
        access="admin",
    ),
    SurfaceSpec(
        id="quotas",
        path="/quotas",
        title="配額",
        purpose="全域預設的資源上限，以及個別使用者的覆寫。",
        sections=("全域預設配額", "個別使用者覆寫"),
        elements=_QUOTAS_ELEMENTS,
        access="admin",
    ),
    SurfaceSpec(
        id="ldap",
        path="/ldap",
        title="LDAP",
        purpose="LDAP / Active Directory 登入的連線、服務帳號與角色對映。",
        sections=("LDAP / Active Directory 登入", "服務帳號與使用者搜尋", "帳號建立與角色對映"),
        elements=_LDAP_ELEMENTS,
        access="admin",
    ),
    SurfaceSpec(
        id="nodes",
        path="/nodes",
        title="節點管理",
        purpose="各 PVE 節點的啟用狀態、連線位址與放置優先度。",
        sections=("節點管理",),
        elements=_NODES_ELEMENTS,
        access="admin",
    ),
    SurfaceSpec(
        id="storage",
        path="/storage",
        title="Storage",
        purpose="各 Storage 的速度等級、使用者優先度與啟用狀態，放置時據此挑選磁碟。",
        sections=("Storage",),
        elements=_STORAGE_ELEMENTS,
        access="admin",
    ),
    SurfaceSpec(
        id="ip-management",
        path="/ip-management",
        title="IP 管理",
        purpose="管理子網設定與所有 IP 位址的分配狀況。",
        sections=("子網設定", "IP 分配"),
        elements=_IP_MGMT_ELEMENTS,
        access="admin",
    ),
    SurfaceSpec(
        id="domain",
        path="/domain",
        title="網域管理",
        purpose=(
            "設定 Cloudflare 供應商連線、檢視 Zone，並新增、調整或刪除 DNS record。"
            "反向代理可用的網址結尾就是在這裡開放的。"
        ),
        sections=("供應商連線", "Zone", "DNS record"),
        access="admin",
        elements=_DOMAIN_ELEMENTS,
    ),
    SurfaceSpec(
        id="gateway",
        path="/gateway",
        title="閘道 VM",
        purpose="管理 HAProxy、Traefik 與 WireGuard VPN 的服務設定與狀態。",
        sections=("連線設定", "服務狀態"),
        access="admin",
        elements=_GATEWAY_ELEMENTS,
    ),
    SurfaceSpec(
        id="admin",
        path="/admin",
        title="使用者管理",
        purpose="管理使用者帳戶、角色與登入狀態。",
        sections=("使用者清單", "角色", "啟用狀態"),
        access="admin",
        elements=_ADMIN_ELEMENTS,
    ),
    SurfaceSpec(
        id="ai-api-review",
        path="/ai-api-review",
        title="AI API 審核",
        purpose="審核 AI API 金鑰申請並核發存取參數。",
        sections=("待審核", "已通過", "已拒絕", "全部"),
        access="admin",
        elements=_AI_API_REVIEW_ELEMENTS,
    ),
    SurfaceSpec(
        id="ai-api-keys",
        path="/ai-api-keys",
        title="金鑰管理",
        purpose="查看資料庫中現存的所有 AI API 金鑰紀錄與狀態。",
        sections=("金鑰清單", "啟用與失效"),
        access="admin",
        elements=_AI_API_KEYS_ELEMENTS,
    ),
    SurfaceSpec(
        id="ai-monitoring",
        path="/ai-monitoring",
        title="AI 使用監控",
        purpose="檢視 AI Proxy 與 Template 服務的呼叫紀錄與用量統計。",
        sections=("Proxy 呼叫", "Template 呼叫", "使用者用量"),
        access="admin",
        elements=_AI_MONITORING_ELEMENTS,
    ),
)

def all_surfaces() -> tuple[SurfaceSpec, ...]:
    return _SURFACES


def get_surfaces_for_user(user: User) -> tuple[SurfaceSpec, ...]:
    role = resolve_user_role(user)
    return tuple(s for s in _SURFACES if can_access(s.access, role))


def find_surface(surface_id: str, surfaces: Iterable[SurfaceSpec]) -> SurfaceSpec | None:
    target = (surface_id or "").strip()
    if not target:
        return None
    for surface in surfaces:
        if surface.id == target:
            return surface
    return None


def match_element_by_label(surface: SurfaceSpec, question: str) -> ElementSpec | None:
    """使用者在問題裡講出元素名稱時直接認出它。

    「資源名稱要填什麼」講得比游標還清楚，不該因為前端還沒回報 focus 就退回
    頁面說明。取最長的命中，避免「名稱」搶走「連線名稱」這種包含關係。
    """
    text = (question or "").casefold()
    if not text:
        return None
    matched = [
        element
        for element in surface.elements
        if element.label and element.label.casefold() in text
    ]
    if not matched:
        return None
    return max(matched, key=lambda element: len(element.label))


def find_element(surface: SurfaceSpec, element_id: str) -> ElementSpec | None:
    target = (element_id or "").strip()
    if not target:
        return None
    for element in surface.elements:
        if element.id == target:
            return element
    return None
