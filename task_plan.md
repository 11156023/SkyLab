# SkyLab 系統完善計畫

> 建立時間：2026-04-23  
> 狀態：規劃中  
> 總工作量估算：8 個 Phase，約 60+ 個子任務

---

## 一覽表

| Phase | 名稱 | 優先 | 狀態 |
|-------|------|------|------|
| P1 | 高風險安全強化 | 🔴 緊急 | ⬜ 未開始 |
| P2 | 測試覆蓋補強 — Backend | 🔴 高 | ⬜ 未開始 |
| P3 | 前端 i18n 完整化 | 🔴 高 | ⬜ 未開始 |
| P4 | VM 資源管理補齊 | 🟡 中 | ⬜ 未開始 |
| P5 | 測試覆蓋補強 — Frontend | 🟡 中 | ⬜ 未開始 |
| P6 | Async 深化 + 技術債清理 | 🟡 中 | ⬜ 未開始 |
| P7 | 可觀測性 (Observability) | 🟢 低 | ⬜ 未開始 |
| P8 | 部署與 Infrastructure 強化 | 🟢 低 | ⬜ 未開始 |

---

## Phase 1 — 高風險安全強化 🔴

### 目標
補上目前已知的安全漏洞與可靠性風險，優先於任何功能工作。

### P1.1 Health Check 強化
- **檔案**：`backend/app/api/routes/utils.py`
- **現狀**：`/api/v1/utils/health-check/` 只回傳 `true`
- **目標**：依序檢查 PostgreSQL 連線、Redis 連線、Proxmox 可達性，各自 timeout，回傳 structured JSON
- **子任務**：
  - [ ] 加入 DB ping（`SELECT 1`）
  - [ ] 加入 Redis ping
  - [ ] 加入 Proxmox API 可達性 ping（catch timeout）
  - [ ] 回傳 `{ db: ok, redis: ok, proxmox: ok, overall: ok }` schema
  - [ ] 加入 `/api/v1/utils/readiness/` endpoint（供 Docker/K8s 使用）

### P1.2 Rate Limiting 擴展
- **現狀**：Rate limit 只套用於 AI API proxy
- **目標**：高危端點均有 rate limit
- **子任務**：
  - [ ] `POST /api/v1/login/access-token` — IP-based rate limit（防暴力破解）
  - [ ] `POST /api/v1/login/google` — IP-based rate limit
  - [ ] `POST /api/v1/password-recovery/*` — rate limit
  - [ ] `POST /api/v1/vm-requests/` — per-user rate limit
  - [ ] `POST /api/v1/batch-provision/` — per-user rate limit
  - [ ] 在 `infrastructure/redis/rate_limiter.py` 加入 IP-based 版本
  - [ ] 在 `core/security.py` 或 FastAPI middleware 實作統一注入點

### P1.3 JWT Token Revocation
- **現狀**：JWT 含 `ver:0` 但無黑名單機制，無法即時 revoke
- **目標**：Logout 後 token 立即失效
- **子任務**：
  - [ ] 設計 Redis-based token blacklist（key: `revoked:{jti}`, TTL = token 剩餘效期）
  - [ ] `POST /api/v1/login/logout` 寫入黑名單
  - [ ] `get_current_user` 依賴加入黑名單檢查
  - [ ] 前端 logout 流程呼叫 API 後清除 token

### P1.4 前端 ErrorBoundary
- **現狀**：無 React ErrorBoundary，任何未捕獲錯誤會導致整頁白屏
- **目標**：路由層與關鍵 widget 有 fallback UI
- **子任務**：
  - [ ] 建立 `frontend/src/components/ErrorBoundary.tsx`（class component）
  - [ ] 在 `_layout.tsx` 根層包覆 ErrorBoundary
  - [ ] VNC Console、AI Chat 等高風險元件各自包覆

---

## Phase 2 — 測試覆蓋補強（Backend）🔴

### 目標
優先補最可能 regression 的區域，每次重構前至少有一條守門測試。

