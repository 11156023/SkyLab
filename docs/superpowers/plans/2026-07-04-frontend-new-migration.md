# frontend → frontend_new 全功能遷移與切換計劃（v2，已審查）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將舊 `frontend`（TypeScript + TanStack + shadcn/ui + 生成 client）剩餘功能補齊到 `frontend_new`（JSX + SCSS Modules + react-router-dom + 手寫 services），最後執行目錄互換：`frontend` → `frontend_old`（保留唯讀）、`frontend_new` → `frontend`（成為唯一前端）。

**Architecture:** 缺口集中在六塊：LDAP 登入、資源詳情頁、模板管理、模組 C（監控/治理/LDAP 設定）、模組 D（反挖礦面板）、模組 E（教室/教學面板/配額）。每塊遷移模式一致：**手寫 service（apiGet/apiPost）→ 頁面（Page.jsx + module.scss）→ 註冊 App.jsx 路由與 Sidebar**。切換階段是本計劃風險最高的部分，已逐檔盤點全 repo 引用（見 Phase 8）。

**Tech Stack:** React 19、react-router-dom 7、SCSS Modules、react-vnc、@xterm/xterm、@xyflow/react、sonner、**recharts（本計劃新增）**、Vite 8、Vitest 4。不引入 TypeScript / TanStack / Tailwind / i18next。

## 已確認的使用者決策（2026-07-04）

1. **i18n 不遷移**（frontend_new 維持繁中；Sidebar 語言選單留原樣，不在本計劃範圍）。
2. **監控圖表採用 recharts**（與舊頁等價）。
3. **直接切換**：不移植 Playwright E2E；舊 e2e 隨舊前端一起退役。
4. **目錄互換**：`frontend` → `frontend_old`（保留於 repo 作參考、不再接入 compose/CI）、`frontend_new` → `frontend`。回滾策略＝`git revert` 切換 commit（不維護 frontend_old 的可建置性，避免 workspace/lockfile 糾纏，理由見 Phase 8 風險說明）。

## Global Constraints

- 頁面放 `src/pages/<分類>/<功能>/XxxPage.jsx` + 同名 `.module.scss`；API 呼叫放 `src/services/*.js`，經 `src/services/api.js` 的 `apiGet/apiPost/apiPut/apiPatch/apiDelete`（自動帶 token、401 自動 refresh）。
- 圖示用 `src/components/MIcon.jsx`，toast 用 `src/hooks/useToast.js`（sonner），樣式遵循 `src/assets/styles/_themes.scss` 與 `STYLE_GUIDE.md`。
- **Phase 1–7 期間不修改 `frontend/`**（舊碼唯讀，是欄位/狀態/權限行為的規格來源）；不碰 `frontend/src/client/`。
- 後端合約以 `backend/app/api/routes/*.py` 為準（本計劃已核對各 router 的實際 prefix 與路由，寫在各任務中）。
- 每任務完成必過：`cd frontend_new && bun run build` 與 `bun run test`，再於 `docker compose watch` 環境手動驗證，然後 commit（繁中祈使句，一任務一 commit）。

---

## 現況差距分析（v2 修正版，逐項以 grep 驗證過）

### 已覆蓋、無需遷移（v1 誤判處已修正）

- **登入頁已完整**：`LoginPage.jsx`（556 行）內建 login / register / forgot / reset 四個 view（reset 從 URL `?token=` 進入）、Google 登入（`VITE_GOOGLE_CLIENT_ID`）、`ENABLE_SIGNUP` 開關。~~v1 計劃的「註冊/忘記/重設密碼頁」任務不需要~~。
- 其餘已覆蓋：Dashboard + 快速模板（走 build-time `virtual:templates`）、我的資源（列表 + VNC/Terminal）、我的申請（advise + AvailabilityPanel）、資源管理（含建立）、GPU、申請審核（spec-change/deletion）、批量審核、AI 五頁、群組（詳情/CSV/批量開通/AI 評分/週期排程）、使用者管理、系統設定五 tab（overview/pve/scheduler/nodes/storage）、Migration Jobs、背景任務、部署日誌、Audit Logs、防火牆/網域/閘道/反代/IP、AI 浮動聊天（= 舊 GlobalAiNavigator）。
- 基礎設施現況：nginx 入口（`nginx/default.conf`）的 `location /` **已經指向 `frontend_new:80`**，舊前端只剩 5173 埠直連。切換的實質工作是改名與清理，不是流量搬遷。

