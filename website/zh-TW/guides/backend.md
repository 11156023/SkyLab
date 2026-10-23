# SkyLab — Backend

SkyLab 後端是以 FastAPI + SQLModel + PostgreSQL 打造的 Proxmox VE 管理服務：VM/LXC 生命週期、申請審核流程、班級與批次建置、防火牆與閘道控制、治理、監控，以及 AI API gateway。

## 技術棧

- **Web 框架**：FastAPI（`fastapi[standard]`）+ Pydantic v2
- **ORM / 遷移**：SQLModel + Alembic
- **資料庫**：PostgreSQL 經 PgBouncer（transaction pooling）+ Redis（流量限制、token 撤銷、arq 佇列、快取）
- **背景任務**：arq worker（`worker` 容器）；`REDIS_ENABLED=false` 時退回行程內執行
- **Proxmox**：proxmoxer，per-connection 連線池、HA failover（TCP ping）、CA 處理
- **安全**：PyJWT、pwdlib（Argon2 + Bcrypt）、cryptography（Fernet 加密存放的憑證）、TOTP（標準庫）
- **遠端連線**：websockets（VNC 與 LXC 終端機代理，連到 PVE 的 websocket 端點；純 RFB 處理在 `infrastructure/vnc/`）、Paramiko（Gateway SSH、`pct` 等 PVE 節點指令）、ldap3
- **可觀測性**：JSON 結構化日誌、Prometheus 指標、Sentry
- **工具**：uv、pytest、ruff、mypy、prek

## 目錄結構

```
backend/
├── app/
│   ├── main.py                 # FastAPI app、lifespan、middleware、WebSocket 端點
│   ├── api/
│   │   ├── main.py             # 路由聚合
│   │   ├── routes/             # REST 端點（薄控制器，約 50 個模組）
│   │   ├── websocket/          # VNC、terminal、jobs、classroom、course progress
│   │   ├── deps/               # 依賴注入（auth、db、proxmox、流量限制、Turnstile、AI API 金鑰）
│   │   └── prometheus_sd.py    # 監控 stack 用的 http_sd targets
│   ├── schemas/                # Pydantic 請求／回應 schema
│   ├── models/                 # 只放 SQLModel 資料表與 enum
│   ├── services/               # 業務邏輯，依領域分組
│   │   ├── classroom/          #   VNC fan-out session、信令 hub
│   │   ├── course/, course_environment/, teaching/, template/
│   │   ├── governance/         #   TTL 回收、閒置偵測
│   │   ├── jobs/               #   Jobs 頁顯示的任務紀錄
│   │   ├── llm_gateway/        #   AI API gateway／proxy（上游一律是 LiteLLM）
│   │   ├── monitoring/         #   健康判定、心跳、登入前檢查、告警
│   │   ├── network/            #   防火牆、NAT、閘道、反向代理、IP 管理、WireGuard、平台入口、憑證
│   │   ├── notification/       #   Web Push
│   │   ├── proxmox/            #   VM/LXC 建置、連線同步
│   │   ├── resource/           #   資源 CRUD、備份、快照
│   │   ├── scheduling/         #   VM 申請排程器（coordinator、policy、provision pool、leader 鎖）
│   │   ├── security/           #   反挖礦偵測
│   │   ├── system/             #   初始化精靈
│   │   ├── user/               #   認證、使用者、TOTP、LDAP、密碼規則、稽核日誌
│   │   └── vm/                 #   批次建置、placement、規格變更、VM 申請
│   ├── infrastructure/         # 只放外部系統整合
│   │   ├── proxmox/            #   API client（per-connection 連線池）、operations、routing、TLS
│   │   ├── ssh/, ldap/, google/, cloudflare/, redis/, vnc/, ai/
│   │   ├── queue/              #   arq 任務模組
│   │   └── worker/             #   行程內背景執行器
│   ├── core/                   # config、db、security、permissions、i18n、logging、metrics、sentry、request context
│   ├── ai/                     # 內建 AI 助手（PVE log、導覽、情境說明、範本推薦、teacher judge）
│   ├── features/ai/            # AI API／LiteLLM 設定（AI_API_*、LITELLM_RUNTIME_*）
│   ├── domain/                 # placement、排程與登入帳號命名規則（純函式）
│   ├── repositories/           # 資料存取 helper
│   ├── locales/                # 後端訊息翻譯
│   ├── alembic/versions/       # 200+ 支 migration
│   ├── email-templates/        # MJML 來源與編譯後 HTML
│   ├── utils/                  # email、token、TOTP、機器登入密碼、時間工具
│   ├── exceptions.py           # AppError 與全域 handler
│   ├── backend_pre_start.py    # 等待資料庫就緒
│   ├── initial_data.py         # 建立第一位 superuser
│   └── reset_totp.py           # 管理員遺失 TOTP 裝置時的 CLI 救援
├── config/                     # system-ai.json（AI 助手）、placement.json、llm-model-profiles.yaml、username_policy.yaml
├── tests/                      # pytest（api/routes、services、performance）
├── scripts/                    # prestart、test、migration 檢查、rotate_secret_key、export_openapi
├── alembic.ini
├── pyproject.toml
└── Dockerfile
```

