# 模組 C：企業級運維治理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 資源監控 Dashboard + 閾值告警、漸進式 TTL/閒置回收、申請 VM vs LXC 規則引擎自動判斷（Auto/手動）、LDAP/AD 登入（管理 UI 可設定）。

**Architecture:** 監控直接代理 PVE cluster/resources 與 RRD（不自存時序）；告警/TTL/閒置為排程器新 task，決策邏輯寫成純函式（可無 DB 單測），I/O 由薄協調層處理；治理與 LDAP 設定各為 DB singleton（仿 ProxmoxConfig）；Auto 判斷為表驅動規則引擎純函式。

**Tech Stack:** FastAPI + SQLModel + Alembic、proxmoxer（既有 infra）、ldap3、既有 send_email、React 19 + recharts + TanStack Query。

**Spec:** `docs/superpowers/specs/2026-07-04-governance-module-c-design.md`

## Global Constraints

- 遵循 Routes → Services → Infrastructure 分層；PVE/LDAP 呼叫只出現在 infrastructure。
- 所有錯誤轉使用者可讀 `AppError` 家族（`BadRequestError`/`AuthenticationError`/`NotFoundError`）。
- Email 發送一律 try/except 包裹（`settings.emails_enabled` 為 False 或 SMTP 失敗不得使排程 task 崩潰）。
- 排程 task 每 tick 必須有界（閒置掃描每 tick ≤ `idle_scan_batch_size` 台）。
- 決策邏輯（告警評估、TTL 狀態機、閒置判斷、workload advisor）為純函式：輸入資料 + now，輸出動作清單；不在純函式內碰 DB/PVE/SMTP。
- 新表/新欄位一律 Alembic migration，rev id 用 `gov01`…`gov04` 前綴（仿 `vmt01`）。
- mypy strict + ruff 必須全過；`uv run ruff check . && uv run mypy .`。
- 純邏輯測試不依賴真實 PVE/DB/SMTP/LDAP（monkeypatch / 建構假資料）。
- commit 訊息格式「模組C運維治理: <內容> (Cn)」。
- 前端改後端 API 後必須 regenerate OpenAPI client（C6 統一做）。

---

### Task C1: 監控 service + routes + 節點 RRD infra

**Files:**
- Modify: `backend/app/infrastructure/proxmox/operations.py`（RRD stats 區塊後加 `get_node_rrd_data`）
- Create: `backend/app/services/monitoring/__init__.py`
- Create: `backend/app/services/monitoring/monitoring_service.py`
- Create: `backend/app/schemas/monitoring.py`（並在 `schemas/__init__.py` re-export）
- Create: `backend/app/api/routes/monitoring.py`
- Modify: `backend/app/api/main.py`（註冊 `monitoring.router`）
- Test: `backend/tests/services/test_monitoring_service.py`

**Interfaces (Produces):**
```python
# infrastructure/proxmox/operations.py
def get_node_rrd_data(node: str, timeframe: str) -> list[dict]:
    return get_proxmox_api().nodes(node).rrddata.get(timeframe=timeframe)

# schemas/monitoring.py
class NodeMetrics(BaseModel):
    node: str; status: str
    cpu: float; maxcpu: int          # cpu 為 0..1 佔比
    mem: int; maxmem: int            # bytes
    disk: int; maxdisk: int          # bytes
    uptime: int
class VMTopEntry(BaseModel):
    vmid: int; name: str; node: str; type: str   # qemu|lxc
    cpu: float; mem: int; maxmem: int; status: str
class MonitoringOverview(BaseModel):
    nodes_online: int; nodes_total: int
    cpu_used: float; cpu_total: int              # used=Σ(node.cpu*maxcpu)
    mem_used: int; mem_total: int
    disk_used: int; disk_total: int
    vms_running: int; vms_stopped: int
    lxc_running: int; lxc_stopped: int
    top_cpu: list[VMTopEntry]; top_mem: list[VMTopEntry]   # 各取前 5、僅 running

# services/monitoring/monitoring_service.py
def build_overview(nodes: list[dict], resources: list[dict]) -> MonitoringOverview  # 純函式
def get_overview() -> MonitoringOverview          # 拉 proxmox_service.list_nodes()/list_all_resources() 後委派
def get_node_rrd(node: str, timeframe: str) -> list[dict]       # timeframe 白名單 hour|day|week，否則 BadRequestError
def get_vm_rrd(session: Session, *, vmid: int, timeframe: str, user: User) -> list[dict]
    # resource_repo.get_resource_by_vmid 查擁有者；非本人且非 admin → PermissionDeniedError
    # 再 proxmox_service.find_resource(vmid) 取 node/type → get_rrd_data
```