### 真實缺口（每項已確認 frontend_new 無對應 service/頁面）

| # | 缺口 | 規格來源（舊碼） | 後端 API（已核對 prefix） |
|---|------|-----------------|--------------------------|
| 1 | LDAP 登入 + 登入方式偵測 | `frontend/src/routes/login.tsx:145-380`（ldapLoginMutation、loginMethods） | `GET /api/v1/login/methods`、`POST /api/v1/login/ldap` |
| 2 | 資源詳情頁（6 tab） | `frontend/src/components/ResourceDetail/*`、`my-resources_.$vmid.tsx`、`resources_.$vmid.tsx` | `resource_details.py`（prefix `/resources`）：`GET {vmid}/current-stats`、`GET {vmid}/stats`、`GET/POST {vmid}/snapshots`、`DELETE {vmid}/snapshots/{snapname}`、`PUT {vmid}/spec/direct`、`POST {vmid}/init-snapshot` |
| 3 | 模板管理頁 | `templates.tsx`（462 行）+ `components/Templates/*`（4 元件） | `templates.py`（prefix `/templates`）：CRUD、`/{id}/clone`、`/{id}/update-cycle/{start,finish,cancel}`、`/tasks`、`/tasks/{task_id}` |
| 4 | 模組 C 監控頁 | `admin.monitoring.tsx`（706 行） | `monitoring.py`（prefix `/monitoring`）：`/overview`、`/nodes/{node}/rrd`、`/vms/{vmid}/rrd`、`/alerts`、`/alerts/{alert_id}/ack` |
| 5 | 系統設定缺 governance / ldap tab + 反挖礦面板 | `admin.configuration.tsx`（7 tab，新版只有 5）、`components/Admin/{GovernanceConfigTab,LdapConfigTab,MiningIncidentsPanel}.tsx` | `governance.py`（`GET/PUT /governance/config`）、`ldap_config.py`（`GET/PUT /admin/ldap-config`、`POST /admin/ldap-config/test`）、`mining_incidents.py`（`GET /mining-incidents`、`POST /{id}/ban`、`POST /{id}/dismiss`、`PUT /exemptions/{vmid}`） |
| 6 | 模組 E 老師教學面板 | `teaching.tsx` + `components/Teaching/*`（Heatmap/ConfigPush/BatchSpec/PairInvite×2/QuotaUsageBar） | `teaching.py`（prefix `/teaching`）：`GET /heatmap`、`POST /batch-spec`、`GET /batch-spec/{task_id}`、`GET /config-push/{task_id}`；`pair_sessions.py`（prefix `/pair-sessions`）：`POST ""`、`GET /mine`、`DELETE /{session_id}`；配額條用 `GET /quotas/my-usage` |
| 7 | 模組 E 虛擬教室 | `classroom.tsx`（248 行）+ `components/Classroom/*`（StudentLayer/WatchDialog/LiveBanner/TakeoverOverlay）；StudentLayer 包在舊 `AppLayout.tsx:42-77` 全域 | `classroom.py`（prefix `/classroom`）：`GET /groups/{group_id}/students`、`POST /sessions`、`DELETE /sessions/{session_id}`、`POST /sessions/{session_id}/control`、`GET /sessions`、`GET /live`；WS：`/ws/classroom`（信令）、`/ws/classroom/{session_id}/watch`（RFB 資料面） |
| 8 | 模組 E 配額管理頁 | `admin.quotas.tsx`（236 行） | `quotas.py`（prefix `/quotas`）：`GET /my-usage`、`GET/POST ""`、`PUT/DELETE /{quota_id}` |

**依賴關係**：教學面板 HeatmapPanel 與資源詳情 MonitoringTab 都吃監控資料 → 監控 service/圖表元件（Phase 2）必須先於資源詳情（Phase 3）與教學面板（Phase 6）。

---

## Phase 1 — LDAP 登入

### Task 1: LoginPage 支援 LDAP

**Files:**
- Modify: `frontend_new/src/services/auth.js`
- Modify: `frontend_new/src/pages/login/LoginPage.jsx`（＋`.module.scss`）
- Test: `frontend_new/src/services/auth.ldap.test.js`

