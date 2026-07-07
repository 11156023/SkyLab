# 模組 D：安全防禦與性能工程 — 設計文件

日期：2026-07-04
狀態：已核准（使用者確認：兩段式處置、資源級豁免標記、既有 runner 並行化、
兩層式 pytest 壓測；並補三個實作注意點 — snapshot 不得阻塞 suspend、
mining_checked_at 無論命中與否都更新、provision 併發用獨立 Semaphore 隔離）

## 目標

1. 反挖礦機制：偵測長期 CPU 滿載 → 快照存證 → 強制暫停 VM → 告警與通知；
   帳號停權由管理員人工一鍵確認（兩段式，降低誤判代價）。
2. 克隆請求異步化：消除 API 同步阻塞與 scheduler 循序 clone 瓶頸，
   支撐 200 名學生同時申請 2Core/4GB VM。
3. 壓力測試：以可重跑的 pytest 驗證「API 不阻塞、隊列能消化、不重不漏」。

## 既有基礎（直接複用，不重造）

- 模組 C：`GovernanceConfig` singleton（管理 UI 已有治理分頁）、
  閒置偵測掃描骨架（RRD day timeframe + 輪替批次 + `idle_checked_at` 游標）、
  `AlertEvent` + email 通知、`average_cpu_percent` 純函式。
- PVE infra：`create_snapshot`、`control(action)`（start/stop/suspend/…）、
  `get_rrd_data`、`clone_vm/clone_lxc`。
- `BackgroundTaskRunner`（in-memory：併發上限、retry、cancel、graceful shutdown、
  task_id 去重）。
- VM request 防重複管線：`SELECT FOR UPDATE SKIP LOCKED` + `migration_status`
  檢查（`_adopt_or_provision_due_request`）— 天然支援並行 worker。
- `User.is_active`（停權）、`Group.owner_id`（老師）、audit log。

## D-1 反挖礦機制（Anti-Mining）

### 偵測 — 排程 task `process_mining_detection`

複用閒置偵測骨架（方向相反）：

- 每 tick 掃描 running VM 中 `mining_checked_at` 最舊的前
  `mining_scan_batch_size` 台（輪替掃描、每 tick 有界）。
- 對每台抽 RRD（day timeframe）算過去 `mining_window_hours` 平均 CPU
  （複用 `average_cpu_percent`）。
- 命中條件（全部成立）：
  1. 平均 CPU ≥ `mining_cpu_threshold_percent`（預設 90%）；
  2. 視窗內有效樣本足夠（覆蓋 ≥ 視窗的 2/3，防資料稀疏誤判）；
  3. `Resource.mining_exempt = false`；
  4. 該 vmid 無未結案（detected/suspended）MiningIncident。
- **無論是否命中，每台處理完一律更新 `mining_checked_at`**（含抽 RRD 失敗），
  否則未命中的 VM 會永遠卡在最舊清單被重複掃描，其他 VM 輪不到。
- 決策邏輯為純函式 `decide_mining_action(avg_cpu, sample_coverage, exempt,
  has_open_incident, threshold, …) -> MiningAction`，可無 DB 單測。

### GovernanceConfig 擴充（migration gov05）

- `mining_detection_enabled: bool = True`
- `mining_cpu_threshold_percent: float = 90.0`（範圍 50–100）
- `mining_window_hours: int = 6`（範圍 1–72）
- `mining_scan_batch_size: int = 20`（範圍 1–200）
- `mining_auto_suspend: bool = True`（false 時僅告警不暫停，處置全人工）

### Model — `MiningIncident`（新表，migration gov05）

id（uuid）、vmid（index）、user_id（FK user）、node、resource_type、
avg_cpu、window_hours、snapshot_name（nullable，存證失敗為 null）、
status（enum：detected / suspended / banned / dismissed，index）、
detected_at、suspended_at、reviewed_by（FK user，nullable）、reviewed_at、
review_note（nullable）。

`Resource` 加欄位：`mining_exempt: bool = False`、
`mining_checked_at: datetime | None`（輪替游標，同 idle_checked_at 模式）。

### 處置狀態機（兩段式）

```
detected ──自動──▶ suspended ──管理員──▶ banned（停權）
                        └──管理員──▶ dismissed（誤判解除）
```

自動段（`mining_response_service.respond(incident)`，偵測命中後立即執行）：

1. 建 MiningIncident（status=detected）+ audit log。
2. 快照存證：`create_snapshot(name=f"mining-{yyyymmddHHMM}", description=...)`。
   **存證是 best-effort：包 try/except 並設逾時（等待 PVE snapshot 任務
   最多 60 秒，逾時視同失敗），失敗只記 log 與 snapshot_name=null，
   絕不阻塞下一步暫停。** 挖礦 VM 處於極高 CPU/IO 壓力，snapshot 可能
   卡頓或失敗，暫停才是止血動作（既有 `create_snapshot` 內部以
   `basic_blocking_task_status` 阻塞等待，需增加可設逾時的等待路徑）。
3. 暫停 VM：qemu 用 `control("suspend")`；LXC 用 `control("stop")`
   （PVE 的 LXC suspend 為實驗性功能，不可靠）。成功 → status=suspended。
   `mining_auto_suspend=false` 時跳過 2–3，事件停留在 detected。
4. 建 `AlertEvent`（scope=vm、metric=cpu、message 註明疑似挖礦）。
5. Email 通知：管理員（is_superuser 或 role=admin）+ 擁有者所屬全部群組的
   owner（老師）。每封各自 try/except。

人工段（admin API）：

