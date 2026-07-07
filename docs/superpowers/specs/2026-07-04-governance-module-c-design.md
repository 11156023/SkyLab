# 模組 C：企業級運維治理 — 設計文件

日期：2026-07-04
狀態：已核准（使用者確認：全部四項、規則引擎 Auto 判斷、漸進式 TTL、站內+Email 告警、
LDAP 管理 UI 可設定、首登自動建帳 student、監控代理 PVE RRD）

## 目標

解決大規模用戶下的資源回收、身份認證與透明化監控：

1. 資源監控 Dashboard（全域/節點/VM 三級）+ 閾值告警（站內 + Email）。
2. TTL 生命週期管理：到期漸進回收（通知 → 關機 → 寬限期 → 刪除佇列）、
   閒置偵測（CPU 長期 <1% → 通知 → 自動關機，不刪除）。
3. 使用者申請 VM vs Container 自動判斷：Auto 模式由規則引擎決定並附理由，
   手動模式行為不變（取代原計畫的「VM vs Container 對比指引」）。
4. LDAP/Active Directory 整合：校方帳號登入，管理 UI 可設定。

## 既有基礎（直接複用，不重造）

- PVE infra：`list_nodes`、`list_all_resources`（cluster/resources）、
  `get_rrd_data`（VM 級 RRD）。監控只需補「節點級 RRD」一個 infra 函式。
- `Resource.expiry_date`、`auto_stop_at/auto_stop_reason` + 排程器
  `process_auto_stops`；`DeletionRequest` 刪除佇列 + `process_pending_deletions`。
  TTL 直接掛接這些機制。
- `ProxmoxConfig` singleton（DB 存 + 管理 UI + `encrypt_value`）模式套用到
  `GovernanceConfig` 與 `LdapConfig`。
- Email：既有 `app/utils/email.py` 的 `send_email`。
- 前端已有 recharts、shadcn/ui、TanStack Query。

## C-1 資源監控 + 告警

### Service — `services/monitoring/`

- `monitoring_service.py`：
  - `get_overview()`：叢集匯總（CPU/RAM/Disk 用量與容量、節點在線數、
    VM/LXC 運行/停止數、top N 高耗用 VM）。來源：一次 `cluster/resources` 呼叫。
  - `get_node_rrd(node, timeframe)`／`get_vm_rrd(vmid, timeframe)`：
    直接代理 PVE RRD（hour/day/week），後端不自存時序資料。
- `alert_service.py`：
  - `evaluate_alerts()`：抽 cluster/resources 比對 GovernanceConfig 閾值
    （預設 90%）。命中 → 建 `AlertEvent` + email 管理員。
  - 冷卻期：同 (scope, target, metric) 在 `alert_cooldown_minutes`（預設 30）
    內不重發。
  - 遲滯自動 resolve：指標回落到「閾值 − 5%」以下 → 標記 `resolved_at`。

### Model — `AlertEvent`（新表）

id、scope（node|vm|cluster）、target（節點名或 vmid）、metric（cpu|memory|disk）、
value、threshold、message、created_at、resolved_at、acknowledged_by、acknowledged_at。

### Routes — `api/routes/monitoring.py`

- `GET /monitoring/overview` — admin。
- `GET /monitoring/nodes/{node}/rrd?timeframe=` — admin。
- `GET /monitoring/vms/{vmid}/rrd?timeframe=` — 擁有者或 admin。
- `GET /monitoring/alerts?active=` — admin。
- `POST /monitoring/alerts/{id}/ack` — admin。

### 排程

coordinator `run_scheduler` 新增 `process_resource_alerts` task
（每 tick 檢查距上次評估是否超過 `alert_check_interval_seconds`，預設 60s）。

## C-2 治理設定 + TTL 生命週期

### Model — `GovernanceConfig`（singleton，同 ProxmoxConfig 模式）

不塞進已過胖的 ProxmoxConfig（維護者原則 #4）。欄位：

- 告警：`alerts_enabled`、`alert_cpu_threshold=90`、`alert_memory_threshold=90`、
  `alert_disk_threshold=90`、`alert_cooldown_minutes=30`、
  `alert_check_interval_seconds=60`、`alert_email_enabled=true`。