**Interfaces:**
- Produces: `getLoginMethods() → {local, ldap}`（`GET /api/v1/login/methods`，未登入可呼叫）、`loginLdap(username, password) → tokens`（`POST /api/v1/login/ldap`，成功後 `AuthStorage.setTokens`）。

**Steps:**
- [x] 讀 `backend/app/api/routes/login.py` 確認兩條路由的 request/response 欄位。（註：methods 回 `{password, google, ldap}`，非計畫原寫的 `{local, ldap}`，已依後端為準）
- [x] 比照 `api.refresh.test.js` 寫 mock fetch 失敗測試（斷言路徑與 token 儲存）→ `bun run test` 紅。
- [x] 實作 service；LoginPage 掛載時取 methods，`ldap: true` 時登入表單顯示「本地/LDAP」切換（呈現對照舊 `login.tsx` 的雙表單）。（實作註記：LDAP/methods 走純 fetch 不經 `api.js` 的 request()，避免登入失敗 401 誤觸 refresh 重試與 auth:unauthorized 強制登出 toast）
- [x] `bun run test` 綠、`bun run build` 綠；手動以 LDAP 帳號登入。（測試 12 條綠、build 綠；**手動 LDAP 登入待使用者於 docker compose + 實際 LDAP 環境驗證**）
- [x] Commit：`前端遷移: LoginPage 支援 LDAP 登入`

---

## Phase 2 — 監控基礎與監控頁（模組 C）

### Task 2: monitoring service + recharts + RrdChart 共用元件

**Files:**
- Modify: `frontend_new/package.json`（`bun add recharts`）
- Create: `frontend_new/src/services/monitoring.js`
- Create: `frontend_new/src/components/RrdChart/RrdChart.jsx` ＋ `.module.scss`
- Test: `frontend_new/src/services/monitoring.test.js`

**Interfaces:**
- Produces: `MonitoringService.{getOverview, getNodeRrd(node, timeframe), getVmRrd(vmid, timeframe), listAlerts(params), ackAlert(alertId)}`。
- Produces: `<RrdChart data={points} series={[{key,label,color}]} unit="%" />`——recharts LineChart 包裝，顏色取 `_themes.scss` CSS 變數，供監控頁、資源詳情 MonitoringTab、教學熱圖共用。

**Steps:**
- [x] service + 1 條 URL 組裝測試 → 綠。（2 條：getNodeRrd 路徑、listAlerts 參數）
- [x] RrdChart 先以固定資料渲染，確認深淺色主題下可讀。（主題色以 getComputedStyle(document.body) 執行期解析——SVG 屬性不支援 var()；視覺確認待監控頁接上實際資料）
- [x] `bun run build` 綠（確認 recharts 進 bundle 無 ESM 問題）。
- [x] Commit：`前端遷移: monitoring service 與 RrdChart 圖表元件`

### Task 3: 資源監控頁

**Files:**
- Create: `frontend_new/src/pages/system/monitoring/MonitoringPage.jsx` ＋ `.module.scss`（規格來源 `admin.monitoring.tsx`：叢集 overview 卡片、節點/VM RRD、告警列表＋ack）
- Modify: `frontend_new/src/App.jsx`（`/monitoring`）、`frontend_new/src/components/Sidebar/Sidebar.jsx`（「系統管理」加「資源監控」）

**Steps:**
- [ ] 對照舊頁移植三區塊；timeframe 切換、ack 後就地更新。
- [ ] 手動驗證 overview 與 Proxmox 實際數字一致。
- [ ] Commit：`前端遷移: 模組C 資源監控頁`

---

## Phase 3 — 資源詳情頁

### Task 4: resources service 擴充（詳情端點）

**Files:**
- Modify: `frontend_new/src/services/resources.js`（同一 `/resources` prefix，直接擴充既有檔）
- Test: `frontend_new/src/services/resources.detail.test.js`

**Interfaces:**
- Produces: `getCurrentStats(vmid)`、`getStats(vmid, params)`、`listSnapshots(vmid)`、`createSnapshot(vmid, body)`、`deleteSnapshot(vmid, snapname)`、`updateSpecDirect(vmid, body)`、`initSnapshot(vmid)`——一一對應 `resource_details.py`。

