# 模組 E：教學體驗（Teaching Experience）設計文件

日期：2026-07-04
分支：`feature/module-e-teaching`
狀態：已核准（使用者於 brainstorming 階段核准）

## 目標

以三個子模組提升教學場景的可用性：

1. **教師效率**：一鍵環境重置（E1）、配置文件分發（E2）、學生進度熱圖（E3）
2. **學生體驗**：自定義快照（E4）、協作實驗室 Pair Mode（E5）
3. **系統韌性**：批次動態資源調整（E6）、資源配額（E7）、快照自動清理（E8）

## 關鍵決策（已與使用者確認）

| 決策點 | 選擇 |
|---|---|
| 一鍵重置語義 | 初始快照 rollback（provision 時自動建 `skylab-init` 受保護快照） |
| 配置分發通道 | QEMU 走 guest agent `file-write`；LXC 走 node SSH `pct push` |
| 配額粒度 | 群組預設 + 個人覆寫 |
| 實作範圍 | E1–E8 一次完成，於本分支 |

## 整體架構

全面沿用既有分層（Routes → Services → Infrastructure），零新基礎設施：

```
backend/app/
├── api/routes/
│   ├── teaching.py            # 新增：config-push、heatmap、batch-spec
│   ├── quotas.py              # 新增：配額 CRUD + my-usage
│   ├── pair_sessions.py       # 新增：協作 session 管理
│   └── resource_details.py    # 擴充：reset、init-snapshot、快照權限/上限
├── services/
│   ├── teaching/              # 新增 package
│   │   ├── config_push_service.py   # 分發任務編排（fan-out + 進度）
│   │   ├── progress_service.py      # 熱圖資料聚合
│   │   └── batch_spec_service.py    # 批次規格調整
│   ├── resource/
│   │   ├── quota_service.py         # 新增：配額計算 + 執法
│   │   └── reset_service.py         # 新增：一鍵重置編排
│   ├── governance/
│   │   ├── snapshot_cleanup_policy.py   # 新增：純函式（清理資格判定）
│   │   └── snapshot_cleanup_service.py  # 新增：掃描 + 刪除協調
│   └── classroom/             # 擴充：SessionMode.pair + 雙人輸入
├── infrastructure/proxmox/
│   └── guest.py               # 新增：agent file-write / pct push
└── models/
    └── resource_quota.py      # 新增：ResourceQuota
```

前端新增：老師教學面板（熱圖 + 分發 + 批次調整）、快照管理 UI、協作邀請、admin 配額頁、governance 設定擴充。

## E1 一鍵環境重置

**初始快照建立**
- Provision 完成點（`services/scheduling/provision_pool` 與 `services/vm/batch_provision_service` 兩條路徑）在 VM/LXC 就緒後呼叫 `create_snapshot(name="skylab-init", description="SkyLab 初始快照（受保護）")`。
- 快照失敗不阻斷 provision（記 warning log + audit），該 VM 之後顯示「重置不可用」。

**重置 API**
- `POST /resources/{vmid}/reset`：權限 = VM owner、所屬群組老師、admin。
- 流程（worker 背景任務，API 回 202 + task id）：
  1. 檢查 `skylab-init` 快照存在，否則 400（`AppError`）
  2. 記錄目前電源狀態；running 則先 stop（強制）
  3. `rollback_snapshot(skylab-init)`
  4. 原本為 running 則重新 start
  5. Audit log（操作者、vmid、結果）
- `POST /resources/{vmid}/init-snapshot`：老師/admin 為舊 VM 補建初始快照；若已存在回 409。

**前端**：資源卡片與詳情頁「還原初始狀態」按鈕 + 二次確認 dialog（明示會丟失快照後所有變更）；task 進度以既有 toast/輪詢呈現。

## E2 配置文件分發

**Infrastructure（`infrastructure/proxmox/guest.py`）**
- `write_file_qemu(node, vmid, path, content: bytes)`：Proxmox API `POST /nodes/{node}/qemu/{vmid}/agent/file-write`（base64）。前置檢查 agent ping；失敗回明確錯誤（agent 未安裝/未開機）。
- `write_file_lxc(node, vmid, path, content: bytes)`：SSH 至 node，暫存檔 + `pct push`（含 `--perms`），完成後清理暫存。
- 檔案大小上限 **1 MB**（agent file-write 為 JSON base64，不適合大檔；超過回 413）。