- `POST /mining-incidents/{id}/ban`：`User.is_active=false` + status=banned +
  reviewed_by/at + audit log（帳號停權後既有 JWT 於下次驗證失效）。
- `POST /mining-incidents/{id}/dismiss`：恢復 VM（qemu `resume` / LXC `start`，
  best-effort）+ status=dismissed + 可選 `exempt=true` 一併設 `mining_exempt`。
- `GET /mining-incidents?status=`：清單（admin）。

### RBAC

| 能力 | student | teacher | admin |
|---|---|---|---|
| 查看/處理挖礦事件、設豁免 | ✗ | ✗ | ✓ |
| 收到自己群組學生的挖礦通知 | ✗ | ✓（email） | ✓（email） |

## D-2 克隆並行化（Async Provisioning）

### Scheduler fan-out

- `process_due_request_starts` 不再於 tick 內循序 clone：對每個 due request
  改為 `submit_sync(process_single_request_start, request_id,
  task_id=f"provision-{request_id}")` 丟進 BackgroundTaskRunner 並行執行。
- 防重複三層保障（前兩層為既有機制，不動）：
  1. DB `SELECT FOR UPDATE SKIP LOCKED` 行鎖；
  2. `migration_status=running` / `vmid is not None` 再檢查；
  3. runner `task_id` 去重（同 request 已在跑則跳過）。
- tick 本身只做輕量查詢與 submit，秒級返回；rebalance/migration 邏輯位置不變。

### 併發隔離（獨立 Semaphore）

- clone 是 PVE 磁碟 I/O 重活。**provision 任務不共用 runner 的全域
  semaphore，改用獨立 `asyncio.Semaphore(provision_max_concurrency)`**，
  避免克隆佔滿全部 runner slot 導致發信、狀態同步等輕量任務飢餓。
- `GovernanceConfig.provision_max_concurrency: int = 4`（範圍 1–16；
  變更後下個 tick 生效，重建 semaphore）。
- 實作：不改 runner 介面 — 提交的 coroutine factory 內先
  `async with provision_semaphore` 再執行 clone（wrapper 模式，最小侵入）。

### API 202 化

- `POST /vm/create`、`POST /lxc/create`（admin 直接建立）：改為驗證 + 規劃後
  立即回 202 `{task_id, vmid?, message}`，clone 走背景任務；失敗記 audit log
  與資源狀態（前端以既有 pending/creating 機制輪詢呈現）。
- 學生申請路徑（`POST /vm-requests/`）本已不阻塞，不改介面。

## D-3 壓力測試（兩層式 pytest）

- **層 1 — API 併發**（`tests/performance/test_concurrent_vm_requests.py`）：
  monkeypatch clone 為短 sleep，以 200 個併發 `POST /vm-requests/` 驗證：
  全部 2xx、無 5xx、p95 延遲 < 2s、DB 恰好 200 筆（不重不漏）。
- **層 2 — 隊列吞吐**（`tests/performance/test_provision_fanout.py`）：
  塞 200 個 due requests、fake clone = sleep 0.05s，驗證：
  同時在跑的 clone 數 ≤ `provision_max_concurrency`、每個 request 恰好被
  clone 一次、總耗時接近 `200/併發 × 單次耗時`（顯著優於循序）。
- 標記 `@pytest.mark.performance`；不依賴真 PVE/Redis/SMTP。

## 前端（模組 D 收尾批次）

- `/admin/monitoring` 加「挖礦事件」面板：未結案清單、事件詳情
  （平均 CPU、視窗、快照名）、「停權帳號」「誤判解除（可選加豁免）」按鈕。
- `/admin/configuration` 治理分頁加「反挖礦」區塊（enabled/閾值/視窗/批次/
  auto_suspend）與「克隆併發上限」欄位。
- 資源管理（admin resources 表）加 mining_exempt 豁免開關。
- 完成後 regenerate OpenAPI client（含 legacy facade 更新）。

## 測試策略

- `decide_mining_action` 表驅動：低於閾值、命中、豁免、已有未結案事件、
  樣本覆蓋不足、偵測停用。
- 處置服務（mock PVE）：snapshot 失敗仍暫停、suspend 失敗事件停在 detected、
  LXC 走 stop、通知對象（管理員+群組 owner）、ban/dismiss 狀態轉移與 audit。
- fan-out 防重複與併發上限（層 2 壓測涵蓋）。
- 全程不需真實 PVE / SMTP / Redis。

## 交付批次

| 批次 | 內容 |
|---|---|
| D1 | 偵測純函式 + GovernanceConfig/Resource/MiningIncident migration（gov05） |
| D2 | 處置管線（snapshot/suspend/通知）+ 排程 task + mining-incidents API |
| D3 | 克隆 fan-out + 獨立 Semaphore + /vm/create、/lxc/create 202 化 |
| D4 | 兩層式壓測 |
| D5 | 前端（事件面板/設定區塊/豁免開關）+ regenerate client |
| D6 | 收尾：全量驗證 + CLAUDE.md |

## 範圍限制與假設

- 偵測僅基於 PVE RRD 的 CPU 均值（無法看 VM 內部行程）；加密貨幣挖礦的
  網路特徵（礦池連線）偵測不在本模組範圍。
- 快照存證品質取決於 PVE 當下負載，允許失敗（best-effort）。
- 單 backend 行程前提與既有 scheduler 一致；DB SKIP LOCKED 語意已為未來
  多 worker 保留擴展空間，本模組不引入 Redis 隊列。
- 停權僅設 `is_active=false`；不刪除使用者資料與 VM（留存證據）。
