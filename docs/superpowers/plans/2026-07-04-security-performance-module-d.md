# 模組 D：安全防禦與性能工程 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 反挖礦兩段式處置（自動存證+暫停、人工停權）、克隆請求並行化（獨立 Semaphore + API 202）、兩層式 pytest 壓測。

**Architecture:** 偵測複用模組 C 閒置掃描骨架（RRD 均值 + 輪替游標 + 有界批次），決策為純函式；處置為狀態機（detected→suspended→banned/dismissed），snapshot best-effort 絕不阻塞 suspend。克隆 fan-out 到既有 BackgroundTaskRunner，以獨立 `asyncio.Semaphore` 隔離重 I/O，防重複靠既有 DB SKIP LOCKED + runner task_id 去重。不引入 Redis 隊列。

**Tech Stack:** FastAPI + SQLModel + Alembic、proxmoxer（既有 infra）、既有 BackgroundTaskRunner、pytest + anyio。

**Spec:** `docs/superpowers/specs/2026-07-04-security-performance-module-d-design.md`

## Global Constraints

- 遵循 Routes → Services → Infrastructure 分層；PVE 呼叫只出現在 infrastructure。
- 反挖礦決策邏輯為純函式（輸入資料 + now，輸出動作），不碰 DB/PVE/SMTP。
- Email 一律 try/except 包裹；排程 task 整體 try/except，失敗 log 後 return 0。
- 掃描每 tick 有界（≤ `mining_scan_batch_size`）；**無論命中與否、成敗與否，每台處理完一律推進 `mining_checked_at`**（仿 `process_idle_detection` 的 finally 模式）。
- snapshot 存證 best-effort：等待逾時 60 秒，失敗只記 log，**絕不阻塞 suspend**。
- provision 併發用**獨立 `asyncio.Semaphore`**，不佔用 runner 全域 slot。
- 新表/新欄位一律 Alembic migration，rev id `gov05_mining`（down_revision 接 `gov04_ldap_config`）。
- ruff 必須全過；mypy 不得新增錯誤（全 repo 有既有基線）。
- 純邏輯測試不依賴真實 PVE/DB/SMTP/Redis。
- commit 訊息格式「模組D安全性能: <內容> (Dn)」。
- 前端改後端 API 後必須 regenerate OpenAPI client（D5 統一做）。

---

### Task D1: 偵測純函式 + migration gov05

**Files:**
- Create: `backend/app/models/mining_incident.py`
- Modify: `backend/app/models/governance_config.py`（加 5 欄位）
- Modify: `backend/app/models/resource.py`（加 `mining_exempt`、`mining_checked_at`）
- Modify: `backend/app/models/__init__.py`（export MiningIncident/MiningIncidentStatus）
- Create: `backend/app/alembic/versions/gov05_add_mining_detection.py`
- Create: `backend/app/services/security/__init__.py`
- Create: `backend/app/services/security/mining_policy.py`（純函式）
- Test: `backend/tests/services/test_mining_policy.py`