### P2.1 Scheduler / Migration 狀態機
- **子任務**：
  - [ ] `tests/services/test_scheduler_coordinator.py`：測試 VM request 從 pending → provisioning → done 流程
  - [ ] 測試 scheduler poll 遇到 Proxmox 錯誤時不崩潰
  - [ ] 測試 migration job claim → worker 執行 → 完成/失敗 狀態流
  - [ ] 測試部署去重（`_ACTIVE_BY_REQUEST` lock）
  - [ ] 測試 cancel task 流程（設 cancel event → rollback）

### P2.2 Network Services
- **子任務**：
  - [ ] `tests/services/test_firewall_service.py`：mock Proxmox API，測試 rule CRUD
  - [ ] `tests/services/test_nat_service.py`：mock SSH，測試 HAProxy config 生成
  - [ ] `tests/services/test_reverse_proxy_service.py`：測試 Traefik config 生成
  - [ ] `tests/services/test_gateway_service.py`：mock SSH，測試 gateway 操作

### P2.3 API Routes（缺測試的端點）
- **子任務**：
  - [ ] `test_firewall.py`：CRUD + 權限
  - [ ] `test_deletion_requests.py`
  - [ ] `test_groups.py`：create、add member、batch provision
  - [ ] `test_batch_provision.py`
  - [ ] `test_spec_change_requests.py`
  - [ ] `test_tunnel.py`

### P2.4 WebSocket 行為測試
- **子任務**：
  - [ ] `tests/api/test_ws_jobs.py`：連線認證、snapshot 推送、斷線重連
  - [ ] `tests/api/test_ws_vnc.py`：認證失敗應 close 1008
  - [ ] `tests/api/test_ws_terminal.py`

### P2.5 Domain 單元測試
- **子任務**：
  - [ ] `tests/domain/test_placement_scoring.py`
  - [ ] `tests/domain/test_migration_eligibility.py`
  - [ ] `tests/domain/test_scheduling_policy.py`

---

## Phase 3 — 前端 i18n 完整化 🔴

### 目標
消除所有硬寫中文字串，讓英文 / 日文介面完整可用。

### P3.1 建立缺少的 namespace
- **現狀**：admin、firewall、groups、ai-management 等頁面無專屬 namespace
- **子任務**：
  - [ ] 新增 `locales/{en,zh-TW,ja}/admin.json`
  - [ ] 新增 `locales/{en,zh-TW,ja}/firewall.json`
  - [ ] 新增 `locales/{en,zh-TW,ja}/groups.json`
  - [ ] 新增 `locales/{en,zh-TW,ja}/aiManagement.json`
  - [ ] 新增 `locales/{en,zh-TW,ja}/reverseProxy.json`
  - [ ] 新增 `locales/{en,zh-TW,ja}/network.json`（NAT、Tunnel 共用）
  - [ ] 更新 `frontend/src/lib/i18n.ts` 載入新 namespace

### P3.2 頁面硬寫字串替換
- **子任務**（每個檔案一個子任務）：
  - [ ] `admin.ai-management.tsx` — 全部中文字串 → `t()`
  - [ ] `admin.ai-monitoring.tsx`
  - [ ] `admin.gateway.tsx`
  - [ ] `admin.domains.tsx`
  - [ ] `admin.configuration.tsx`
  - [ ] `admin.audit-logs.tsx`（已有部分，補齊剩餘）
  - [ ] `admin.ip-management.tsx`
  - [ ] `admin.migration-jobs.tsx`
  - [ ] `admin.deploy-logs.tsx`
  - [ ] `firewall.tsx`
  - [ ] `groups.tsx` / `groups_.$groupId.tsx`
  - [ ] `reverse-proxy.tsx`
  - [ ] `gpu-management.tsx`
  - [ ] `jobs.tsx`
  - [ ] `approvals.tsx` / `approvals_.$requestId.tsx`

### P3.3 驗證日文翻譯完整性
- **子任務**：
  - [ ] 比對 `ja/` 與 `zh-TW/` 所有 key，補齊缺漏

---

## Phase 4 — VM 資源管理補齊 🟡

