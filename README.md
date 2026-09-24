# SkyLab

SkyLab 是一個面向校園資源管理的全端 Proxmox VE（PVE）虛擬化平台，整合了 VM/LXC 生命週期管理、申請審核流程、防火牆與閘道控制、AI 驅動的資源配置建議，以及自架的 vLLM 推論服務。

## 系統組成

| 子系統 | 路徑 | 角色 |
| --- | --- | --- |
| Backend | `backend/` | FastAPI + SQLModel + PostgreSQL，串接 Proxmox API、提供 REST/WebSocket |
| Frontend | `frontend/` | React 19 + Vite + TanStack Router/Query + Tailwind + shadcn/ui |
| AI PVE Placement Advisor | `ai-pve-placement-advisor/` | 規則 + 加權評分的 PVE 放置建議服務（port 8011） |
| AI Template Recommendation | `ai-template-recommendation/` | 基於 vLLM 的模板/規格推薦服務 |
| AI Teacher Judge | `ai-teacher-judge/` | 評分量表（rubric）解析、可偵測度分析與精煉服務 |
| PVE Resource Simulator | `pve_ resource_simulator/` | 離線放置策略模擬器（port 8012） |
| vLLM Service | `vllm-service/` | 單模型主服務 + 多模型 vLLM cluster（由 LiteLLM 對外選路） |
| vLLM Legacy Inference | `vllm-inference/` | 舊單模型 vLLM 部署，遷移參考 |
| vLLM Legacy API Gateway | `vllm-API/` | 舊多模型 vLLM Gateway，遷移參考 |
| Gateway 安裝腳本 | `gateway/` | 校內出口閘道安裝腳本 |
| Docs | `docs/` | 補充文件 |

完整架構/部署指引請見 `docs/development.md`、`docs/deployment.md`。

## 技術棧概覽

- **後端**：FastAPI、SQLModel、Alembic、PostgreSQL、Redis、Proxmoxer、Paramiko、PyJWT、Cryptography、httpx、websockets、Sentry
- **前端**：React 19、TypeScript、Vite 7、TanStack Router/Query/Table、Tailwind v4、Radix UI、Biome、Playwright、react-vnc、xterm.js、@xyflow/react、Recharts、Monaco Editor、i18next（en / zh-TW / ja）
- **基礎設施**：Docker Compose、Traefik、PostgreSQL、Adminer、MailCatcher
- **AI / 推論**：vLLM（OpenAI 相容 API）、自製 PVE Advisor 與 Template Recommendation 服務
- **套件管理**：UV（Python）、Bun（前端）

## 主要功能

- VM / LXC 生命週期管理（建立、查詢、規格調整、快照、刪除）
- 透過 WebSocket 代理 Proxmox VNC 與 LXC Terminal（xterm.js + noVNC）
- VM 申請工作流：學生提交 → 審核 → 租借時段容量評估 → 自動排程供應
- AI 放置建議（PVE Placement Advisor）與模板推薦
- 防火牆拓撲視覺化、NAT 規則、Reverse Proxy 規則管理
- 閘道 VM 管理：HAProxy、Traefik 與 WireGuard 設定
- 多重 Proxmox cluster 連線設定與 HA failover
- 正式班級管理、固定課表、學生名單、多機環境與整班批次建置
- 班級教室監看、教師廣播，以及班級內的 AI 評分檢查
- 管理員專用的獨立 AI PVE 維運助手（不綁定班級）
- AI API 憑證管理 + 申請審核 + Redis sliding-window 流量限制
- OpenAI 相容 AI Proxy：使用者透過 Campus `/api/v1/ai-proxy/{models,chat/completions,completions,responses}` 呼叫；Backend 驗證 `ccai_*` 後轉發至受限 LiteLLM service key。內部 System AI 仍直接走 `{VLLM_BASE_URL}/chat/completions`
- 規格變更申請（vCPU / RAM / Disk）審核流程
- 完整 Audit Log（操作來源、目標 VM、時間）
- 角色權限：admin / instructor / student / superuser
- 三語系 UI（英文、繁中、日文）

## 快速開始（Docker Compose）

複製範例環境變數並啟動整個 stack：

```bash
cp .env.example .env       # 視需要修改 PROXMOX_*、SECRET_KEY、SMTP 等
docker compose watch
```

預設服務位址：

| 服務 | URL |
| --- | --- |
| Frontend | http://localhost:5173 |
| Backend API | http://localhost:8000 |
| Swagger Docs | http://localhost:8000/docs |
| Adminer | http://localhost:8080 |
| MailCatcher（開發用收信匣；正式環境請設 SMTP_HOST） | http://localhost:1080 |

| Traefik Dashboard | http://localhost:8090 |