維持這個結構的規則：

- **Routes → Services → Infrastructure**，不可反向依賴。Route 只驗證與委派；業務規則在 service；只有 `infrastructure/` 可以碰 Proxmox、SSH、Redis、LDAP 或 LLM。
- **Models 與 schemas 分離**：資料表在 `models/`，API I/O 在 `schemas/`。
- **錯誤**：`raise AppError(status_code, message)`，由全域 handler 轉成 HTTP 回應。
- **PgBouncer transaction pooling**：不要依賴跨交易的 session 狀態。advisory lock 一律用 `pg_advisory_xact_lock`／`pg_try_advisory_xact_lock`，不要用 `SET`、`LISTEN/NOTIFY`、temp table。
- **慢速 I/O 期間不要抱著資料庫交易。** `get_db` 的 session 第一次查詢就開交易，連線一直佔到回應結束，而且 `get_db` 不會 commit。await Proxmox、SSH 或 LLM 之前，先用 `end_read_transaction(session)`（`app/core/db.py`）把連線還回去。它會送出 COMMIT，所以 async 程式要經 threadpool 呼叫；session 裡還有未送出的寫入時它什麼都不做。async 程式裡的同步 DB 操作經 `run_db_in_threadpool`（同一個檔案）執行，並使用自己的短 `Session(engine)`。持有 `pg_advisory_xact_lock` 的交易裡不要呼叫 `end_read_transaction`：commit 會連鎖一起放掉鎖。
- **Proxmox 設定是逐連線的。** 操作某個節點的程式碼要用 `get_proxmox_settings_for_node(node)` 或 `get_proxmox_settings(connection_id)`；尚無任何連線時無參數版本會直接拋錯。

## API 概覽

所有 REST 路由由 `app/api/main.py` 掛在 `/api/v1` 之下，依領域分組：

| 領域 | 模組 | 說明 |
| --- | --- | --- |
| 認證與使用者 | `login`、`users`、`setup`、`ldap_config`、`private`、`utils` | 密碼／Google／LDAP 登入、TOTP 挑戰、refresh、首次安裝精靈、健康檢查 |
| 資源 | `resources`、`resource_details`、`resource_settings`、`vm`、`lxc`、`templates`、`gpu`、`quotas`、`jobs`、`policies` | 清單、規格、RRD、快照、備份、建立（回 202，clone 在 worker 執行）、GPU 對應、配額用量、任務紀錄、登入帳號命名政策 |
| 申請 | `vm_requests`、`spec_change_requests`、`batch_provision` | 申請流程、可用性與放置建議、規格變更審核、整班建置 |
| 教學 | `teaching_classes`、`classroom`、`courses`、`course_admin`、`course_environments`、`quick_practice`、`rubric`、`teacher_judge_*` | 班級、課表、名單、教室監看與廣播、課程路徑、教學環境、快速練習、AI導師檢查 |
| 網路 | `firewall`、`gateway`、`reverse_proxy`、`ip_management`、`cloudflare`、`desktop_client` | 防火牆拓撲與規則、閘道（nginx、WireGuard、平台入口、憑證）、網域、網段、Cloudflare DNS、SkyLab Connect 裝置登入 |
| 平台 | `proxmox_config`、`monitoring`、`governance`、`mining_incidents`、`audit_logs`、`push` | PVE 連線（CRUD、測試、同步）、概況與 RRD、系統健康、告警、治理設定、挖礦事件、稽核日誌、Web Push |
| AI | `ai`、`ai_api`、`ai_proxy`、`ai_contextual_help`、`ai_monitoring`、`ai_navigation`、`ai_pve_log`、`ai_template_recommendation` | AI API 憑證與審核、OpenAI 相容代理、各種助手、用量監控 |

WebSocket 端點（VNC、LXC 終端機、jobs、classroom、課程進度）直接註冊在 `app/main.py` 的 app 上，不經 API router；處理程式在 `api/websocket/`，路徑列在 [WebSocket 端點](/zh-TW/reference/websocket)。雙向轉發用 `asyncio.Event` 在兩個 pump task 之間通知斷線；送收之前一律先檢查它，否則代理會出現「receive after disconnect」錯誤。