**要點：**
- `cluster/resources`（`list_all_resources()`）的 dict 欄位：`vmid,name,node,type("qemu"|"lxc"),status("running"|"stopped"),cpu,maxcpu,mem,maxmem`。節點 dict（`list_nodes()`）：`node,status("online"|...),cpu,maxcpu,mem,maxmem,disk,maxdisk,uptime`。缺鍵一律 `.get(k, 0)` 容忍。
- 路由權限：`/monitoring/overview`、`/monitoring/nodes/{node}/rrd` 用 `AdminUser`；`/monitoring/vms/{vmid}/rrd` 用 `CurrentUser`（service 內做擁有者檢查）。router prefix `/monitoring`，tags `["monitoring"]`。
- admin 判定沿用 `require_admin_access`（`core/authorizers.py`）。
- PVE 呼叫失敗 → 讓既有 `ProxmoxError` 例外處理鏈接手，不要吞掉。

**Tests（純函式，無 DB/PVE）：**
- `build_overview`：2 節點（1 online 1 offline）+ 4 資源（qemu running/stopped、lxc running/stopped）→ 驗證匯總、計數、top 排序（cpu 降冪）、offline 節點仍計容量、缺鍵資源不炸。
- `get_node_rrd` timeframe 白名單：`"month"` → `BadRequestError`。
- `get_vm_rrd` 擁有者檢查：monkeypatch `resource_repo.get_resource_by_vmid` 與 `proxmox_service`，student 存取他人 vmid → `PermissionDeniedError`（確認 `exceptions.py` 內實際類名，若無則用 `AppError(403, ...)` 既有慣例）。

- [x] C1-1 寫 `get_node_rrd_data` + schemas + `build_overview` 純函式與測試（先寫測試跑 FAIL）
- [x] C1-2 實作 service I/O 包裝 + routes + main.py 註冊，測試 PASS
- [x] C1-3 `uv run ruff check . && uv run mypy .` 全過
- [x] C1-4 Commit：「模組C運維治理: 監控 overview/RRD service + routes (C1)」

---

### Task C2: GovernanceConfig + AlertEvent + 告警排程 + email

**Files:**
- Create: `backend/app/models/governance_config.py`
- Create: `backend/app/models/alert_event.py`
- Modify: `backend/app/models/__init__.py`（export 兩者）
- Create: `backend/app/alembic/versions/gov01_add_governance_config_and_alert_events.py`
- Create: `backend/app/repositories/governance.py`
- Create: `backend/app/services/monitoring/alert_service.py`
- Create: `backend/app/api/routes/governance.py`（`GET/PUT /governance/config`，AdminUser）
- Modify: `backend/app/api/routes/monitoring.py`（`GET /monitoring/alerts?active=`、`POST /monitoring/alerts/{id}/ack`）
- Modify: `backend/app/schemas/monitoring.py`（`AlertEventPublic`、`GovernanceConfigPublic/Update`）
- Modify: `backend/app/services/scheduling/coordinator.py`（`run_scheduler` tasks 加 `process_resource_alerts`）
- Test: `backend/tests/services/test_alert_service.py`