**Interfaces (Produces):**
```python
# models/mining_incident.py
class MiningIncidentStatus(str, enum.Enum):
    detected = "detected"; suspended = "suspended"
    banned = "banned"; dismissed = "dismissed"

class MiningIncident(SQLModel, table=True):
    __tablename__ = "mining_incidents"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    vmid: int = Field(index=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", index=True)
    node: str = Field(max_length=255)
    resource_type: str = Field(max_length=8)          # "qemu" | "lxc"
    avg_cpu: float
    window_hours: int
    snapshot_name: str | None = Field(default=None, max_length=128)
    status: MiningIncidentStatus  # sa Enum, index=True
    detected_at: datetime          # timezone=True, nullable=False
    suspended_at: datetime | None
    reviewed_by: uuid.UUID | None = Field(default=None, foreign_key="user.id")
    reviewed_at: datetime | None
    review_note: str | None = Field(default=None, max_length=1024)

# models/governance_config.py 加欄位
mining_detection_enabled: bool = True
mining_cpu_threshold_percent: float = Field(default=90.0, ge=50, le=100)
mining_window_hours: int = Field(default=6, ge=1, le=72)
mining_scan_batch_size: int = Field(default=20, ge=1, le=200)
mining_auto_suspend: bool = True
provision_max_concurrency: int = Field(default=4, ge=1, le=16)   # D3 消費

# models/resource.py 加欄位
mining_exempt: bool = False
mining_checked_at: datetime | None   # timezone=True，輪替游標

# services/security/mining_policy.py — 全部純函式
class MiningAction(str, enum.Enum):
    flag = "flag"; none = "none"

def cpu_stats(rrd: list[dict], *, window_hours: int, now: datetime) -> tuple[float, float] | None:
    # 回 (avg_cpu_percent, coverage)；coverage = 視窗內有 cpu 值的點數 / 期望點數
    # 期望點數 = window_hours * 2（day timeframe ≈ 每 30 分鐘一點）
    # 視窗內無任何有效點 → None
def decide_mining_action(*, avg_cpu: float | None, coverage: float,
                         exempt: bool, has_open_incident: bool,
                         threshold_percent: float) -> MiningAction:
    # avg_cpu None → none；exempt → none；has_open_incident → none
    # coverage < 2/3 → none（樣本不足防誤判）
    # avg_cpu >= threshold_percent → flag；否則 none
```

**要點：**
- migration gov05：`governance_config` ADD COLUMN ×6（含 provision_max_concurrency，D3 用）、`resource` ADD COLUMN ×2、CREATE TABLE `mining_incidents` + index（vmid、status、user_id）。server_default 對既有列：booleans 用 `'true'`/`'false'`、數值用字面量（仿 gov01/gov02 寫法）。
- `cpu_stats` 與 `lifecycle_policy.average_cpu_percent` 相似但多 coverage — 不改既有函式（閒置偵測不需要 coverage），放 mining_policy 自持。
- rrd 條目 `{"time": epoch, "cpu": 0..1}`；缺 cpu 鍵的點不計入有效點。

**Tests（表驅動，`test_mining_policy.py`）：**
- `cpu_stats`：滿視窗高 CPU → (≈98, ≈1.0)；半視窗有資料 → coverage≈0.5；空 rrd → None；缺 cpu 鍵的點忽略；視窗外的點過濾。
- `decide_mining_action`：命中（95%、coverage 0.9）→ flag；低於閾值 → none；exempt → none；已有未結案 → none；coverage 0.5 → none；avg_cpu None → none。

- [x] D1-1 models + migration gov05（先跑 `alembic heads` 確認接 gov04_ldap_config）
- [x] D1-2 mining_policy 純函式（測試先行 FAIL→PASS）
- [x] D1-3 `uv run ruff check .`；mypy 對新增檔案無錯誤；`alembic upgrade head` 成功
- [x] D1-4 Commit：「模組D安全性能: 挖礦偵測純函式 + MiningIncident/設定 migration (D1)」

---

### Task D2: 處置管線 + 排程 task + mining-incidents API

**Files:**
- Modify: `backend/app/infrastructure/proxmox/client.py`（`basic_blocking_task_status` 加 `timeout_seconds` 參數）
- Modify: `backend/app/infrastructure/proxmox/operations.py`（`create_snapshot` 加 `wait_timeout_seconds` 透傳）
- Create: `backend/app/repositories/mining.py`
- Modify: `backend/app/repositories/resource.py`（`list_mining_scan_candidates` — 仿 `list_idle_scan_candidates`，游標欄位換 `mining_checked_at`）
- Create: `backend/app/services/security/mining_service.py`（掃描 + 處置協調）
- Modify: `backend/app/services/scheduling/coordinator.py`（掛 `process_mining_detection` task）
- Modify: `backend/app/models/audit_log.py`（AuditAction 加 `mining_detected`、`mining_suspend`、`mining_ban`、`mining_dismiss`，仿 login_ldap_* 模式）
- Create: `backend/app/schemas/mining.py`（`MiningIncidentPublic`、`MiningDismissRequest`；`schemas/__init__.py` re-export）
- Create: `backend/app/api/routes/mining_incidents.py`（AdminUser）
- Modify: `backend/app/api/main.py`（註冊 router，prefix `/mining-incidents`，tags `["mining"]`）
- Test: `backend/tests/services/test_mining_service.py`