**Service（`services/teaching/config_push_service.py`）**
- `start_push(file, target_path, vmids, requested_by) -> task_id`：驗證權限（老師僅能選自己群組成員的 VM）、逐 VM fan-out（`asyncio.Semaphore` 上限沿用 `provision_max_concurrency`），任務狀態存 in-memory worker + 逐 VM 結果（成功/失敗+原因）。
- 目標路徑白名單驗證：必須為絕對路徑、拒絕含 `..`。

**API**
- `POST /teaching/config-push`（multipart：file、target_path、vmids[]）→ 202 `{task_id}`
- `GET /teaching/config-push/{task_id}` → 逐 VM 進度/結果

**前端**：老師頁群組選擇 → VM 多選表格 → 上傳 + 路徑輸入 → 逐台結果表（成功綠/失敗紅+原因）。

## E3 學生進度熱圖

**Service（`services/teaching/progress_service.py`）**
- 聚合 Proxmox cluster resources（既有 monitoring 基礎）：每台學生 VM 的 `status`、`cpu%`、`mem%`、`uptime`。
- 「長期無動靜」判定：status=stopped 或 uptime > 0 但 CPU 長期趨近 0（直接以當下 cpu < 1% 且 uptime > 1h 標示 `stale`，不做歷史查詢以控制成本）。

**API**
- `GET /teaching/heatmap?group_id=`：老師僅能查自己管理的群組；admin 可查全部。回傳 `[{vmid, name, owner_name, status, cpu_percent, mem_percent, uptime_seconds, activity: running|idle|stale|stopped}]`。

**前端**：格狀熱圖（一格一學生 VM）：灰=關機、綠=運行、依 CPU 由淺綠→橘→紅漸層、深灰=stale；hover 顯示明細；TanStack Query 30 秒 refetch。

## E4 學生自定義快照

既有 `/resources/{vmid}/snapshots` CRUD 之上補強：

- **權限收斂**：owner 可管理自己 VM 的快照；老師/admin 可管理群組內 VM。
- **保護 `skylab-init`**：`delete_snapshot` 對 `skylab-init` 回 403（僅 admin 可刪）。
- **數量上限**：`GovernanceConfig.student_snapshot_max_count`（預設 3，範圍 1–10；不含 `skylab-init`）。建立時超限回 409，提示先刪舊快照。

**前端**：資源詳情頁快照分頁 — 列表（名稱/建立時間/備註）、建立 dialog（名稱 + 備註，如「安裝 Nginx 前」）、一鍵回滾（二次確認）、刪除。

## E5 協作實驗室（Pair Mode）

擴充 `services/classroom`：

- `SessionMode` 新增 `pair`。pair session：上游 = 學生 VM 的 VNC，下游 = owner + 受邀者，**兩人皆可輸入**（`_subscriber_reader` 對 pair mode 放行所有成員輸入，不走 `set_controller` 單一控制權）。
- 邀請模型：owner 對同群組成員發起；一個 VM 同時僅一個 pair session；受邀者接受後取得 watch WS 權限。Session 生命週期複用 `VncSessionManager`（含 upstream 斷線清理）。

**API（`api/routes/pair_sessions.py`）**
- `POST /pair-sessions`：`{vmid, invitee_user_id}` → 建立 session（owner 專屬）
- `GET /pair-sessions/mine`：我發起的 + 邀請我的
- `DELETE /pair-sessions/{session_id}`：owner 或 admin 結束

**前端**：VM 詳情「邀請協作」（選同群組成員）；受邀者在資源頁看到「協作邀請」卡可加入；雙方進入同一 VNC 畫面。

## E6 動態資源調整（批次）

**Service（`services/teaching/batch_spec_service.py`）**
- `start_batch_spec(vmids | group_id, cores?, memory_mb?, requested_by) -> task_id`
- 每台 VM：先過 E7 配額檢查（以 VM owner 計）→ 呼叫 Proxmox set config（LXC 即時生效；QEMU 更新 config，若無法 hotplug 則結果標記 `needs_restart`）→ 逐台結果。
- Fan-out 併發沿用 `asyncio.Semaphore(provision_max_concurrency)`。