**Interfaces (Produces):**
```python
# models/governance_config.py — singleton，id 固定 1（仿 ProxmoxConfig）
class GovernanceConfig(SQLModel, table=True):
    __tablename__ = "governance_config"
    id: int = Field(default=1, primary_key=True)
    alerts_enabled: bool = True
    alert_cpu_threshold: float = Field(default=90.0, ge=50, le=100)     # percent
    alert_memory_threshold: float = Field(default=90.0, ge=50, le=100)
    alert_disk_threshold: float = Field(default=90.0, ge=50, le=100)
    alert_cooldown_minutes: int = Field(default=30, ge=1, le=1440)
    alert_check_interval_seconds: int = Field(default=60, ge=15, le=3600)
    alert_email_enabled: bool = True
    ttl_enabled: bool = True
    expiry_warn_days: int = Field(default=3, ge=1, le=30)
    expiry_grace_delete_days: int = Field(default=7, ge=0, le=90)
    idle_detection_enabled: bool = True
    idle_cpu_threshold_percent: float = Field(default=1.0, ge=0.1, le=20)
    idle_window_hours: int = Field(default=48, ge=1, le=720)
    idle_grace_hours: int = Field(default=24, ge=1, le=720)
    idle_scan_batch_size: int = Field(default=20, ge=1, le=200)
    workload_advisor_enabled: bool = True
    updated_at: datetime = Field(default_factory=get_datetime_utc, sa_type=sa.DateTime(timezone=True))

# models/alert_event.py
class AlertScope(str, enum.Enum): cluster="cluster"; node="node"; vm="vm"
class AlertMetric(str, enum.Enum): cpu="cpu"; memory="memory"; disk="disk"
class AlertEvent(SQLModel, table=True):
    __tablename__ = "alert_events"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    scope: AlertScope（sa Enum）; target: str = Field(max_length=255, index=True)
    metric: AlertMetric（sa Enum）
    value: float; threshold: float
    message: str
    created_at: datetime（timezone=True, nullable=False）
    resolved_at: datetime | None（timezone=True）
    acknowledged_by: uuid.UUID | None = Field(default=None, foreign_key="user.id")
    acknowledged_at: datetime | None（timezone=True）
    # index: (target, metric, resolved_at)

# repositories/governance.py
def get_governance_config(*, session: Session) -> GovernanceConfig
    # session.get(GovernanceConfig, 1)；無則建立預設列 commit 後回傳
def update_governance_config(*, session: Session, data: dict) -> GovernanceConfig
def get_open_alerts(*, session: Session) -> list[AlertEvent]        # resolved_at IS NULL
def list_alerts(*, session: Session, active_only: bool, limit: int = 200) -> list[AlertEvent]
def acknowledge_alert(*, session: Session, alert_id: uuid.UUID, user_id: uuid.UUID) -> AlertEvent  # 無此 id → NotFoundError

# services/monitoring/alert_service.py
@dataclass(frozen=True)
class MetricSample:
    scope: str; target: str; metric: str; value: float   # value 為 percent 0..100
@dataclass(frozen=True)
class AlertDecision:
    new_alerts: list[MetricSample]        # 需建立事件
    resolved_targets: list[tuple[str, str]]  # (target, metric) 需標記 resolved

def collect_samples(nodes: list[dict], resources: list[dict]) -> list[MetricSample]  # 純函式
    # node: cpu*100、mem/maxmem*100、disk/maxdisk*100（maxX==0 → 略過該樣本）
    # vm(running only): cpu*100、mem/maxmem*100（VM 級不做 disk — cluster/resources 無可靠值）
def evaluate(samples, open_alerts, config, now) -> AlertDecision   # 純函式
    # 超過對應閾值且 (target,metric) 無 open alert 且距同鍵最近一筆 created_at ≥ cooldown → new
    # open alert 且 value < threshold - 5.0（遲滯）→ resolved
def process_resource_alerts() -> int
    # module-level `_last_run: float` monotonic gate（< interval 直接 return 0）
    # 拉 PVE → collect → 讀 config/open alerts → evaluate → 寫 AlertEvent/resolved_at
    # alert_email_enabled → 對每個 admin（is_superuser 或 role==admin 且 is_active）send_email
    #（subject: "[SkyLab 告警] {target} {metric} {value:.0f}%"），try/except per-email
```

**要點：**
- cooldown 查詢：同 (target, metric) 的最近一筆 AlertEvent（含已 resolved）`created_at`；在冷卻期內即使再超標也不建新事件。
- `process_resource_alerts` 整體 try/except（仿 `process_pending_deletions_task`），PVE 失敗 log 後 return 0。
- migration `gov01`：down_revision 接目前 head（先跑 `alembic heads` 確認）；建兩表 + index。
- `GET /governance/config` 回 Public schema（全部欄位 + updated_at）；PUT 用 partial update schema（全欄位 optional）。

