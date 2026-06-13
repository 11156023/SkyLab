# SkyLab — Backend

SkyLab 後端：基於 FastAPI + SQLModel + PostgreSQL 的 Proxmox VE 虛擬化管理 API，提供 VM/LXC 生命週期、申請審核、防火牆/閘道、placement 檢查與 vLLM 代理等能力。

## 技術棧

- **Web 框架**：FastAPI（standard ≥ 0.135）+ Pydantic v2
- **ORM / 遷移**：SQLModel + Alembic
- **資料庫**：PostgreSQL（psycopg）+ Redis（hiredis，速率限制與快取）
- **Proxmox 整合**：proxmoxer（含 HA failover、TCP ping）
- **安全**：PyJWT、pwdlib（Argon2 + Bcrypt）、Cryptography（Fernet 加密）
- **遠端連線**：websockets（VNC 代理）、Paramiko（Gateway SSH / LXC terminal）
- **錯誤追蹤**：sentry-sdk[fastapi]
- **重試 / 工具**：tenacity、httpx、emails、jinja2、pyyaml
- **開發工具**：UV、pytest、ruff、mypy、prek

## 目錄結構

```
backend/
├── app/
│   ├── main.py                # FastAPI 入口、lifespan、middleware、WebSocket
│   ├── api/
│   │   ├── main.py            # 路由聚合
│   │   ├── routes/            # 19 個 REST 路由模組
│   │   ├── websocket/         # VNC / Terminal WebSocket 代理
│   │   └── deps/              # 依賴注入（auth、db、proxmox）
│   ├── core/                  # config、db、security、proxmox client、redis
│   ├── models/                # 21 個 SQLModel 模型
│   ├── schemas/               # 14 個 Pydantic schema 模組
│   ├── services/              # 21 個業務邏輯服務
│   ├── repositories/          # 15 個資料存取層
│   ├── ai/                    # Template Recommendation / PVE Log 等 AI 內嵌邏輯
│   ├── domain/placement/      # VM/LXC placement 規則、容量與 scoring
│   ├── ai_api/                # 外部 AI API 整合設定
│   ├── alembic/versions/      # 22+ 個遷移版本
│   ├── email-templates/       # MJML 來源 + 編譯後 HTML
│   ├── utils/                 # email、token 工具
│   ├── backend_pre_start.py   # 啟動前 DB 連線檢查
│   └── initial_data.py        # 預設超級管理員初始化
├── tests/                     # pytest 測試
├── scripts/                   # prestart / test / lint / format
├── alembic.ini
├── pyproject.toml
└── Dockerfile
```

## API 路由概覽

於 `app/api/main.py` 註冊的主要路由：

| 模組 | 功能 |
| --- | --- |
| `login.py` | 登入 / token 換發 / 刷新 |
| `users.py` | 使用者 CRUD、密碼管理、個人資料 |
| `groups.py` | 群組管理、CSV 匯入成員、寄發初始密碼 |
| `vm.py` | VM 建立、VNC ticket、模板列舉 |
| `lxc.py` | LXC 建立與終端機連線 |
| `vm_requests.py` | VM 申請提交、可用性檢查、審核工作流 |
| `migration_jobs.py` | VM 遷移工作追蹤 |
| `resources.py` | 節點 / VM / LXC 列表、使用者資源 |
| `resource_details.py` | 規格、RRD、快照、直接規格更新 |
| `proxmox_config.py` | Cluster 連線設定、憑證驗證、cluster 統計 |
| `firewall.py` | 防火牆拓撲、規則、NAT、Reverse Proxy |
| `gateway.py` | 閘道 VM SSH 隧道、HAProxy / Traefik / FRP 設定 |
| `ai_api.py` | AI API 憑證、申請審核、流量限制 |
| `ai_proxy.py` | OpenAI 相容 `/chat/completions` 代理至 vLLM |
| `spec_change_requests.py` | VM 規格變更申請與審核 |
| `audit_logs.py` | 操作稽核紀錄查詢 |
| `script_deploy.py` | 從 GitHub 自動化部署服務模板 |
| `ai_template_recommendation` | 內嵌 AI 模板推薦 |

WebSocket 端點（`app/main.py`）：

- `GET /ws/vnc/{vmid}` — 透過 JWT query token 取得 Proxmox VNC 代理
- `GET /ws/terminal/{vmid}` — LXC 終端機（Paramiko + xterm.js）

## 核心模組

`app/core/`：

- `config.py`：Pydantic Settings，從 `.env` 載入 Proxmox / SMTP / CORS / DB / SECRET_KEY 等
- `db.py`：SQLAlchemy engine、連線池、首位 superuser 建立
- `security.py`：密碼雜湊（Argon2 + Bcrypt）、JWT 簽發/驗證、Fernet 加密
- `proxmox.py`：ProxmoxAPI client factory，HA failover（TCP ping）、SSL/CA 處理
- `redis.py`：Redis 連線池初始化與關閉

## 中介層與安全