**Steps:**
- [ ] 讀 `resource_details.py` 核對參數/回傳 → 實作 + 1 條測試 → 綠。
- [ ] Commit：`前端遷移: resources service 詳情端點`

### Task 5: 資源詳情頁（6 tab）與路由

**Files:**
- Create: `frontend_new/src/pages/personal/resources/detail/ResourceDetailPage.jsx` ＋ `.module.scss`
- Create: 同目錄 `OverviewTab.jsx`、`SpecificationsTab.jsx`、`SnapshotsTab.jsx`、`MonitoringTab.jsx`、`AuditLogsTab.jsx`、`AdvancedSettingsTab.jsx`（規格來源 `frontend/src/components/ResourceDetail/*`）
- Modify: `App.jsx`（`/my-resources/:vmid`、`/resource-mgmt/:vmid`）；`ResourcesPage.jsx`、`ResourceMgmtPage.jsx` 列表列導向詳情。

**Interfaces:**
- Consumes: Task 4 詳情端點；MonitoringTab 用 `RrdChart` + `getStats`（舊 MonitoringTab 用哪條 stats 端點以舊碼為準）；SpecificationsTab 送 `services/specChangeRequests.js`（一般使用者）或 `updateSpecDirect`（管理員，對照舊 tab 的權限分支）；AuditLogsTab 用既有 `services/auditLogs.js` 以 vmid 過濾。

**Steps:**
- [ ] 逐 tab 對照舊元件移植（tab 樣式比照 `SettingsPage.jsx` 的 TABS 寫法）；頂部動作列複用 ResourcesPage 既有開關機/主控台。
- [ ] 手動驗證：六 tab 切換、建/刪快照、規格變更（一般與管理員兩種身份）。
- [ ] Commit：`前端遷移: 資源詳情頁六個分頁`

---

## Phase 4 — 模板管理

### Task 6: templates service + 模板管理頁

**Files:**
- Create: `frontend_new/src/services/templates.js`
- Create: `frontend_new/src/pages/resource/templates/TemplatesPage.jsx` ＋ `.module.scss`，同目錄 `TemplateFormDialog.jsx`、`TemplateCloneDialog.jsx`、`TemplateBadges.jsx`、`TemplateTasksCard.jsx`（規格來源 `templates.tsx` + `components/Templates/*`）
- Modify: `App.jsx`（`/templates`）、`Sidebar.jsx`（「資源」群組加「模板管理」）

**Interfaces:**
- Produces: `TemplatesService.{list, get, create, update, remove, clone, startUpdateCycle, finishUpdateCycle, cancelUpdateCycle, listTasks, getTask}`。

**Steps:**
- [ ] service（1 條測試）→ 頁面 → 路由/側欄 → build 綠。
- [ ] 手動驗證：CRUD、clone、更新週期 start/finish/cancel、任務卡輪詢。
- [ ] Commit：`前端遷移: 模板管理頁`

---

## Phase 5 — 治理 / LDAP 設定 / 反挖礦（模組 C/D）

### Task 7: governance / ldapConfig / miningIncidents services

**Files:**
- Create: `frontend_new/src/services/governance.js`（`getConfig`/`updateConfig`）
- Create: `frontend_new/src/services/ldapConfig.js`（`get`/`update`/`test`）
- Create: `frontend_new/src/services/miningIncidents.js`（`list`/`ban(id)`/`dismiss(id)`/`setExemption(vmid, body)`）
- Test: 各 1 條 mock 測試

**Steps:**
- [ ] 讀三個後端路由檔核對欄位（GovernanceConfig 含 TTL 回收、閒置偵測、告警閾值、快照治理、`provision_max_concurrency`、反挖礦參數）→ 實作 → 測試綠。
- [ ] Commit：`前端遷移: governance/ldap/mining services`

### Task 8: SettingsPage 新增「治理」「LDAP」tab 與反挖礦面板

**Files:**
- Modify: `frontend_new/src/pages/system/settings/SettingsPage.jsx`（TABS 加 `governance`、`ldap`）
- Create: 同目錄 `GovernanceTab.jsx`、`LdapTab.jsx`、`MiningIncidentsPanel.jsx`（規格來源 `components/Admin/*`；MiningIncidentsPanel 內嵌於 GovernanceTab，位置比照舊版）