- TTL：`ttl_enabled`、`expiry_warn_days=3`、`expiry_grace_delete_days=7`。
- 閒置：`idle_detection_enabled`、`idle_cpu_threshold_percent=1.0`、
  `idle_window_hours=48`、`idle_grace_hours=24`。
- Auto 判斷：`workload_advisor_enabled=true`。

Routes：`GET/PUT /governance/config` — admin。

### Resource 新欄位（Alembic migration）

`expiry_notified_at`、`idle_since`、`idle_notified_at`、`scheduled_deletion_at`。

### 排程 task 1 — `process_ttl_lifecycle`（漸進式）

1. 到期前 `expiry_warn_days` 內且未通知 → email 擁有者，記 `expiry_notified_at`。
2. 已過期且仍運行 → `set_auto_stop(now, reason="ttl_expired")` 走既有
   auto-stop 管線 + email。
3. 過期超過 `expiry_grace_delete_days` → `create_deletion_request`（複用既有
   刪除佇列）+ email，記 `scheduled_deletion_at` 防重複入列。

### 排程 task 2 — `process_idle_detection`

對 running VM 抽 RRD（day timeframe）計算過去 `idle_window_hours` 平均 CPU：

- < `idle_cpu_threshold_percent` 且未標記 → 記 `idle_since` + email 擁有者。
- 已標記且超過 `idle_grace_hours` 仍閒置 → `set_auto_stop(now, reason="idle")`。
- 恢復活躍 → 清除 `idle_since/idle_notified_at`。
- **只關機不刪除**。每 tick 抽樣上限（防 200+ VM 時單 tick 過長），輪替掃描。

## C-3 VM vs Container 自動判斷（Auto/手動）

### 規則引擎 — `services/vm/workload_advisor.py`

純函式、無外部呼叫、表驅動可單測。
輸入：申請欄位（environment_type、os_info、reason、cores、memory、
gpu_mapping_id、service_template_slug、ostemplate）。
輸出：`WorkloadAdvice { resource_type: "vm"|"lxc", confidence: high|medium|low,
reasons: list[str] }`（理由為使用者可讀的中文字串）。

規則優先序（先命中先贏）：

1. `gpu_mapping_id` 存在 → VM（GPU passthrough 需完整虛擬化）。
2. os_info/environment_type 含 Windows/BSD/macOS 等非 Linux → VM。
3. reason/環境關鍵字：docker、kubernetes/k8s、kernel/核心模組、VPN/wireguard、
   嵌套虛擬化/nested、systemd-nspawn → VM（LXC 內受限）。
4. `service_template_slug` 存在 → LXC（community-scripts 本來就是 LXC 路徑）。
5. 輕量 Linux（cores ≤ 2 且 memory ≤ 4096、web/dev 類環境）→ LXC（密度與秒級啟動）。
6. 預設：Linux → LXC；無法判定 → VM（confidence=low）。

### API 整合

- `POST /vm-requests/advise`：傳入表單欄位 → 回傳建議 + 理由。前端 Auto 模式
  以此決定實際 resource_type 並顯示對應的範本欄位（LXC 需 ostemplate、
  VM 需 template_id+username 的既有驗證因此不受影響）。
- `VMRequestCreate` 新增 `requested_mode: "manual"|"auto"`（預設 manual）；
  `resource_type` 維持具體值（vm|lxc）。requested_mode=auto 時 create() 於
  伺服器端重跑 advisor（同一純函式、確定性結果）並記錄
  `auto_decision_reason`（VMRequest 新欄位，migration；審核者與申請者可見）。
- 手動模式行為完全不變；`workload_advisor_enabled=false` 時 advise API 與
  auto 模式回 400。

## C-4 LDAP/AD SSO

### 依賴

`ldap3`（純 Python，MIT）加入 backend pyproject。

### Model — `LdapConfig`（singleton）

enabled、server_uri（ldap:// 或 ldaps://）、use_starttls、bind_dn、
encrypted_bind_password（`encrypt_value`）、user_search_base、
user_filter_template（如 `(sAMAccountName={username})` / `(uid={username})`）、
email_attribute（預設 mail）、name_attribute（預設 displayName）、
teacher_group_dn、admin_group_dn（可選；命中給對應角色）、
auto_create_users=true、connect_timeout_seconds=5。