### P4.1 VM 快照 UI
- **現狀**：`snapshot_service.py` 已完整（list/create/rollback/delete），前端無 UI
- **子任務**：
  - [ ] `resources_.$vmid.tsx` 加入「快照」Tab
  - [ ] 列出快照（名稱、時間、描述、vmstate）
  - [ ] 建立快照表單（snapname、description、vmstate checkbox）
  - [ ] 快照 rollback 確認 Dialog
  - [ ] 快照刪除確認 Dialog
  - [ ] 補齊 `compat.ts` 對應 API 方法
  - [ ] 補齊 i18n key（resourceDetail namespace）

### P4.2 資源配額 (Quota) 系統
- **現狀**：無任何 per-user/per-group CPU/Memory 上限
- **子任務**：
  - **Backend**：
    - [ ] 新增 `models/resource_quota.py`（QuotaPolicy: max_vcpu, max_memory_mb, max_vms, max_disk_gb）
    - [ ] 新增 `repositories/resource_quota.py`
    - [ ] 新增 `services/resource/quota_service.py`（check_quota_before_provision）
    - [ ] 在 `scheduler/coordinator.py` provision 前呼叫 quota check
    - [ ] Admin API：CRUD `/api/v1/admin/quotas/`
    - [ ] Alembic migration
  - **Frontend**：
    - [ ] `admin.configuration.tsx` 加入配額設定 UI
    - [ ] 申請 VM 時顯示目前用量 vs 上限

### P4.3 VM 效能監控（歷史時序）
- **現狀**：只有即時 CPU 百分比，無歷史
- **子任務**：
  - [ ] 新增 `api/routes/resource_stats.py`：呼叫 Proxmox RRD API
  - [ ] Schema：`ResourceStatsResponse`（timeframe: hour/day/week, data points）
  - [ ] Frontend：`resources_.$vmid.tsx` 加入效能圖表 Tab（用 recharts/shadcn Chart）
  - [ ] 支援 hour/day/week 切換

### P4.4 VM 定時備份設定
- **子任務**：
  - [ ] Backend：`api/routes/resource_details.py` 加入 backup schedule CRUD（呼叫 Proxmox vzdump API）
  - [ ] Frontend：資源詳情頁加入備份排程設定

---

## Phase 5 — 測試覆蓋補強（Frontend）🟡

### P5.1 Vitest 單元測試補充
- **現狀**：只有 5 個測試檔
- **子任務**：
  - [ ] `services/jobs.test.ts`：`connectJobsWebSocket` 重連邏輯
  - [ ] `services/pendingResources.test.ts`
  - [ ] `services/deletingResources.test.ts`
  - [ ] `hooks/useAuth.test.ts`
  - [ ] `lib/i18n.test.ts`：各 namespace key 完整性驗證（防止翻譯 key 缺漏）

### P5.2 E2E Playwright 補強
- **現狀**：E2E 只覆蓋 login/signup/settings
- **子任務**：
  - [ ] `tests/vm-request.spec.ts`：申請 VM → 出現在 pending 列表
  - [ ] `tests/groups.spec.ts`：建立群組 → 加成員 → batch provision
  - [ ] `tests/firewall.spec.ts`：開啟防火牆 → 新增規則
  - [ ] `tests/ai-api.spec.ts`：申請 API key → admin 審核 → 使用
  - [ ] `tests/admin.spec.ts`：admin 登入 → 查看 audit log、user 管理

---

## Phase 6 — Async 深化 + 技術債清理 🟡

### P6.1 引入 AsyncSession
- **子任務**：
  - [ ] 建立 `core/db_async.py`（AsyncEngine + AsyncSessionLocal）
  - [ ] 建立 `api/deps/async_db.py`（`AsyncSessionDep`）
  - [ ] 先改寫 WebSocket handlers 使用 AsyncSession
  - [ ] 改寫 Scheduler poll loop 使用 AsyncSession
  - [ ] 補對應 async repository 方法（從高 I/O 熱點開始）

### P6.2 舊相容層退役
- **子任務**：
  - [ ] 確認 `domain/pve_placement/`、`domain/pve_scheduling/` 無新使用者（grep）
  - [ ] 將測試 import 全改到新路徑
  - [ ] 刪除 `domain/pve_*` package

### P6.3 Coordinator / Placement Service 拆分
- 按 BACKEND_FUTURE.md 建議：
  - [ ] `coordinator.py` → 拆出 `runtime.py`、`migration_jobs.py`、`reconcile.py`
  - [ ] `placement_service.py` → 拆出 `plan_builder.py`、`reservation_solver.py`、`storage_selection.py`