**Tests（純函式）：**
- `collect_samples`：節點 + running/stopped VM 混合 → stopped VM 不取樣、maxmem=0 略過、百分比換算正確。
- `evaluate`：首次超標 → new；已 open → 不重建；冷卻期內（最近事件 10 分鐘前、cooldown 30）→ 不建；回落 87%（threshold 90、遲滯 85）→ 不 resolve；回落 84% → resolve。
- ack/repository 測試併入 C7 的 DB-backed 測試（可選）。

- [x] C2-1 models + migration gov01 + repositories（測試先行：evaluate/collect_samples FAIL）
- [x] C2-2 alert_service 純函式實作，測試 PASS
- [x] C2-3 process_resource_alerts I/O 協調 + email + coordinator 掛載 + routes/schemas
- [x] C2-4 ruff + mypy 全過
- [x] C2-5 Commit：「模組C運維治理: GovernanceConfig/AlertEvent + 告警排程 (C2)」

---

### Task C3: TTL 漸進回收 + 閒置偵測排程

**Files:**
- Modify: `backend/app/models/resource.py`（加 4 欄位）
- Create: `backend/app/alembic/versions/gov02_add_resource_lifecycle_fields.py`
- Create: `backend/app/services/governance/__init__.py`
- Create: `backend/app/services/governance/lifecycle_policy.py`（純函式）
- Create: `backend/app/services/governance/lifecycle_service.py`（I/O 協調）
- Modify: `backend/app/repositories/resource.py`（查詢 helpers）
- Modify: `backend/app/services/scheduling/coordinator.py`（掛 `process_ttl_lifecycle`、`process_idle_detection`）
- Test: `backend/tests/services/test_lifecycle_policy.py`

**Resource 新欄位：**
```python
expiry_notified_at: datetime | None      # timezone=True
idle_since: datetime | None
idle_notified_at: datetime | None
idle_checked_at: datetime | None         # 輪替掃描游標
scheduled_deletion_at: datetime | None   # 已入刪除佇列時間（防重複入列）
```

**Interfaces (Produces):**
```python
# lifecycle_policy.py — 全部純函式
class TtlAction(str, enum.Enum):
    warn="warn"; stop="stop"; delete="delete"; none="none"
def decide_ttl_action(*, expiry_date: date | None, expiry_notified_at, scheduled_deletion_at,
                      is_running: bool, now: datetime, warn_days: int, grace_delete_days: int) -> TtlAction
    # 無 expiry_date → none
    # now < expiry 且 expiry - now ≤ warn_days 且未通知 → warn
    # now ≥ expiry 且 is_running → stop（重複 tick 冪等：已停止則跳過）
    # now ≥ expiry + grace_delete_days 且未入列 → delete
    #（expiry_date 為 date：以當日 00:00 UTC 起算）
class IdleAction(str, enum.Enum):
    mark="mark"; stop="stop"; clear="clear"; none="none"
def average_cpu_percent(rrd: list[dict], *, window_hours: int, now: datetime) -> float | None
    # rrd 條目 {"time": epoch, "cpu": 0..1}；取 window 內有 cpu 值的點平均*100；無點 → None
def decide_idle_action(*, avg_cpu: float | None, idle_since, now,
                       threshold_percent: float, grace_hours: int) -> IdleAction

# lifecycle_service.py
def process_ttl_lifecycle() -> int
    # config.ttl_enabled 檢查 → resource_repo.list_resources_with_expiry()
    # warn: email 擁有者 + 記 expiry_notified_at
    # stop: proxmox_service.find_resource 確認 running → set_auto_stop(now, "ttl_expired") + email
    # delete: deletion_service.create_deletion_request(purge=True) + 記 scheduled_deletion_at + email
def process_idle_detection() -> int
    # config.idle_detection_enabled → list_all_resources() 取 running 集合
    # DB 撈 running 中 idle_checked_at 最舊的前 idle_scan_batch_size 台
    # 每台 get_rrd_data(timeframe="day") → average_cpu_percent → decide_idle_action
    # mark: idle_since=now + email + idle_notified_at；stop: set_auto_stop(now, "idle")；clear: 清三欄位
    # 每台處理完更新 idle_checked_at=now（含失敗，避免卡死輪替）
```

