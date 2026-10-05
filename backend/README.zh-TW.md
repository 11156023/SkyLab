# SkyLab — Backend

> [English](./README.md) | **繁體中文**

SkyLab 後端是以 FastAPI + SQLModel + PostgreSQL 打造的 Proxmox VE 管理服務：VM/LXC 生命週期、申請審核流程、班級與批次建置、防火牆與閘道控制、治理、監控，以及 AI API gateway。

## 技術棧

- **Web 框架**：FastAPI（standard ≥ 0.135）+ Pydantic v2
- **ORM / 遷移**：SQLModel + Alembic
- **資料庫**：PostgreSQL 經 PgBouncer（transaction pooling）+ Redis（流量限制、token 撤銷、arq 佇列、快取）
- **背景任務**：arq worker（`worker` 容器）；`REDIS_ENABLED=false` 時退回行程內執行
- **Proxmox**：proxmoxer，per-connection 連線池、HA failover（TCP ping）、CA 處理
- **安全**：PyJWT、pwdlib（Argon2 + Bcrypt）、cryptography（Fernet 加密存放的憑證）、TOTP（標準庫）
- **遠端連線**：websockets（VNC 代理，純 RFB 處理在 `infrastructure/vnc/`）、Paramiko（Gateway SSH、LXC terminal、PVE 節點指令）、ldap3
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
│   │   ├── deps/               # 依賴注入（auth、db、proxmox）
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
│   ├── domain/                 # placement 與排程規則（純函式）
│   ├── repositories/           # 資料存取 helper
│   ├── locales/                # 後端訊息翻譯
│   ├── alembic/versions/       # 200+ 支 migration
│   ├── email-templates/        # MJML 來源與編譯後 HTML
│   ├── utils/                  # email、token、TOTP、機器登入密碼、時間工具
│   ├── exceptions.py           # AppError 與全域 handler
│   ├── backend_pre_start.py    # 等待資料庫就緒
│   ├── initial_data.py         # 建立第一位 superuser
│   └── reset_totp.py           # 管理員遺失 TOTP 裝置時的 CLI 救援
├── tests/                      # pytest（api/routes、services、performance）
├── scripts/                    # prestart、test、migration 檢查、rotate_secret_key
├── alembic.ini
├── pyproject.toml
└── Dockerfile
```

維持這個結構的規則：

- **Routes → Services → Infrastructure**，不可反向依賴。Route 只驗證與委派；業務規則在 service；只有 `infrastructure/` 可以碰 Proxmox、SSH、Redis、LDAP 或 LLM。
- **Models 與 schemas 分離**：資料表在 `models/`，API I/O 在 `schemas/`。
- **錯誤**：`raise AppError(status_code, message)`，由全域 handler 轉成 HTTP 回應。
- **PgBouncer transaction pooling**：不要依賴跨交易的 session 狀態。advisory lock 一律用 `pg_advisory_xact_lock`／`pg_try_advisory_xact_lock`，不要用 `SET`、`LISTEN/NOTIFY`、temp table。
- **Proxmox 設定是逐連線的。** 操作某個節點的程式碼要用 `get_proxmox_settings_for_node(node)` 或 `get_proxmox_settings(connection_id)`；尚無任何連線時無參數版本會直接拋錯。

## API 概覽

所有 REST 路由由 `app/api/main.py` 掛在 `/api/v1` 之下，依領域分組：

| 領域 | 模組 | 說明 |
| --- | --- | --- |
| 認證與使用者 | `login`、`users`、`setup`、`ldap_config`、`private`、`utils` | 密碼／Google／LDAP 登入、TOTP 挑戰、refresh、首次安裝精靈、健康檢查 |
| 資源 | `resources`、`resource_details`、`resource_settings`、`vm`、`lxc`、`templates`、`gpu`、`quotas`、`jobs` | 清單、規格、RRD、快照、備份、建立（回 202，clone 在 worker 執行）、GPU 對應、配額用量、任務紀錄 |
| 申請 | `vm_requests`、`spec_change_requests`、`batch_provision` | 申請流程、可用性與放置建議、規格變更審核、整班建置 |
| 教學 | `teaching_classes`、`classroom`、`courses`、`course_admin`、`course_environments`、`quick_practice`、`rubric`、`teacher_judge_*` | 班級、課表、名單、教室監看與廣播、課程路徑、教學環境、快速練習、AI 評分 |
| 網路 | `firewall`、`gateway`、`reverse_proxy`、`ip_management`、`cloudflare`、`desktop_client` | 防火牆拓撲與規則、閘道（nginx、WireGuard、平台入口、憑證）、網域、網段、Cloudflare DNS、SkyLab Connect 裝置登入 |
| 平台 | `proxmox_config`、`monitoring`、`governance`、`mining_incidents`、`audit_logs`、`push` | PVE 連線（CRUD、測試、同步）、概況與 RRD、系統健康、告警、治理設定、挖礦事件、稽核日誌、Web Push |
| AI | `ai`、`ai_api`、`ai_proxy`、`ai_contextual_help`、`ai_monitoring`、`ai_navigation`、`ai_pve_log`、`ai_template_recommendation` | AI API 憑證與審核、OpenAI 相容代理、各種助手、用量監控 |

WebSocket 端點直接註冊在 `app/main.py` 的 app 上：

| 路徑 | 用途 |
| --- | --- |
| `/ws/vnc/{vmid}` | Proxmox VNC 代理（JWT 放 query） |
| `/ws/terminal/{vmid}` | LXC 終端機（Paramiko + xterm.js） |
| `/ws/jobs` | Jobs 頁的即時任務更新 |
| `/ws/classroom`、`/ws/classroom/{session_id}/watch` | 教室信令與 VNC fan-out |
| `/ws/courses/paths/{path_id}/progress` | 課程進度更新 |

雙向轉發用 `asyncio.Event` 在兩個 pump task 之間通知斷線；送收之前一律先檢查它。

## 執行期

- **Lifespan** 初始化 Redis 並啟動三個背景迴圈：VM 申請排程器、Web Push 推播、WireGuard reconciler。每個迴圈各持一把 PostgreSQL 交易層級的 advisory lock，多副本時同一迴圈只有一個行程在跑。
- **排程任務**（60 秒一輪）包含申請開始／結束、auto-stop、刪除佇列、資源告警、TTL 生命週期、閒置偵測、挖礦偵測與系統健康告警。行為由 `GovernanceConfig` singleton（`GET/PUT /governance/config`）控制。
- **arq worker**：範本轉換與克隆、VM 申請建置、一鍵重置、班級批次建置與資源刪除都以任務紀錄入列。建置以 job id `vm_request:<id>` 去重，並受 `provision_max_concurrency` 限制。
- **健康檢查**：`GET /api/v1/utils/health-check/`（liveness）、`GET /api/v1/utils/health-check/ready`（DB + Redis，失敗回 503）、`GET /api/v1/monitoring/system-health`（管理員）、`GET /metrics`（只在內網）。見 [`../docs/monitoring.zh-TW.md`](../docs/monitoring.zh-TW.md)。
- **Middleware**：安全標頭（CSP、HSTS、X-Frame-Options）、CORS、帶 `X-Request-ID` 的 request context、Prometheus instrumentation。

## 設定

設定由 `app/core/config.py` 從專案根目錄 `.env` 載入（`env_file="../.env"`）。帶註解的範本是 `.env.example`，主要分組：

```env
# 身分與密鑰（正式環境務必修改）
SECRET_KEY=...                  # 簽發 JWT，也衍生加密存放憑證的 Fernet 金鑰
FIRST_SUPERUSER=admin@example.com
FIRST_SUPERUSER_PASSWORD=...
POSTGRES_PASSWORD=...