### P6.4 Frontend SDK 統一升級
- **現狀**：`compat.ts` + `legacy-services.ts` 手工 shim 層
- **子任務**：
  - [ ] 評估全面升級到 `@hey-api/openapi-ts` 新風格的工作量
  - [ ] 建立遷移策略（逐 service 替換）
  - [ ] 依序遷移各 service 檔案
  - [ ] 最終刪除 `legacy-services.ts` 與 `core/` shim

---

## Phase 7 — 可觀測性 (Observability) 🟢

### P7.1 Structured Logging
- **子任務**：
  - [ ] 引入 `structlog` 或設定 `logging.config.dictConfig` 輸出 JSON
  - [ ] 統一 log format：`timestamp, level, logger, request_id, user_id, message`
  - [ ] Request ID middleware（`core/request_context.py` 已有框架，補齊注入）

### P7.2 Prometheus Metrics
- **子任務**：
  - [ ] 安裝 `prometheus-fastapi-instrumentator`
  - [ ] 在 `main.py` 掛載 `/metrics` endpoint（僅內網可達）
  - [ ] 自訂 metric：VM provision 成功/失敗 counter、scheduler poll latency histogram
  - [ ] Docker Compose 加入 Prometheus + Grafana 服務

### P7.3 OpenTelemetry Tracing
- **子任務**：
  - [ ] 引入 `opentelemetry-sdk` + `opentelemetry-instrumentation-fastapi`
  - [ ] 配置 Jaeger exporter（開發用 all-in-one Docker）
  - [ ] 關鍵流程加 custom span（provision、migration、AI inference）

---

## Phase 8 — 部署與 Infrastructure 強化 🟢

### P8.1 Worker Durability（Durable Task Queue）
- **現狀**：in-process `background_tasks.py`，重啟遺失任務
- **子任務**：
  - [ ] 評估 ARQ（Redis-backed async）vs Dramatiq
  - [ ] 改寫 `infrastructure/worker/` 使用選定方案
  - [ ] 確保 provision task 重啟後可從 DB 狀態恢復

### P8.2 Database Migration 安全網
- **子任務**：
  - [ ] 補 CI 步驟：每次 PR 自動跑 `alembic upgrade head` 在測試 DB
  - [ ] 補 `alembic downgrade -1` 測試（確認 rollback 不爆）
  - [ ] 建立 migration 命名規範文件

### P8.3 MFA（TOTP）支援
- **子任務**：
  - [ ] 後端：`models/user_mfa.py`（totp_secret、enabled）
  - [ ] `services/user/auth_service.py` 加入 TOTP verify 步驟
  - [ ] 前端：設定頁加入「啟用雙重驗證」流程（QRCode 掃描 + 驗證碼確認）
  - [ ] `pyotp` 套件

### P8.4 Kubernetes / Helm（選做）
- **子任務**：
  - [ ] 建立 `k8s/` 目錄，撰寫 Deployment、Service、ConfigMap manifest
  - [ ] 建立 `helm/SkyLab/` chart
  - [ ] 文件說明從 Docker Compose 遷移到 K8s 的步驟

---

## 執行原則

1. **每個 Phase 開始前補測試守門**，避免重構破壞現有功能
2. **每個 DB model 變更必須配 Alembic migration**
3. **每個新 API endpoint 必須更新 OpenAPI → 重跑 `generate-client.sh`**
4. **不在同一個 PR 同時做功能 + 重構**
5. **相容層退役前先 grep 確認無使用者**

---

## 依賴關係

```
P1（安全）→ 可並行 P2（測試）
P2（測試）→ P6.1（Async，需測試覆蓋才能安全重構）
P3（i18n）→ 可並行執行
P4.1（快照 UI）→ 需要 P2 測試基礎
P4.2（Quota）→ 需要 DB migration（可獨立）
P6.3（Coordinator 拆）→ 需要 P2.1 Scheduler 測試
P7（Observability）→ P6.1 Async 後更有意義
P8.1（Worker Durability）→ P6.1 Async 後進行
```