**要點：**
- 兩個 task 掛進 `run_scheduler` tasks 清單，handler 整體 try/except 包裹（仿 `process_pending_deletions_task`）。
- email 一律 `try/except`；warn/stop/delete 各自 email 主旨含 vmid 與到期日；擁有者 email 從 `resource.user` 關聯取。
- `list_resources_with_expiry(*, session)`：`expiry_date IS NOT NULL` 全撈（量小）；idle 掃描查詢需 join running 集合（先 PVE 拿 running vmids，再 `WHERE vmid IN (...) ORDER BY idle_checked_at NULLS FIRST LIMIT batch`）。
- `set_auto_stop` 既有簽名：`set_auto_stop(*, session, vmid, auto_stop_at, auto_stop_reason, commit=True)`；停機交給既有 `process_auto_stops` 執行，本 task 只排程（auto_stop_at=now）。

**Tests（純函式 + 假時鐘）：**
- `decide_ttl_action` 全分支：無到期、warn 視窗內未通知/已通知、過期運行中、過期已停、寬限期滿未入列/已入列。
- `average_cpu_percent`：視窗過濾、空 rrd → None、缺 cpu 鍵的點忽略。
- `decide_idle_action`：低於閾值首次 → mark、已 mark 未滿寬限 → none、滿寬限 → stop、恢復活躍 → clear、avg=None → none。

- [x] C3-1 model 欄位 + migration gov02 + policy 純函式（測試先行 FAIL→PASS）
- [x] C3-2 repository helpers + lifecycle_service + coordinator 掛載
- [x] C3-3 ruff + mypy 全過
- [x] C3-4 Commit：「模組C運維治理: TTL 漸進回收與閒置偵測排程 (C3)」

---

### Task C4: workload advisor + advise API + auto 模式整合

**Files:**
- Create: `backend/app/services/vm/workload_advisor.py`
- Modify: `backend/app/models/vm_request.py`（加 `requested_mode`、`auto_decision_reason`）
- Create: `backend/app/alembic/versions/gov03_add_vm_request_auto_decision_fields.py`
- Modify: `backend/app/schemas/vm_request.py`（Create/Public 加欄位；新增 advise schemas）
- Modify: `backend/app/services/vm/vm_request_service.py`（create() 整合）
- Modify: `backend/app/api/routes/vm_requests.py`（`POST /vm-requests/advise`）
- Test: `backend/tests/services/test_workload_advisor.py`

**Interfaces (Produces):**
```python
# services/vm/workload_advisor.py
@dataclass(frozen=True)
class WorkloadAdvice:
    resource_type: Literal["vm", "lxc"]
    confidence: Literal["high", "medium", "low"]
    reasons: list[str]                     # 使用者可讀中文
def advise(*, environment_type: str | None, os_info: str | None, reason: str | None,
           cores: int | None, memory: int | None, gpu_mapping_id: str | None,
           service_template_slug: str | None) -> WorkloadAdvice
```

**規則優先序（先命中先贏；關鍵字比對一律 casefold）：**
1. `gpu_mapping_id` → vm/high：「需要 GPU passthrough，必須使用完整虛擬機」
2. os_info 或 environment_type 含 `windows|freebsd|bsd|macos` → vm/high
3. reason/environment_type/os_info 含 `docker|kubernetes|k8s|kernel|核心模組|nested|嵌套|vpn|wireguard|systemd-nspawn` → vm/medium：「工作負載需要完整核心權限，LXC 容器受限」
4. `service_template_slug` → lxc/high：「服務範本以容器部署，啟動快、資源占用低」
5. `(cores or 0) <= 2 and (memory or 0) <= 4096` → lxc/medium：「輕量工作負載，容器密度高、秒級啟動」
6. 其餘 → lxc/low：「一般 Linux 工作負載預設建議容器；如需完整虛擬化請改用手動模式」

**Schemas：**
```python
class WorkloadAdviseRequest(BaseModel):
    environment_type: str | None = None; os_info: str | None = None
    reason: str | None = None; cores: int | None = None; memory: int | None = None
    gpu_mapping_id: str | None = None; service_template_slug: str | None = None
class WorkloadAdviceResponse(BaseModel):
    resource_type: Literal["vm", "lxc"]; confidence: str; reasons: list[str]
# VMRequestCreate 加：requested_mode: Literal["manual", "auto"] = "manual"
# VMRequestPublic 加：requested_mode: str = "manual"; auto_decision_reason: str | None = None
```