## 執行期

- **Lifespan** 初始化 Redis 並啟動三個背景迴圈：VM 申請排程器、Web Push 推播、WireGuard reconciler。每個迴圈各持一把 PostgreSQL 交易層級的 advisory lock，多副本時同一迴圈只有一個行程在跑。
- **排程任務**（60 秒一輪）包含申請開始／結束、auto-stop、刪除佇列、資源告警、TTL 生命週期、閒置偵測、挖礦偵測與系統健康告警。行為由 `GovernanceConfig` singleton（`GET/PUT /governance/config`）控制。
- **arq worker**：範本轉換與克隆、VM 申請建置、一鍵重置、班級批次建置、備份與還原、資源刪除都以任務紀錄入列。建置以 job id `vm_request:<id>` 去重，並受 `provision_max_concurrency` 限制。
- **健康檢查**：`GET /api/v1/utils/health-check/`（liveness）、`GET /api/v1/utils/health-check/ready`（DB + Redis，失敗回 503）、`GET /api/v1/monitoring/system-health`（管理員）、`GET /metrics`（只在內網）。見[監控](/zh-TW/guides/monitoring)。
- **Middleware**：安全標頭（CSP、HSTS、X-Frame-Options）、CORS、帶 `X-Request-ID` 的 request context、Prometheus instrumentation。

## 設定

設定由 `app/core/config.py` 從專案根目錄 `.env` 載入（`env_file="../.env"`；不認得的變數會被忽略）。帶註解的範本是 `.env.example`，每個變數都列在[環境變數參考](/zh-TW/operations/environment)。

Proxmox 連線**不在** `.env`：在初始化精靈或「PVE 管理」頁填入並加密存放。舊的 `PROXMOX_*` 變數會被忽略。

`SECRET_KEY` 必須是至少 32 字元的固定值，backend 與 worker 共用。更換要用 `python -m scripts.rotate_secret_key --apply`（見[部署指南](/zh-TW/guides/deployment)）；只改 `.env` 會讓所有已加密的憑證解不開。

## 開發

在主機上或用 Docker Compose 跑後端的方式見[開發指南](/zh-TW/guides/development#後端)。映像執行 `fastapi run --workers 1`：教室 VNC session、presence 與 VNC ticket cookie 都是行程內狀態，要先把這些狀態移出行程，才能用多個容器橫向擴充。容器啟動時 `scripts/prestart.sh` 會等待資料庫、執行 `alembic upgrade head`，並建立第一位 superuser。

## 遷移

`app/models/` 的任何變更都要建 migration，照[開發指南](/zh-TW/guides/development#資料庫與遷移)在主機上產生；schema 變更的正規化規則見[資料庫設計](/zh-TW/guides/database-design)。revision id 上限 32 字元。PostgreSQL enum 欄位（`models/` 裡用 `sa.Enum(...)` 的欄位）新增值時要配 `ALTER TYPE … ADD VALUE` migration。`audit_logs.action` 是一般字串欄位，寫入時以 `AuditAction` 驗證，所以新增稽核動作不必 migration，舊值也照樣讀得到。

## 測試

```bash
uv run bash ./scripts/test.sh        # pytest 含覆蓋率 → htmlcov/
```

測試在主機上跑（映像裡沒有 `tests/`）。`tests/conftest.py` 的 `db` fixture 會拒絕對名稱不像測試庫的資料庫執行；設 `PYTEST_ALLOW_NON_TEST_DB=1` 可覆蓋、`PYTEST_ENABLE_DB_CLEANUP=1` 可啟用清理。測試一律停用 Sentry。純函式層（`domain/`、`services/*/*policy.py` 這些模組、`tests/performance/` 層 2）不需任何外部依賴。

CI 以 Python 3.11 跑測試（`.github/workflows/backend-tests.yml`）；正式映像是 `python:3.15-slim`。

## 程式碼品質

ruff、mypy 與 prek pre-commit hooks 的跑法見[開發指南](/zh-TW/guides/development#測試與-lint)。CI 以 `ruff check` 把關，mypy 只回報不擋。

## Email 模板

`app/email-templates/src/` 放 MJML 來源，`build/` 放編譯後的 HTML。改 MJML 後用 VS Code 的 MJML 套件匯出（「MJML: Export to HTML」）。

## 參考

- [專案總覽](/zh-TW/overview)
- [開發指南](/zh-TW/guides/development)
- [部署指南](/zh-TW/guides/deployment)
- [監控](/zh-TW/guides/monitoring)
- [WebSocket 端點](/zh-TW/reference/websocket)