> vLLM 推論請優先使用 `vllm-service/`。`start_single_model.sh` 啟動單模型主服務；AI API 遷移期以 `start_multi_model_cluster.sh` 啟動多模型 vLLM，並由 LiteLLM routing。舊多模型 Gateway 僅保留為 P5/P6 的回滾入口，以 `python main.py gateway` 啟動（沒有獨立腳本）。


## 本地開發

### 後端

```bash
cd backend
uv sync
source .venv/bin/activate
fastapi dev app/main.py
```

詳見 [`backend/README.md`](backend/README.md)。

### 前端

```bash
cd frontend
bun install
bun run dev
```

詳見 [`frontend/README.md`](frontend/README.md)。

### 重新生成前端 API Client

後端 schema 變更後，於專案根目錄執行：

```bash
bash ./scripts/generate-client.sh
```

會更新 `frontend/src/client/`（不要手動編輯 generated 檔案）。

## 資料庫遷移

使用 Alembic 管理 schema：

```bash
docker compose exec backend bash
alembic revision --autogenerate -m "Description"
alembic upgrade head
```

容器啟動時 `scripts/prestart.sh` 會自動執行 `alembic upgrade head`。

## 測試

- **後端**：`bash ./scripts/test.sh`（pytest，覆蓋率報告於 `backend/htmlcov/`）
- **前端 E2E**：`docker compose up -d --wait backend && cd frontend && bunx playwright test`

## Proxmox 整合設定

於根目錄 `.env` 設定：

```env
PROXMOX_HOST=192.168.x.x
PROXMOX_USER=ccapiuser@pve
PROXMOX_PASSWORD=...
PROXMOX_VERIFY_SSL=false
```

可於後端 `admin/configuration` 頁面動態切換 cluster 連線設定，支援 HA failover（TCP ping 偵測）。

## SSH 目錄查看腳本

專案根目錄提供獨立腳本 `resource_ssh_ls.py`，可透過後端 API 取得指定 VM/LXC 的 SSH 私鑰與 IP，並用 SSH 列出遠端目錄內容。

### 前置條件

- 後端 API 可連線（預設 `http://localhost:8000/api/v1`）
- 目標 VM/LXC 已開機且可透過 IP 連線
- 該 VMID 在 `resources` 已有 `ssh_private_key_encrypted` / `ssh_public_key`
- 本機 Python 環境可使用 `requests` 與 `paramiko`

### 基本用法

```bash
python resource_ssh_ls.py --vmid 101 --ssh-user ubuntu --path /home/ubuntu

# 或執行自訂遠端指令
python resource_ssh_ls.py --vmid 101 --ssh-user ubuntu --command "whoami; hostname; id"
```

### 常用參數

- `--api-base`：API Base URL（預設 `http://localhost:8000/api/v1`）
- `--api-user`：SkyLab 帳號（email）
- `--api-password`：SkyLab 密碼（不填會互動式提示輸入）
- `--vmid`：目標 VM/LXC 的 VMID（必填）
- `--ssh-user`：登入 VM/LXC 的 Linux 帳號（必填）
- `--ssh-port`：SSH port（預設 22）
- `--path`：要列出的遠端目錄（預設 `/`）
- `--command`：自訂遠端指令（提供後會覆蓋 `--path` 的 ls 模式）
- `--timeout`：HTTP/SSH timeout 秒數（預設 15）
- `--insecure-host-key`：改用 AutoAdd host key policy（僅建議內網除錯）

### 環境變數（可選）

- `skylab_API_BASE`
- `skylab_API_USER`
- `skylab_API_PASSWORD`

範例：

```bash
export skylab_API_BASE=http://localhost:8000/api/v1
export skylab_API_USER=admin@example.com
export skylab_API_PASSWORD=your_password
python resource_ssh_ls.py --vmid 101 --ssh-user ubuntu --path /etc
```

> 若看到 `Resource has no ip_address`，通常代表機器尚未開機、未取得 DHCP/靜態 IP，或後端尚未快取到最新 IP。

## 文件索引

- [`docs/multi-machine-environment-sop.md`](docs/multi-machine-environment-sop.md) — 多機教學環境建構、發布與學生使用的正式 SOP
- [`docs/development.md`](docs/development.md) — 完整開發環境設置
- [`docs/deployment.md`](docs/deployment.md) — 生產部署指引
- [`placement.md`](placement.md) — VM placement 演算法說明
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — 貢獻指引
- [`SECURITY.md`](SECURITY.md) — 安全政策
- [`CLAUDE.md`](CLAUDE.md) — Claude Code 工作指引

## 授權

本專案以 MIT License 釋出，詳見 [`LICENSE`](LICENSE)。