**create() 整合（`vm_request_service.py`，resource_type 驗證之後）：**
```python
if request_in.requested_mode == "auto":
    config = governance_repo.get_governance_config(session=session)
    if not config.workload_advisor_enabled:
        raise BadRequestError("Auto mode is disabled by administrator")
    advice = workload_advisor.advise(...)   # 伺服器端重跑（確定性）
    db 欄位 auto_decision_reason = "；".join(advice.reasons)
    # resource_type 仍用 request_in 傳入的具體值（前端已依 advise 帶入）；
    # 若與伺服器建議不一致，在 auto_decision_reason 附註「（提交值與伺服器建議不同）」
```
`create_vm_request` repo 建立時寫入 `requested_mode` 與 `auto_decision_reason`（確認 `repositories/vm_request.py` 的 create 是否用 `model_dump` 自動帶 — 是則只需 schema/model 欄位）。

**Route：** `POST /vm-requests/advise`（`CurrentUser`；`workload_advisor_enabled=False` → 400）。

**Tests（表驅動）：** 每條規則一案 + 優先序衝突案（gpu+service_template → vm；windows+輕量 → vm）+ 全空輸入 → lxc/low。

- [x] C4-1 advisor 純函式 + 表驅動測試（FAIL→PASS）
- [x] C4-2 model/schema 欄位 + migration gov03 + create() 整合 + advise route
- [x] C4-3 ruff + mypy 全過
- [x] C4-4 Commit：「模組C運維治理: VM vs LXC 自動判斷規則引擎 + Auto 模式 (C4)」

---

### Task C5: LDAP/AD 登入

**Files:**
- Modify: `backend/pyproject.toml`（dependencies 加 `"ldap3>=2.9,<3"`；`uv sync`）
- Create: `backend/app/models/ldap_config.py`
- Create: `backend/app/alembic/versions/gov04_add_ldap_config.py`
- Modify: `backend/app/models/__init__.py`、`backend/app/models/audit_log.py`（AuditAction 加 `login_ldap_success/login_ldap_failed`）
- Create: `backend/app/infrastructure/ldap/__init__.py`
- Create: `backend/app/infrastructure/ldap/client.py`
- Create: `backend/app/services/user/ldap_auth_service.py`
- Create: `backend/app/schemas/ldap.py`（Config Public/Update、TestRequest、LoginMethods）
- Modify: `backend/app/api/routes/login.py`（`POST /login/ldap`、`GET /login/methods`）
- Create: `backend/app/api/routes/ldap_config.py`（admin GET/PUT/`POST .../test`）
- Modify: `backend/app/api/main.py`（註冊 ldap_config.router）
- Test: `backend/tests/services/test_ldap_auth_service.py`