**Steps:**
- [ ] 對照舊元件逐欄位移植（分節樣式比照 PveTab 的 `title:` 寫法）。
- [ ] 手動驗證：governance 存檔後 GET 回讀一致；LDAP test 按鈕回報結果；mining 事件 ban/dismiss/豁免。
- [ ] Commit：`前端遷移: 系統設定治理/LDAP分頁與反挖礦面板`

---

## Phase 6 — 模組 E 教學體驗

### Task 9: classroom / teaching / pairSessions / quotas services

**Files:**
- Create: `frontend_new/src/services/classroom.js`、`teaching.js`、`pairSessions.js`、`quotas.js`（端點見差距表 #6–#8；各 1 條測試）

**Steps:**
- [ ] 逐檔對照後端路由實作；ConfigPushPanel 的「送出」端點以舊元件實際呼叫為準（可能複用既有 script-deploy service，核對 `components/Teaching/ConfigPushPanel.tsx`）。
- [ ] Commit：`前端遷移: 模組E services（classroom/teaching/pair/quotas）`

### Task 10: 老師教學面板

**Files:**
- Create: `frontend_new/src/pages/teaching/TeachingPage.jsx` ＋ `.module.scss`，同目錄 `HeatmapPanel.jsx`、`ConfigPushPanel.jsx`、`BatchSpecPanel.jsx`、`PairInvitesCard.jsx`、`PairInviteDialog.jsx`、`QuotaUsageBar.jsx`（規格來源 `components/Teaching/*`）
- Modify: `App.jsx`（`/teaching`）、`Sidebar.jsx`（位置比照舊 `AppSidebar.tsx`）

**Steps:**
- [ ] 移植六元件；熱圖消費 `MonitoringService`＋`GroupsService`（舊 HeatmapPanel 即如此）；QuotaUsageBar 用 `/quotas/my-usage`。
- [ ] 手動驗證：熱圖輪詢、批次規格送出後出現在批量審核、邀請發送/撤回。
- [ ] Commit：`前端遷移: 老師教學面板`

### Task 11: 虛擬教室（學生層 + 廣播觀看）

**Files:**
- Create: `frontend_new/src/pages/classroom/ClassroomPage.jsx` ＋ `.module.scss`（老師端 session 管理，規格來源 `classroom.tsx`）
- Create: `frontend_new/src/components/Classroom/ClassroomStudentLayer.jsx`、`LiveBanner.jsx`、`TakeoverOverlay.jsx`、`ClassroomWatchDialog.jsx` ＋ scss（規格來源 `frontend/src/components/Classroom/*`）
- Modify: `frontend_new/src/layout/DashboardLayout.jsx`（以 `<ClassroomStudentLayer>` 包住 Outlet，比照舊 `AppLayout.tsx:42-77`）
- Modify: `App.jsx`（`/classroom`）、`Sidebar.jsx`

**Interfaces:**
- Consumes: `classroom.js`；信令 WS `/ws/classroom`（token 認證與訊息型別逐一對照舊實作）；觀看資料面 `/ws/classroom/{session_id}/watch` 為原生 RFB 流（security=None），`react-vnc` 的 `VncScreen` 直接指向該 URL——接法比照現有 `VncDialog.jsx`。

**Steps:**
- [ ] 先移植 ClassroomWatchDialog（可獨立驗證：開 session 能看到畫面）。
- [ ] 再移植 StudentLayer / LiveBanner / TakeoverOverlay 的信令狀態機（含 WS 斷線重連）。
- [ ] ClassroomPage：建立/結束 session、廣播/接管控制。
- [ ] 雙瀏覽器手動驗證：老師開播 → 學生 LiveBanner → 觀看 → 接管 → 結束。
- [ ] Commit：`前端遷移: 虛擬教室學生層與廣播觀看`

### Task 12: 配額管理頁

**Files:**
- Create: `frontend_new/src/pages/system/quotas/QuotasPage.jsx` ＋ `.module.scss`（規格來源 `admin.quotas.tsx`）
- Modify: `App.jsx`（`/quotas`）、`Sidebar.jsx`（「系統管理」加「配額管理」）