**Interfaces (Consumes):** D1 的 `MiningIncident`、`decide_mining_action`、`cpu_stats`、GovernanceConfig mining 欄位。

**Interfaces (Produces):**
```python
# infrastructure/proxmox/client.py
def basic_blocking_task_status(..., timeout_seconds: float | None = None) -> dict:
    # while 迴圈以 time.monotonic 計時；逾時 raise TimeoutError(f"PVE task {task_id} timed out")

# repositories/mining.py
def create_incident(*, session, vmid, user_id, node, resource_type,
                    avg_cpu, window_hours, now) -> MiningIncident   # status=detected
def get_incident(*, session, incident_id) -> MiningIncident          # 無 → NotFoundError
def has_open_incident(*, session, vmid) -> bool                      # detected/suspended
def list_incidents(*, session, status: MiningIncidentStatus | None, limit=200) -> list[MiningIncident]

# services/security/mining_service.py
def process_mining_detection() -> int        # 排程 task；回傳 flag 數
def respond_to_incident(session, incident, resource, pve_info, config) -> None
    # 1) snapshot：create_snapshot(node, vmid, rtype,
    #      snapname=f"mining-{now:%Y%m%d%H%M}", description="Mining evidence (auto)",
    #      wait_timeout_seconds=60)
    #    try/except（含 TimeoutError）→ 失敗 snapshot_name=None，繼續
    # 2) 暫停：qemu → control(node, vmid, "qemu", "suspend")
    #          lxc  → control(node, vmid, "lxc", "stop")
    #    成功 → status=suspended, suspended_at=now；失敗 → 停留 detected（log）
    # 3) AlertEvent(scope=vm, metric=cpu, message="疑似挖礦：...")（複用 governance_repo）
    # 4) email：alert_service._list_admin_emails(session) + 群組 owner
    #    （SELECT Group.owner_id JOIN GroupMember WHERE user_id=resource.user_id）
    #    每封 try/except
def ban_incident(*, session, incident_id, admin: User) -> MiningIncident
    # incident.status 必須是 detected/suspended，否則 BadRequestError
    # User.is_active=False + status=banned + reviewed_by/at + audit mining_ban
def dismiss_incident(*, session, incident_id, admin: User, exempt: bool, note: str | None) -> MiningIncident
    # 恢復 VM：qemu → control("resume")、lxc → control("start")，try/except best-effort
    # status=dismissed + reviewed_by/at/note；exempt=True → resource.mining_exempt=True
    # audit mining_dismiss

# api/routes/mining_incidents.py
GET  /mining-incidents?status=            → list[MiningIncidentPublic]   # AdminUser
POST /mining-incidents/{id}/ban           → MiningIncidentPublic
POST /mining-incidents/{id}/dismiss       body: {exempt: bool=False, note: str|None} → MiningIncidentPublic
```