**Interfaces (Produces):**
```python
# models/ldap_config.py — singleton id=1
class LdapConfig(SQLModel, table=True):
    __tablename__ = "ldap_config"
    id: int = Field(default=1, primary_key=True)
    enabled: bool = False
    server_uri: str = Field(default="", max_length=255)     # ldap:// 或 ldaps://
    use_starttls: bool = False
    bind_dn: str = Field(default="", max_length=512)
    encrypted_bind_password: str = Field(default="", max_length=2048)  # encrypt_value
    user_search_base: str = Field(default="", max_length=512)
    user_filter_template: str = Field(default="(uid={username})", max_length=512)
    email_attribute: str = Field(default="mail", max_length=64)
    name_attribute: str = Field(default="displayName", max_length=64)
    teacher_group_dn: str | None = Field(default=None, max_length=512)
    admin_group_dn: str | None = Field(default=None, max_length=512)
    auto_create_users: bool = True
    connect_timeout_seconds: int = Field(default=5, ge=1, le=60)
    updated_at: datetime（default_factory=get_datetime_utc, timezone）

# infrastructure/ldap/client.py
@dataclass(frozen=True)
class LdapUserInfo:
    dn: str; email: str; full_name: str | None; groups: list[str]  # groups=memberOf DN
def authenticate_user(config: LdapConfig, username: str, password: str) -> LdapUserInfo
    # 1) service bind（decrypt_value(encrypted_bind_password)）
    # 2) search(user_search_base, filter_template.format(username=escaped), attrs=[email,name,memberOf])
    #    username 先做 LDAP filter escape（ldap3.utils.conv.escape_filter_chars）
    # 3) 找不到或 email 屬性空 → AuthenticationError("Invalid LDAP credentials")
    # 4) 以 user DN + password rebind；失敗 → AuthenticationError（同上，不洩漏帳號存在性）
    # 5) LDAPException / socket timeout → AppError(502, "無法連線 LDAP 伺服器，請聯絡管理員")
def test_bind(config: LdapConfig) -> None       # 只做 service bind，失敗丟同上例外

# services/user/ldap_auth_service.py
def login_ldap(*, session: Session, username: str, password: str) -> Token
    # config = get_ldap_config(session)；not enabled → BadRequestError("LDAP login is not enabled")
    # info = ldap_client.authenticate_user(...)
    # user = get_user_by_email(email=info.email)
    # 無 user：auto_create_users 才建（role: admin_group_dn 命中→admin；teacher_group_dn→teacher；否則 student；
    #   group 比對 casefold 完整 DN 相等；hashed_password=get_password_hash(secrets.token_urlsafe(32))）
    #   auto_create_users=False → BadRequestError("Account is not registered")
    # inactive → BadRequestError("Inactive user")
    # audit log login_ldap_success/failed（仿 auth_service.login 寫法）
    # return auth_service._create_token_pair(user) — 將 _create_token_pair 改名 export 或直接複製三行
def get_login_methods(*, session: Session) -> dict
    # {"password": True, "google": bool(settings.GOOGLE_CLIENT_ID), "ldap": config.enabled}
```

**Routes：**
- `POST /api/v1/login/ldap`，body `{username, password}` → `Token`（放 login.py，無需認證）。
- `GET /api/v1/login/methods` → LoginMethods（公開）。
- `GET/PUT /api/v1/admin/ldap-config`（AdminUser；GET 回傳不含 encrypted_bind_password，以 `bind_password_set: bool` 代替；PUT 收 `bind_password: str | None`，有值才重新加密覆寫）。
- `POST /api/v1/admin/ldap-config/test`：body 可帶完整 config override（不落 DB）+ 可選 test_username/test_password；只 test_bind 或連帶 authenticate_user；成功回 `{ok: true, message}`。

**Tests（mock `app.infrastructure.ldap.client`）：**
- enabled=False → BadRequestError。
- 驗證成功 + 無本地帳號 + auto_create → 建 student、audit success（用 DB fixture 或 monkeypatch user repo）。
- admin_group_dn 命中 → role=admin。
- auto_create=False + 無帳號 → BadRequestError + audit failed。
- infrastructure 丟 AuthenticationError → 透傳 + audit failed。

- [x] C5-1 ldap3 依賴 + LdapConfig model/migration gov04 + AuditAction
- [x] C5-2 infrastructure client（escape、雙 bind、例外轉換）
- [x] C5-3 service + routes + schemas + main.py 註冊；測試 FAIL→PASS
- [x] C5-4 ruff + mypy 全過
- [x] C5-5 Commit：「模組C運維治理: LDAP/AD 登入與管理設定 (C5)」

---

### Task C6: 前端（監控 Dashboard、設定、Auto 申請、LDAP 登入）

**Files:**
- Regenerate: `frontend/src/client/`（`bash ./scripts/generate-client.sh`，需後端運行）
- Create: `frontend/src/routes/_layout/admin.monitoring.tsx`
- Modify: `frontend/src/routes/_layout/admin.tsx` 或側欄元件（加監控入口；先 grep 現有 admin 導覽定義位置）
- Modify: `frontend/src/routes/_layout/admin.configuration.tsx`（治理 + LDAP 設定區塊）
- Modify: `frontend/src/routes/_layout/applications-create.tsx`（Auto/手動切換 + 建議卡）
- Modify: `frontend/src/routes/login.tsx`（依 /login/methods 顯示校園帳號分頁；先確認實際檔名 `routes/login.tsx`）
- Modify: `frontend/src/routes/_layout/my-resources.tsx`（到期倒數 badge、閒置 badge — 需後端 resource schema 曝露新欄位；若 C3 未曝露則在本批補 `schemas/resource.py`）
- i18n：新增字串跟隨現有 i18next 資源檔位置