**Steps:**
- [ ] CRUD 表格 + 表單 dialog；驗證改配額後教學面板 QuotaUsageBar 反映。
- [ ] Commit：`前端遷移: 配額管理頁`

---

## Phase 7 — 功能對照總驗收

### Task 13: 以舊側欄為清單逐項驗收

**Steps:**
- [ ] 以 `frontend/src/components/Sidebar/AppSidebar.tsx` 的完整選單為 checklist，兩個身份（一般/管理員）逐項在 frontend_new 操作一遍，缺漏即回補。
- [ ] 核對非頁面資產：ErrorBoundary（舊 `components/ErrorBoundary.tsx`→ 新版若無，補一個包在 DashboardLayout）、Session 到期行為（新版 401-refresh 已覆蓋舊 SessionWarningDialog 的目的，記錄為「以 refresh 取代」即可）、批次操作列（舊 `Resources/BatchActionBar.tsx`，核對新 ResourcesPage 是否已有等價物）。
- [ ] `bun run build`、`bun run test` 全綠。
- [ ] Commit（如有回補）：`前端遷移: 總驗收回補`

---

## Phase 8 — 切換與目錄互換（高風險區，已逐檔盤點）

### 全 repo 引用盤點（2026-07-04 掃描結果）

**引用 `frontend_new` 的檔案（切換時必改）：**

| 檔案 | 內容 | 處置 |
|------|------|------|
| `docker-compose.yml:53,212-226` | `frontend_new` 服務（image `${DOCKER_IMAGE_FRONTEND_NEW:-frontend-new}`、dockerfile `frontend_new/Dockerfile`、args `VITE_GOOGLE_CLIENT_ID`/`ENABLE_SIGNUP`、port `5174:80`）；nginx `depends_on` | 服務改名 `frontend`、image 改 `${DOCKER_IMAGE_FRONTEND}`、dockerfile 改 `frontend/Dockerfile`、port 改 `5173:80` |
| `nginx/default.conf:55-56` | `set $frontend_new_upstream frontend_new:80` | 改 `frontend:80`（變數名一併改） |
| `frontend_new/Dockerfile` | `WORKDIR /app/frontend_new`、`COPY frontend_new/...`、**`COPY ./frontend/nginx.conf`（陷阱：引用舊前端目錄的檔案！）** | 見 Task 14/15 |
| `ai-navigation-demo/README.md` | 純文字描述 | 順手改或忽略（不影響建置） |

**引用 `frontend/`（舊目錄路徑）的檔案（改名後語意改變，必須逐一處置）：**

| 檔案 | 內容 | 處置 |
|------|------|------|
| `docker-compose.yml:196-210` | `frontend_old` 服務（dockerfile `frontend/Dockerfile`、port 5173）| **整段刪除**（回滾靠 git revert） |
| `docker-compose.yml:230-243` | `playwright` 服務（dockerfile `frontend/Dockerfile.playwright`）| **整段刪除**（決策 3：E2E 不移植） |
| 根 `package.json` | `workspaces: ["frontend"]`；scripts `dev/lint/test/generate-client/check:openapi-sync` 均 `--filter frontend` 或指向 frontend 路徑 | workspaces 維持 `["frontend"]`（改名後即指向新前端）；`dev`/`test` 可沿用（新前端有 dev/test script）；**刪除 `lint`、`generate-client`、`check:openapi-sync`、`test:ui`**（新前端無對應 script、不用生成 client） |
| 根 `bun.lock`、`package-lock.json` | 鎖舊 workspace 依賴 | 改名後於根目錄重跑 `bun install` 重生（舊 lock 內容全部失效） |
| `scripts/generate-client.sh`、`generate-client.mjs`、`check-openapi-sync.sh`、`check-openapi-sync.mjs` | 生成/校驗 `frontend/src/client` | **刪除**（新前端不用生成 client；舊碼在 frontend_old 保留歷史即可） |
| `.github/workflows/frontend-tests.yml` | paths `frontend/**`、wd `frontend`、步驟：biome → vitest → `bun run build`（tsc）→ 上傳 dist | 重寫步驟：移除 biome 步（新前端無 biome.json）、`bun install`（新前端自帶 bun.lock，移除刪 lockfile 的 workaround 註解需重新評估——若 Linux runner 裝不起來再套用同 workaround）、vitest、build、artifact path 不變。**保留 job 名 `Frontend Tests`（branch protection 的 required check 名稱）** |
| `.github/workflows/biome-autofix.yml` | 對 `frontend/**` 跑 biome 自動修 | **刪除或停用**（新前端未導入 biome；「為新前端導入 biome」列入切換後待辦，不阻塞） |
| `.env.example:51` / 部署機 `.env` | `DOCKER_IMAGE_FRONTEND=frontend`、（部署機可能有）`DOCKER_IMAGE_FRONTEND_NEW` | `DOCKER_IMAGE_FRONTEND` 沿用；`.env.example` 與部署機 `.env` 移除 `DOCKER_IMAGE_FRONTEND_NEW`；確認 `GOOGLE_CLIENT_ID`、`ENABLE_SIGNUP` 在 `.env.example` 有樣板 |
| `CLAUDE.md` | 前端技術棧、指令（generate-client、biome、playwright）、結構說明 | 依新前端現況重寫前端段落 |
| `backend/app/core/config.py:37` | `FRONTEND_HOST=http://localhost:5173`（信件連結/CORS） | 不改後端；改 `frontend_new/vite.config.js` dev port `5174 → 5173` 對齊 |
| `.github/workflows/deploy-pve-test.yml` | 泛用 `docker compose build && up -d --remove-orphans` | 免改；`--remove-orphans` 會自動清掉被刪服務的舊容器 |
| `README.md`、`docs/*.md` | 文字描述 | 最後 sweep 順手更新 |