**`process_mining_detection` 骨架（仿 `process_idle_detection`）：**
```python
MINING_RESCAN_MINUTES = 30
def process_mining_detection() -> int:
    try:
        flagged = 0; now = _utc_now()
        with Session(engine) as session:
            config = governance_repo.get_governance_config(session=session)
            if not config.mining_detection_enabled: return 0
            pve_map = _pve_resource_map()          # 同 lifecycle_service 寫法
            running_vmids = [vmid for vmid, i in pve_map.items()
                             if str(i.get("status") or "") == "running"]
            candidates = resource_repo.list_mining_scan_candidates(
                session=session, vmids=running_vmids,
                checked_before=now - timedelta(minutes=MINING_RESCAN_MINUTES),
                limit=config.mining_scan_batch_size)
            for resource in candidates:
                pve_info = pve_map.get(resource.vmid)
                if pve_info is None: continue
                try:
                    stats = _fetch_cpu_stats(resource, pve_info,
                        window_hours=config.mining_window_hours, now=now)
                    action = decide_mining_action(
                        avg_cpu=stats[0] if stats else None,
                        coverage=stats[1] if stats else 0.0,
                        exempt=resource.mining_exempt,
                        has_open_incident=mining_repo.has_open_incident(
                            session=session, vmid=resource.vmid),
                        threshold_percent=config.mining_cpu_threshold_percent)
                    if action is MiningAction.flag:
                        incident = mining_repo.create_incident(...)
                        audit_service.log_action(..., action="mining_detected", commit=False)
                        respond_to_incident(session, incident, resource, pve_info, config)
                        flagged += 1
                except Exception:
                    session.rollback()
                    logger.exception("Mining detection failed for vmid=%s", resource.vmid)
                finally:
                    # 無論命中/未命中/失敗都推進游標 — 否則低 CPU 的 VM 永遠佔住最舊清單
                    resource.mining_checked_at = now
                    session.add(resource); session.commit()
        return flagged
    except Exception:
        logger.exception("process_mining_detection failed"); return 0
```

**要點：**
- coordinator 掛法仿 `process_idle_detection_task`（延遲 import 避 cycle）。
- `mining_auto_suspend=False` 時 `respond_to_incident` 跳過步驟 1–2（事件停留 detected，仍發告警與 email）。
- 群組 owner 查詢放 mining_service 內部 helper `_teacher_emails(session, user_id)`；學生無群組 → 只通知管理員。
- schemas：`MiningIncidentPublic` 含全部欄位（id/vmid/user_id/node/resource_type/avg_cpu/window_hours/snapshot_name/status/detected_at/suspended_at/reviewed_by/reviewed_at/review_note）。

**Tests（mock PVE / email，`test_mining_service.py`）：**
- snapshot 拋 TimeoutError → 仍執行 suspend、incident.snapshot_name is None、status=suspended。
- suspend 拋例外 → status 停留 detected、不 crash。
- lxc 資源 → control 收到 "stop" 而非 "suspend"。
- `mining_auto_suspend=False` → 不呼叫 snapshot/control、status=detected、仍建 AlertEvent。
- ban：is_active=False、status=banned；對 dismissed 事件 ban → BadRequestError。
- dismiss(exempt=True)：qemu 收到 "resume"、resource.mining_exempt=True、status=dismissed。
- 游標推進：候選中含一台抽 RRD 失敗的 VM → 其 mining_checked_at 仍被更新。

- [x] D2-1 infra timeout + repo helpers（`list_mining_scan_candidates`、mining repo）
- [x] D2-2 mining_service 掃描與處置（測試先行 FAIL→PASS）
- [x] D2-3 AuditAction + schemas + routes + main.py 註冊 + coordinator 掛載
- [x] D2-4 ruff 全過；mypy 新增檔案無錯誤
- [x] D2-5 Commit：「模組D安全性能: 反挖礦偵測處置管線 + 事件 API (D2)」

---

### Task D3: 克隆 fan-out + 獨立 Semaphore + /vm/create、/lxc/create 202 化

**Files:**
- Create: `backend/app/services/scheduling/provision_pool.py`（獨立 semaphore 管理）
- Modify: `backend/app/services/scheduling/coordinator.py`（`process_due_request_starts` fan-out）
- Modify: `backend/app/api/routes/vm.py`（`POST /vm/create` 202）
- Modify: `backend/app/api/routes/lxc.py`（`POST /lxc/create` 202）
- Modify: `backend/app/schemas/resource.py`（`VMCreateResponse`/`LXCCreateResponse` 的 vmid/upid 改 optional + `task_id` 欄位）
- Test: `backend/tests/services/test_provision_pool.py`

**Interfaces (Consumes):** D1 的 `GovernanceConfig.provision_max_concurrency`。