**要點：**
- `/admin/monitoring`：
  - 頂部四卡：CPU/RAM/Disk 用量（`MonitoringOverview`，進度環或條）+ 節點在線數/VM 運行數。
  - 節點表：每節點 cpu/mem/disk 進度條 + status；點列展開 recharts `AreaChart`（rrd `cpu`、`memused/maxmem`），timeframe 切換 hour/day/week。
  - Top VM 兩欄（cpu/mem）。
  - 告警面板：`GET /monitoring/alerts?active=true` 列表 + ack 按鈕（`useMutation` + invalidate）；30s `refetchInterval`。
- 申請表單 Auto 模式：RadioGroup「自動判斷（推薦）/ 手動選擇」；Auto 時對 advise API 以 500ms debounce 呼叫（依 environment/os/reason/cores/memory/gpu 變動），顯示建議卡（圖示 VM/LXC + reasons 列表）；提交時 `requested_mode: "auto"` + `resource_type` 用建議值，範本欄位區依建議型別切換（沿用手動模式既有欄位元件）。advisor 停用（400）→ 自動退回手動並提示。
- 登入頁：載入時打 `/login/methods`（公開端點）；ldap=true 顯示 Tabs「Email / 校園帳號」；校園分頁 username+password 打 `/login/ldap`，成功後與既有登入相同流程存 token。
- 設定頁兩區塊：react-hook-form + zod，數字欄位範圍與後端 Field 約束一致；LDAP 區塊含「測試連線」按鈕（呼叫 test endpoint 顯示結果 toast）；bind password 欄位 placeholder「已設定（留空表示不變）」。
- 完成後：`bun run lint`、`bun run build` 必須全過。

- [x] C6-1 後端起 dev server → regenerate client
- [x] C6-2 admin.monitoring 頁 + 側欄入口
- [x] C6-3 configuration 治理/LDAP 區塊 + 測試連線
- [x] C6-4 applications-create Auto 模式 + 登入頁 LDAP 分頁 + my-resources badges
- [x] C6-5 `bun run lint && bun run build` 全過
- [x] C6-6 Commit：「模組C運維治理: 前端監控/設定/Auto申請/LDAP登入 (C6)」

---

### Task C7: 收尾 — 全量驗證 + 文檔

**Files:**
- Modify: `CLAUDE.md`（架構段落：services/monitoring、services/governance、infrastructure/ldap、排程 task 清單、`/monitoring`、`/governance`、`/admin/ldap-config`、`/login/ldap` API）
- Modify: `docs/superpowers/plans/2026-07-04-governance-module-c.md`（勾選完成項）

**步驟：**
- [x] C7-1 後端全量：`uv run ruff check . && uv run mypy .` + `docker compose exec backend bash scripts/tests-start.sh -x`（或本地 `bash ./scripts/test.sh`）；新增測試全綠、既有測試無回歸
- [x] C7-2 檢查 4 個 migration 鏈：`alembic heads` 單一 head；`alembic upgrade head` 在 dev DB 成功
- [x] C7-3 CLAUDE.md 更新（模組C架構與 API）
- [x] C7-4 Commit：「模組C運維治理: 測試收尾與文檔更新 (C7)」

---

## Self-Review 紀錄

- Spec 覆蓋：C-1 監控/告警→C1+C2；C-2 治理設定/TTL/閒置→C2(config)+C3；C-3 Auto 判斷→C4；C-4 LDAP→C5；C-5 前端→C6；RBAC 表→各 task 路由權限；測試策略→各 task 測試 + C7。
- 型別一致性：`GovernanceConfig` 由 C2 產出、C3（ttl/idle 欄位）與 C4（workload_advisor_enabled）消費；`set_auto_stop` 簽名與 repositories/resource.py 現況一致；`Token` 由既有 auth 流程產出。
- 已知風險：`repositories/vm_request.py` create 欄位帶入方式需在 C4 實作時現場確認；login 路由檔名/前端登入頁檔名在 C6 現場確認。