- **SecurityHeadersMiddleware**：純 ASGI，注入 X-Content-Type-Options、X-Frame-Options、CSP、HSTS
- **CORSMiddleware**：可從環境變數設定來源，預設加入 `FRONTEND_HOST`
- **lifespan**：啟動 Redis、啟動 / 停止 VM 申請排程器
- **Exception handlers**：將 `AppError`/`ProxmoxError`/`ProvisioningError` 等映射到對應 HTTP 狀態

## 環境變數

主要設定（完整見 `app/core/config.py` 與根目錄 `.env.example`）：

```env
# Project
PROJECT_NAME=SkyLab
SECRET_KEY=...
ENVIRONMENT=local            # local | staging | production
BACKEND_CORS_ORIGINS=http://localhost:5173

# PostgreSQL
POSTGRES_SERVER=db
POSTGRES_USER=postgres
POSTGRES_PASSWORD=...
POSTGRES_DB=app

# Initial superuser
FIRST_SUPERUSER=admin@example.com
FIRST_SUPERUSER_PASSWORD=...

# Proxmox
PROXMOX_HOST=192.168.x.x
PROXMOX_USER=ccapiuser@pve
PROXMOX_PASSWORD=...
PROXMOX_VERIFY_SSL=false

# SMTP（可選）
SMTP_HOST=...
SMTP_USER=...
SMTP_PASSWORD=...
EMAILS_FROM_EMAIL=...

# Sentry（可選）
SENTRY_DSN=...
```

## 開發環境

### 使用 UV 本機開發

```bash
cd backend
uv sync
source .venv/bin/activate           # Windows: .venv\Scripts\activate
fastapi dev app/main.py
```

`fastapi dev` 會啟動單一 worker、autoreload 模式。Swagger 位於 http://localhost:8000/docs。

### Docker Compose

```bash
docker compose watch                # 全 stack 熱重載
docker compose exec backend bash    # 進入容器
docker compose logs backend         # 查看日誌
```

容器啟動時 `scripts/prestart.sh` 會：

1. 執行 `app/backend_pre_start.py` 等待 DB 就緒
2. 執行 `alembic upgrade head`
3. 執行 `app/initial_data.py` 建立預設 superuser

## 資料庫遷移

```bash
docker compose exec backend bash
alembic revision --autogenerate -m "Add column foo to bar"
alembic upgrade head
```

> 修改 `app/models/` 下任何 SQLModel 後務必建立遷移檔；`app/alembic/versions/` 內已有 22+ 個歷史版本。

## 測試

```bash
# 完整測試
bash ./scripts/test.sh

# 在執行中的 stack 內跑（支援 pytest 參數）
docker compose exec backend bash scripts/tests-start.sh -x
```

`tests/conftest.py` 的 `db` fixture 具備安全防護：

- 預設拒絕在「非測試型資料庫目標」上執行 DB-backed pytest（避免誤連正式/開發 DB）。
- 若你確定要覆蓋此保護，可顯式設定：`PYTEST_ALLOW_NON_TEST_DB=1`。
- 測試結束後的資料清理預設為關閉；如需啟用再設定：`PYTEST_ENABLE_DB_CLEANUP=1`。

> 建議維持透過 compose 測試流程執行，以確保資料庫環境隔離。

測試報告：`backend/htmlcov/index.html`

主要測試檔（`tests/api/routes/`）：

- `test_login.py` / `test_users.py`：認證與使用者
- `test_ai_api.py`：AI API 工作流
- `test_vm_request_availability.py`：VM 申請可用性

## 程式碼品質

```bash
uv run ruff check .          # Lint
uv run ruff check --fix .    # 自動修復
uv run mypy .                # 型別檢查
uv run prek install -f       # 安裝 pre-commit
uv run prek run --all-files  # 手動執行
```

## 主要特性

- **VM 申請工作流**：可用性檢查 → placement 節點建議 → 審核 → 排程供應 → 自動遷移（背景排程器每 60 秒掃描）
- **HA failover**：cluster 設定支援多個 Proxmox host，TCP ping 偵測接管
- **Gateway 控制**：透過 SSH 直接讀寫 HAProxy / Traefik / FRP 設定並重啟服務
- **腳本部署**：從 community-scripts/ProxmoxVE 拉取腳本並於 PVE 節點背景部署
- **AI 代理**：以 OpenAI Chat Completion 介面連接內部 vLLM，含 Redis sliding-window 流量限制
- **加密憑證儲存**：AI API 憑證以 Fernet 加密落地

## Email 模板

`app/email-templates/` 內含 `src/`（MJML）與 `build/`（HTML）兩個資料夾。建議在 VS Code 安裝 MJML 套件，編輯後 `MJML: Export to HTML` 輸出到 `build/`。

## 參考

- 主專案：[`../README.md`](../README.md)
- 開發指引：[`../development.md`](../development.md)
- 部署指引：[`../deployment.md`](../deployment.md)
- VM 放置邏輯：[`../placement.md`](../placement.md)