**Interfaces (Produces):**
```python
# services/scheduling/provision_pool.py
_semaphore: asyncio.Semaphore | None = None
_semaphore_size: int = 0

def get_provision_semaphore(size: int) -> asyncio.Semaphore:
    # size 變更時重建（舊 semaphore 上等待者自然結束後被 GC）
def submit_provision(request_id: uuid.UUID, *, concurrency: int) -> str:
    # background_tasks.submit_factory(
    #     lambda: _provision_with_semaphore(request_id, concurrency),
    #     name="provision", task_id=f"provision-{request_id}")
    # runner task_id 去重：同 request 已在跑則 submit_factory 直接跳過（既有行為）
async def _provision_with_semaphore(request_id, concurrency) -> None:
    async with get_provision_semaphore(concurrency):
        await asyncio.to_thread(coordinator.process_single_request_start, request_id)

# coordinator.process_due_request_starts 改動（僅 provision 段）：
# 原：for request in active_requests: _ensure_request_running(...)（同步循序）
# 新：vmid 為 None 的 request → provision_pool.submit_provision(request.id, concurrency=config.provision_max_concurrency)
#     vmid 已存在的 request → 保留原地同步 ensure-started/migration 邏輯（輕量）
```

**要點：**
- fan-out 只針對「尚未 provision（vmid is None）」的 request；已 provision 的啟動檢查與 migration 維持原路徑（輕量、不佔 semaphore）。
- 防重複三層：runner `task_id=f"provision-{request_id}"` 去重（submit 時）→ `process_single_request_start` 內既有 SKIP LOCKED → `migration_status`/vmid 再檢查。**不改 `_adopt_or_provision_due_request` 邏輯**。
- `process_single_request_start` 已存在且自帶 session/例外處理，fan-out 直接複用。
- `/vm/create` 202 化：
  ```python
  @router.post("/create", status_code=202, response_model=VMCreateResponse)
  def create_vm(vm_data: VMCreateRequest, session: SessionDep, current_user: AdminUser):
      task_id = background_tasks.submit_sync(
          _run_create_vm, vm_data, current_user.id,
          name="admin-create-vm")
      return VMCreateResponse(vmid=None, upid=None, task_id=task_id,
                              message="VM 建立中，請稍後於資源列表查看")
  # _run_create_vm 開自己的 Session(engine)（route session 不能跨執行緒）
  ```
  `_run_create_vm`/`_run_create_lxc` 為 route 檔內模組級函式：`with Session(engine) as s: provisioning_service.create_vm(session=s, ...)`，整體 try/except + audit log 失敗記錄。
- schema 變更：`VMCreateResponse.vmid: int | None = None`、`upid: str | None = None`、`task_id: str | None = None`；`LXCCreateResponse` 同。**檢查前端使用點**（`grep createVm\(|createLxc\(` in frontend/src）：若有讀 `vmid` 的呼叫端，D5 一併改為輪詢 pending 清單。
- batch provisioning 路徑（`BatchProvisionService`）已有自己的背景機制，不動。

**Tests（`test_provision_pool.py`，無 DB — monkeypatch `coordinator.process_single_request_start`）：**
- 併發上限：fake provision = `await asyncio.sleep(0.05)` 記錄同時在跑數的峰值；提交 20 個、concurrency=4 → 峰值 ≤ 4。
- 去重：同一 request_id 提交兩次 → fake 只被呼叫一次。
- semaphore 重建：先 concurrency=2 提交完畢，再 concurrency=6 → `get_provision_semaphore(6)` 回新實例且上限生效。
- 全部完成：提交 N 個不同 id → fake 被呼叫恰 N 次。

- [x] D3-1 provision_pool + 測試（FAIL→PASS，需 `pytest-asyncio`/anyio 既有慣例）
- [x] D3-2 coordinator fan-out 改造（既有 scheduler 測試無回歸）
- [x] D3-3 /vm/create、/lxc/create 202 + schema 改造；`grep` 前端使用點記錄到 D5
- [x] D3-4 ruff 全過；mypy 新增檔案無錯誤
- [x] D3-5 Commit：「模組D安全性能: 克隆並行化 fan-out + 建立 API 202 (D3)」