**確認過不受影響：** `frontend_new/vite.config.js` 的 `templatesPlugin` 用相對路徑 `../backend/...`（改名後仍成立）；backend 程式不引用前端路徑；CodeQL workflow 不綁 frontend 路徑。

### Task 14: 改名前置（在還叫 frontend_new 時完成，獨立 commit）

**Files:**
- Create: `frontend_new/nginx.conf`（從 `frontend/nginx.conf` 複製一份；如舊檔引用 `nginx-backend-not-found.conf` 相關內容，一併核對是否需要複製）
- Modify: `frontend_new/Dockerfile`（`COPY ./frontend/nginx.conf` → `COPY ./frontend_new/nginx.conf`）
- Modify: `frontend_new/vite.config.js`（dev port `5174` → `5173`，與 `FRONTEND_HOST`、文件慣例對齊）

**Steps:**
- [ ] 複製 nginx.conf、改 Dockerfile COPY 來源。
- [ ] `docker compose build frontend_new && docker compose up -d frontend_new`，開 5174 煙霧測試（此時 compose 埠映射仍 5174）。
- [ ] 確認 `git status` 只有預期兩檔 + 新檔。
- [ ] Commit：`前端切換前置: frontend_new 自帶 nginx.conf 並統一 dev port`

### Task 15: 目錄互換與全引用更新（單一 commit，內容環環相扣不可拆）

**Steps:**
- [ ] **先停止** `docker compose down` 與所有本機 dev server / 編輯器 watcher（Windows 檔案鎖會讓目錄改名失敗）。
- [ ] `git mv frontend frontend_old && git mv frontend_new frontend`（git mv 為 OS 層 rename，untracked 的 node_modules/dist 會一起搬走，屬預期）。
- [ ] 改 `frontend/Dockerfile`（原 frontend_new 的）：`WORKDIR /app/frontend_new` → `/app/frontend`；所有 `COPY frontend_new/...`、`COPY ./frontend_new` → `frontend/...`；`COPY ./frontend_new/nginx.conf` → `./frontend/nginx.conf`；頂部註解同步改。
- [ ] 改 `docker-compose.yml`：刪 `frontend_old`、`playwright` 服務；`frontend_new` 服務改名 `frontend`（image `${DOCKER_IMAGE_FRONTEND}`、dockerfile `frontend/Dockerfile`、port `5173:80`、build args 保留）；nginx `depends_on` 改為 `frontend`。
- [ ] 改 `nginx/default.conf`：upstream `frontend_new:80` → `frontend:80`。
- [ ] 改根 `package.json`：刪 `lint`、`generate-client`、`check:openapi-sync`、`test:ui` scripts；`workspaces` 維持 `["frontend"]`。
- [ ] 刪 `scripts/generate-client.sh`、`scripts/generate-client.mjs`、`scripts/check-openapi-sync.sh`、`scripts/check-openapi-sync.mjs`。
- [ ] 根目錄 `rm bun.lock package-lock.json && bun install` 重生 lock。
- [ ] 重寫 `.github/workflows/frontend-tests.yml` 步驟（見盤點表）；刪 `.github/workflows/biome-autofix.yml`。
- [ ] `.env.example` 移除 `DOCKER_IMAGE_FRONTEND_NEW`、確認 `GOOGLE_CLIENT_ID`/`ENABLE_SIGNUP` 樣板；**提醒使用者同步清部署機 `.env`**。
- [ ] 更新 `CLAUDE.md` 前端段落與開發 URL（5173 為新前端）。
- [ ] 驗證（全部通過才 commit）：
  - `git grep -n "frontend_new"` → 除 `frontend_old/` 內舊碼與 docs 歷史紀錄外應為 0。
  - `cd frontend && bun run test && bun run build` 綠。
  - 根目錄 `bun run dev` 起得來（workspace filter 正常）。
  - `docker compose build && docker compose up -d` → 開 `http://localhost`（nginx 入口）與 `http://localhost:5173` 全站煙霧測試（登入、我的資源、監控頁、教室各點一頁）。
  - `docker compose config` 無 warning（DOCKER_IMAGE_FRONTEND_NEW 未定義殘留等）。