**API**
- `POST /teaching/batch-spec` → 202 `{task_id}`；`GET /teaching/batch-spec/{task_id}` 逐台結果（ok / needs_restart / quota_exceeded / error）。
- 權限：老師限自己群組；admin 不限。

**前端**：老師群組頁「批次調整規格」dialog（cores/memory 目標值）→ 結果表（含「需重啟生效」名單，可一鍵重啟選取機器——複用既有電源操作）。

## E7 資源配額

**Model（`models/resource_quota.py`）**
```python
class ResourceQuota(SQLModel, table=True):
    id: uuid
    scope: "group" | "user"          # enum
    group_id: uuid | None            # scope=group 時必填
    user_id: uuid | None             # scope=user 時必填（覆寫）
    max_cpu_cores: int               # 預設 8
    max_memory_mb: int               # 預設 16384
    max_disk_gb: int                 # 預設 100
    max_instances: int               # 預設 5
```
- 解析順序：user 覆寫 → 其所屬 group 中最大值 → 系統預設（無列時視為不限或內建預設；採**內建預設 8C/16G/100G/5 台**，避免無配置時完全不設防）。

**Service（`services/resource/quota_service.py`）**
- `get_effective_quota(user_id) -> Quota`（純函式解析 + DB 查詢分離，policy 可單測）
- `get_usage(user_id) -> Usage`：以 DB resource 記錄加總（cores/memory/disk/台數）
- `check_quota(user_id, delta) -> None | AppError(409)`：執法點呼叫

**執法點**（四處，皆呼叫 `check_quota`）：
1. VM request 建立（`vm_request_service.create`）
2. Batch provision
3. Spec change 審核通過時
4. E6 批次調整（逐台）

**API（`api/routes/quotas.py`）**
- `GET/POST/PUT/DELETE /quotas`（admin）：群組與個人覆寫管理
- `GET /quotas/my-usage`：目前用量 + 有效配額（所有登入者）

**前端**：admin 配額管理頁（群組表 + 使用者覆寫表）；學生資源頁頂部用量條（CPU/RAM/Disk/台數 對 配額）。

## E8 快照自動清理

**GovernanceConfig 新欄位**
```python
snapshot_cleanup_enabled: bool = True
snapshot_retention_days: int = 7      # ge=1, le=90
student_snapshot_max_count: int = 3   # ge=1, le=10（E4 用）
```

**Policy（`services/governance/snapshot_cleanup_policy.py`，純函式）**
- `is_cleanup_eligible(snapshot, now, retention_days) -> bool`：`snaptime` 超期、名稱非 `skylab-init`、非 Proxmox `current` 偽快照。

**Service + Scheduler**
- `process_snapshot_cleanup`：掛入 `run_scheduler` 既有循環（批次掃描學生 VM，沿用 `idle_scan_batch_size` 式的批次上限概念，新欄位不另設、直接批 20）。刪除後寫 audit log + 通知 VM owner（沿用既有通知機制）。

**前端**：governance 設定頁新增三欄位（開關、保留天數、學生快照上限）。

## 資料庫遷移

一支 Alembic migration：
1. `resource_quota` 資料表
2. `governance_config` 加三欄位（帶 server_default）

## 錯誤處理原則

- 全部走 `AppError(status_code, message)` + 全域 handler。
- 批次操作（E2/E6）單台失敗不中斷整批，逐台記錄結果。
- Proxmox/SSH 呼叫失敗訊息需可讀（區分 agent 未裝、VM 關機、逾時）。

## 測試策略

- **純函式單測**：quota 解析/計算、快照清理資格、重置前置條件、熱圖 activity 判定。
- **Route 測試**：mock Proxmox operations / SSH，驗證權限（owner/老師/admin 邊界）、202 流程、配額 409。
- **前端**：`tsc` + biome + 既有 build；API client 重新生成。

## 非目標（YAGNI）

- 不做快照排程備份（僅清理）
- 不做 pair mode 三人以上協作
- 不做配額的磁碟即時掃描（以 DB 記錄為準）
- 不做熱圖歷史回放