# 環境
ENVIRONMENT=local               # local | staging | production
ENABLE_SIGNUP=true
FRONTEND_HOST=http://127.0.0.1:5173
NGINX_HOST_PORT=8082
# SKYLAB_TRUSTED_PROXY=...      # 只有用閘道的平台入口時才需要

# 資料庫 / redis
POSTGRES_SERVER=db
POSTGRES_DB=app
REDIS_ENABLED=true
REDIS_URL=redis://redis:6379/0

# 選填：System AI、經 LiteLLM 的 AI API、SMTP、Google 登入、Turnstile、LDAP CA、Sentry、WireGuard、日誌、監控 stack
```

Proxmox 連線**不在** `.env`：在初始化精靈或「PVE 連線」頁填入並加密存放。舊的 `PROXMOX_*` 變數會被忽略。

`SECRET_KEY` 必須是至少 32 字元的固定值，backend 與 worker 共用。更換要用 `python -m scripts.rotate_secret_key --apply`（見 [`../docs/deployment.zh-TW.md`](../docs/deployment.zh-TW.md)）；只改 `.env` 會讓所有已加密的憑證解不開。

## 開發

```bash
cd backend
uv sync
source .venv/bin/activate            # Windows: .venv\Scripts\activate
fastapi dev app/main.py              # http://localhost:8000/docs
```

或在 repo 根目錄用 Docker Compose 起整個 stack：

```bash
docker compose watch
docker compose exec backend bash
docker compose logs backend
```

容器啟動時 `scripts/prestart.sh` 會等待資料庫、執行 `alembic upgrade head`，並建立第一位 superuser。

## 遷移

```bash
docker compose exec backend bash
alembic revision --autogenerate -m "Add column foo to bar"
alembic upgrade head
```

`app/models/` 的任何變更都要建 migration。revision id 上限 32 字元。`audit_logs.action` 是 PostgreSQL enum：新增 `AuditAction` 值要配 `ALTER TYPE … ADD VALUE` migration，刪值會讓稽核清單整批壞掉。

## 測試

```bash
bash ./scripts/test.sh                                   # pytest 含覆蓋率 → htmlcov/
docker compose exec backend bash scripts/tests-start.sh -x   # 在執行中的 stack 內跑
```

`tests/conftest.py` 的 `db` fixture 會拒絕對名稱不像測試庫的資料庫執行；設 `PYTEST_ALLOW_NON_TEST_DB=1` 可覆蓋、`PYTEST_ENABLE_DB_CLEANUP=1` 可啟用清理。測試一律停用 Sentry。純函式層（`domain/`、`services/*/policy.py`、`tests/performance/` 層 2）不需任何外部依賴。

CI 以 Python 3.11 跑測試（`.github/workflows/backend-tests.yml`）；正式映像是 `python:3.14-slim`。

## 程式碼品質

```bash
uv run ruff check .
uv run ruff check --fix .
uv run ruff format .
uv run mypy .
uv run prek install -f           # pre-commit hooks
uv run prek run --all-files
```

## Email 模板

`app/email-templates/src/` 放 MJML 來源，`build/` 放編譯後的 HTML。改 MJML 後用 VS Code 的 MJML 套件匯出（「MJML: Export to HTML」）。

## 參考

- 專案總覽：[`../README.zh-TW.md`](../README.zh-TW.md)
- 開發指引：[`../docs/development.zh-TW.md`](../docs/development.zh-TW.md)
- 部署指引：[`../docs/deployment.zh-TW.md`](../docs/deployment.zh-TW.md)
- 監控：[`../docs/monitoring.zh-TW.md`](../docs/monitoring.zh-TW.md)