---

### Task D4: 兩層式壓測

**Files:**
- Create: `backend/tests/performance/__init__.py`
- Create: `backend/tests/performance/test_provision_fanout.py`（層 2）
- Create: `backend/tests/performance/test_concurrent_vm_requests.py`（層 1）
- Modify: `backend/pyproject.toml`（`[tool.pytest.ini_options]` markers 加 `performance`）

**Interfaces (Consumes):** D3 的 `provision_pool.submit_provision`。

**層 2 — 隊列吞吐（無 DB，必跑）：**
```python
@pytest.mark.performance
async def test_200_requests_fanout_throughput(monkeypatch):
    in_flight = 0; peak = 0; done: set[uuid.UUID] = set()
    async def fake_provision(request_id):   # 掛在 asyncio.to_thread 目標上
        nonlocal in_flight, peak
        in_flight += 1; peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1; done.add(request_id)
    # monkeypatch provision_pool 的 to_thread 呼叫為 fake
    start = time.monotonic()
    ids = [uuid.uuid4() for _ in range(200)]
    for rid in ids: provision_pool.submit_provision(rid, concurrency=8)
    await _wait_all_done(lambda: len(done) == 200, timeout=15)
    elapsed = time.monotonic() - start
    assert peak <= 8
    assert len(done) == 200                    # 不重不漏
    assert elapsed < 200 * 0.02                # 顯著優於循序（循序需 4s）
```

**層 1 — API 併發（需 DB，跟隨 tests/api 既有 fixture；無測試 DB 環境時由 conftest gate 排除）：**
```python
@pytest.mark.performance
def test_200_concurrent_vm_request_submissions(client_factory, normal_user_token_headers, monkeypatch):
    # monkeypatch scheduler 立即觸發路徑為 no-op（提交不做 clone）
    # ThreadPoolExecutor(max_workers=50) 發 200 個 POST /api/v1/vm-requests/
    # （合法 payload：lxc/2c/2048MB/immediate；hostname 帶序號唯一）
    # 斷言：所有回應 status_code < 500；成功數 == 200（或 == rate limit 允許數，
    #        若觸發既有 _CREATE_RATE_LIMIT 則放寬 rate limit 設定後重跑）
    # 斷言：DB 中 VMRequest 筆數 == 成功數（不重不漏）
    # 斷言：p95 響應時間 < 2.0s
```

**要點：**
- 層 1 的 rate limit：`POST /vm-requests/` 掛 `_CREATE_RATE_LIMIT` — 測試內 monkeypatch rate limiter 為 no-op（無 Redis 環境本來就得 stub）。
- 兩檔皆 `pytestmark = pytest.mark.performance`；`pyproject.toml` markers 註冊避免 warning。

- [x] D4-1 層 2 測試（隨 D3 實作應直接 PASS；若揭露 bug 回修 D3）
- [x] D4-2 層 1 測試（本地無測試 DB 時記錄限制，docker compose 環境驗證）
- [x] D4-3 ruff 全過
- [x] D4-4 Commit：「模組D安全性能: 兩層式 200 併發壓測 (D4)」

---

### Task D5: 前端（挖礦事件面板 / 設定區塊 / 豁免開關）+ regenerate client