- [ ] Commit：`前端切換: frontend_new 轉正為 frontend，舊前端封存為 frontend_old`
- [ ] Push 後盯 CI：`Frontend Tests` 必須綠（required check）；`deploy-pve-test` 部署後遠端煙霧測試。

### Task 16: 觀察期後清理（需使用者另行確認，不在本計劃自動執行）

**Steps:**
- [ ] 並行觀察一至兩週無回報問題後：刪除 `frontend_old/` 目錄（歷史仍在 git）。
- [ ] 屆時一併評估：為新 frontend 導入 biome + 恢復 autofix workflow、補 Playwright 關鍵路徑、i18n 復活（皆為切換後待辦，非本計劃範圍）。

---

## 風險清單

| 風險 | 緩解 |
|------|------|
| `frontend_new/Dockerfile` 引用舊目錄的 nginx.conf——改名後建置直接爆 | Task 14 前置修復，改名前先驗證 build |
| Windows 檔案鎖導致 `git mv` 失敗或半途 | Task 15 第一步強制停 compose/dev server；失敗時 `git status` 檢查後重來 |
| 根 workspace/lockfile 與新前端糾纏 | 改名後重生 lock + 驗證 `bun run dev`；不維護 frontend_old 可建置性 |
| branch protection required check 名稱改變導致 PR 卡死 | frontend-tests.yml 保留 job 名 `Frontend Tests` |
| 部署機 `.env` 殘留 `DOCKER_IMAGE_FRONTEND_NEW` 或缺 `GOOGLE_CLIENT_ID` | Task 15 明列提醒；`docker compose config` 驗證 |
| 舊頁面隱性功能（權限分支、輪詢間隔、錯誤文案）漏抄 | 每任務以舊碼為規格來源逐欄對照 + Task 13 雙身份總驗收 |
| WS 教室資料面在新 react-vnc 下行為差異 | Task 11 先單獨驗證 WatchDialog 再接狀態機 |

## Self-Review 紀錄（v2）

- v1 錯誤已修正：註冊/忘記/重設密碼頁在 frontend_new 已存在（LoginPage 四 view），刪除該任務；Google 登入亦已存在。
- 所有後端 prefix/路由已用 `backend/app/api/main.py` 與各 route 檔核實（非憑印象）。
- 切換段引用盤點來自全 repo `grep frontend_new` / `grep frontend` 實掃，涵蓋 compose、nginx、Dockerfile、根 package.json、scripts×4、workflows×2、.env.example、CLAUDE.md、backend config。
- 任務順序滿足依賴：Task 2（monitoring/RrdChart）先於 Task 5（詳情 MonitoringTab）與 Task 10（熱圖）；Task 9（quotas.js）先於 Task 10/12。
- 決策 1–4 已全數落入計劃：無 i18n 任務、Task 2 引 recharts、無 E2E 任務、Phase 8 為目錄互換。