### Infrastructure — `infrastructure/ldap/client.py`

service bind → 以 filter 搜尋使用者 DN 與屬性 → 以使用者密碼 rebind 驗證 →
讀取 memberOf 群組。所有 ldap3 例外轉為使用者可讀 `AppError`。

### Service — `services/user/ldap_auth_service.py`

`login_ldap(username, password)`：LDAP 驗證 → 以 email 查本地 user：

- 不存在且 `auto_create_users` → 建立 user（role=student；命中 group DN 對映
  則升 teacher/admin；`hashed_password` 設隨機不可登入值）。
- 存在 → 檢查 is_active。
- 發既有 JWT token pair；audit log 新增 `login_ldap_success/failed` action。

### Routes

- `POST /login/ldap`（username + password）。
- `GET /login/methods`（公開：`{password: true, google: bool, ldap: bool}`，
  前端登入頁據此顯示分頁）。
- `GET/PUT /admin/ldap-config`、`POST /admin/ldap-config/test`
  （測試 service bind 或測試指定帳密登入，不落 DB）— admin。

## C-5 前端

- `/admin/monitoring`：全域卡片（CPU/RAM/Disk 儀表 + 運行數）、節點用量表
  （進度條 + 點開 recharts 趨勢圖）、top 耗用 VM、活動告警面板（ack 按鈕）。
- `/admin/configuration`：新增「治理」（告警閾值/TTL/閒置）與「LDAP」設定區塊
  （含測試連線按鈕）。
- 申請表單：資源類型加「自動判斷（推薦）/ 手動選擇」切換；Auto 模式以
  debounce 呼叫 advise API 即時顯示建議與理由卡片。
- 登入頁：`/login/methods` 回報 ldap 啟用時顯示「校園帳號」分頁。
- `my-resources`：到期倒數 badge、閒置警示 badge。
- 完成後 regenerate OpenAPI client。

## RBAC 摘要

| 能力 | student | teacher | admin |
|---|---|---|---|
| 監控 overview / 節點 RRD / 告警 | ✗ | ✗ | ✓ |
| 自己 VM 的 RRD 趨勢 | ✓ | ✓ | ✓ |
| governance/LDAP 設定 | ✗ | ✗ | ✓ |
| advise API（Auto 判斷） | ✓ | ✓ | ✓ |
| LDAP 登入 | ✓ | ✓ | ✓ |

## 交付批次

| 批次 | 內容 |
|---|---|
| C1 | 監控 service + routes + 節點 RRD infra |
| C2 | GovernanceConfig + AlertEvent + 告警排程 + email（migration） |
| C3 | TTL/閒置排程 + Resource 欄位 migration |
| C4 | workload advisor + advise API + vm_request auto 整合（migration） |
| C5 | LDAP infra + service + routes + LdapConfig（migration） |
| C6 | 前端全部 + regenerate client |
| C7 | 單元測試補齊 + CLAUDE.md 更新 |

## 測試策略

- advisor：表驅動單測覆蓋全部規則與優先序。
- 告警：假 cluster/resources 數據驗證閾值、冷卻、遲滯 resolve。
- TTL/閒置：假時鐘 + 假 RRD 驗證狀態機（通知→關機→刪除佇列；閒置恢復清標記）。
- LDAP：mock ldap3 連線驗證建帳、角色對映、錯誤轉 AppError。
- 全程不需真實 PVE / LDAP / SMTP。

## 範圍限制與假設

- 監控趨勢依賴 PVE RRD 精度（hour≈每分鐘、day≈每 30 分鐘），不承諾秒級即時。
- 告警評估在單一 backend 行程排程器內（與既有 scheduler 同前提）。
- 閒置偵測僅看 CPU（PVE RRD 無每 VM 精細 IO 基線）；反挖礦的 CPU 特徵屬模組 D。
- LDAP 僅做認證與屬性讀取，不做定期目錄同步（帳號停用以 is_active 管理）。