**Files:**
- Regenerate: `frontend/src/client/`（`bash ./scripts/generate-client.sh`）
- Modify: `frontend/src/client/legacy-services.ts`（加 `MiningIncidentsService` facade：`listIncidents`/`banIncident`/`dismissIncident`）
- Modify: `frontend/src/client/index.ts`（facaded 清單加 `MiningIncidentsService`）
- Create: `frontend/src/components/Admin/MiningIncidentsPanel.tsx`
- Modify: `frontend/src/routes/_layout/admin.monitoring.tsx`（掛面板，位置在 AlertsPanel 之後）
- Modify: `frontend/src/components/Admin/GovernanceConfigTab.tsx`（加「反挖礦」卡片 5 欄位 + 「克隆併發上限」欄位）
- Modify: 管理員資源頁（`grep` admin resources 表位置）加 mining_exempt 開關（呼叫既有 resource 更新 API；若無現成欄位更新 API，後端補 `PATCH /resources/{vmid}/mining-exempt`，AdminUser）
- Modify: D3 記錄的前端 `createVm`/`createLxc` 使用點（202 後不再有同步 vmid，改走 pending 輪詢）

**要點：**
- `MiningIncidentsPanel`：`useQuery(["miningIncidents"], listIncidents({status:"suspended"}))` + detected，30s refetch；每列顯示 vmid/平均 CPU/視窗/快照名/偵測時間；「停權帳號」按鈕（confirm dialog）→ ban mutation；「誤判解除」按鈕 → dialog（checkbox「同時加入豁免」+ note 欄）→ dismiss mutation；成功後 invalidate。
- GovernanceConfigTab 新卡片沿用既有 `numberField`/`switchField` helper，範圍與後端 Field 一致（threshold 50–100、window 1–72、batch 1–200、concurrency 1–16）。
- 豁免開關：資源列有 `mining_exempt` badge/switch（admin only）。
- 完成後 `bun run lint && bun run build` 全過（build 前先 `bunx --bun @tanstack/router-cli generate` 若有新 route）。

- [x] D5-1 regenerate client + facade 更新
- [x] D5-2 MiningIncidentsPanel + monitoring 頁掛載
- [x] D5-3 GovernanceConfigTab 反挖礦/併發區塊 + 豁免開關 + createVm 使用點修正
- [x] D5-4 `bun run lint && bun run build` 全過
- [x] D5-5 Commit：「模組D安全性能: 前端挖礦事件/設定/豁免 (D5)」

---

### Task D6: 收尾 — 全量驗證 + 文檔

**Files:**
- Modify: `CLAUDE.md`（services/security、排程 task 加 process_mining_detection、`/mining-incidents` API、provision fan-out 說明）
- Modify: `docs/superpowers/plans/2026-07-04-security-performance-module-d.md`（勾選完成項；**用 Edit 工具或 bash sed，勿用 PowerShell Set-Content — PS 5.1 編碼會毀中文**）

**步驟：**
- [x] D6-1 後端：`uv run ruff check .` 全過；`uv run pytest tests/services/ tests/performance/ -q` 模組 D 測試全綠、既有無回歸（Redis-backed 測試除外，屬既有環境限制）
- [x] D6-2 `alembic heads` 單一 head（gov05_mining）；dev DB `alembic upgrade head` 成功
- [x] D6-3 CLAUDE.md 更新
- [x] D6-4 Commit：「模組D安全性能: 測試收尾與文檔更新 (D6)」

---

## Self-Review 紀錄

- Spec 覆蓋：偵測+豁免+游標→D1/D2；兩段式處置（snapshot 60s 逾時不阻塞 suspend、LXC 用 stop、通知老師）→D2；fan-out+獨立 Semaphore+202→D3；兩層壓測→D4；前端→D5；文檔→D6。
- 型別一致性：`decide_mining_action`/`cpu_stats` 由 D1 產出、D2 消費；`provision_max_concurrency` 由 D1 migration 產出、D3 消費；`submit_provision` 由 D3 產出、D4 消費；`MiningIncidentPublic` 由 D2 產出、D5 消費。
- 使用者三注意點落點：snapshot 逾時（D2 infra timeout_seconds + best-effort）、mining_checked_at 無條件推進（D2 finally 骨架 + 專屬測試）、獨立 Semaphore（D3 provision_pool，不佔 runner slot）。
- 已知風險：前端 createVm 使用點數量未知（D3-3 現場 grep 記錄、D5-3 修正）；層 1 壓測依賴測試 DB 環境。
