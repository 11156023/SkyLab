# SkyLab

> [English](./README.md) | **繁體中文**

SkyLab 是一個面向校園的 Proxmox VE（PVE）全端管理平台，整合 VM/LXC 生命週期管理、申請審核流程、班級批次建置、防火牆與閘道控制、AI 輔助維運，以及以自架 vLLM 模型為後盾的 OpenAI 相容 AI API。

## 系統組成

| 子系統 | 路徑 | 角色 |
| --- | --- | --- |
| Backend | `backend/` | FastAPI + SQLModel + PostgreSQL + Redis，串接 Proxmox API，提供 REST 與 WebSocket |
| Frontend | `frontend/` | React 19（JSX）+ Vite + react-router-dom 7 + SCSS Modules，手寫 API services |
| nginx | `nginx/` | 單一入口（預設 `:8082`）：`/api`、`/ws` → backend，其餘 → frontend |
| Gateway 安裝腳本 | `gateway/` | 校園出口閘道 VM 的 `install.sh`：nginx（Port 轉發、網域反向代理）、WireGuard、nftables ACL |
| 桌面端 | `desktop-client/` | SkyLab Connect（Electron）：裝置登入與到閘道的 WireGuard 隧道 |
| vLLM Service | `vllm-service/` | 單模型主服務、多模型 vLLM cluster，以及對外的 LiteLLM gateway |
| AI PVE Placement Advisor | `ai-pve-placement-advisor/` | 規則 + 加權評分的 PVE 放置建議服務（port 8011） |
| PVE Resource Simulator | `pve_ resource_simulator/` | 離線放置策略模擬器（port 8012） |
| 監控 stack | `monitoring/` | 選用的 Prometheus / Grafana / Loki / InfluxDB profile |
| 舊版 vLLM | `vllm-inference/`、`vllm-API/` | 早期單模型部署與多模型 Gateway，只供遷移參考 |
| Docs | `docs/` | 指南與設計文件，索引見 [`docs/README.zh-TW.md`](docs/README.zh-TW.md) |

## 技術棧

- **後端**：FastAPI、SQLModel、Alembic、PostgreSQL（經 PgBouncer）、Redis、arq worker、proxmoxer、Paramiko、PyJWT、cryptography、httpx、websockets、Sentry
- **前端**：React 19、Vite、react-router-dom 7、SCSS Modules、react-i18next（en / zh-TW / ja）、react-vnc、xterm.js、@xyflow/react、Recharts、Monaco Editor、sonner、Vitest
- **基礎設施**：Docker Compose、nginx、PgBouncer、PostgreSQL、Redis、MailCatcher（開發用）、選用的 Prometheus / Grafana / Loki / Alloy / InfluxDB
- **AI**：vLLM（OpenAI 相容 API）經 LiteLLM 對外，另有自製的 PVE Advisor 與範本推薦服務
- **套件管理**：uv（Python）、Bun（前端）

## 主要功能

- VM / LXC 生命週期：建立、查詢、規格調整、快照、備份／還原、刪除
- 瀏覽器內的 VNC 與 LXC 終端機（WebSocket 代理，noVNC + xterm.js）
- VM 申請工作流：學生提交 → 審核 → 依申請時段做容量檢查 → 排程自動供應
- 範本、正式班級、課表、學生名單、多機教學環境與整班批次建置
- 班級教室監看、教師廣播，以及班級內的 AI導師檢查
- 防火牆拓撲、NAT、反向代理（網域）規則、IP 管理，以及經 SSH 管理的閘道 VM（nginx Port 轉發／網域反向代理／管理員自備的 HTTPS 憑證／WireGuard）
- 多個 Proxmox 連線（單台或叢集），叢集內主機自動 HA failover
- 治理：TTL 漸進回收、閒置偵測、資源告警、反挖礦偵測、配額
- AI API：`ccai_*` 憑證、申請審核、Redis sliding-window 流量限制，以及 `/api/v1/ai-proxy/{models,chat/completions,completions,responses}` 的 OpenAI 相容代理（轉發到受限的 LiteLLM service key）
- 管理員專用的 AI PVE 維運助手，以及教學流程的 AI 導覽、情境說明與範本推薦
- 認證：密碼、Google、LDAP/AD、逐帳號的 TOTP 兩步驟驗證、Cloudflare Turnstile、登入前服務檢查
- 平台健康監控、系統告警、Web Push 推播、Prometheus `/metrics`
- 完整稽核日誌；角色：superuser / admin / instructor / student；三語系介面（en / zh-TW / ja）

## 快速開始（Docker Compose）

根目錄 `docker-compose.yml` 已整合 LiteLLM。先填好兩份 `.env`（模型路由與金鑰見 [AI API 使用手冊](docs/ai-api-user-manual.zh-TW.md)），再啟動整個 stack：

```bash
cp -n .env.example .env
cp -n vllm-service/litellm/.env.example vllm-service/litellm/.env
# 填好兩份 .env；本機模型需先用 vllm-service/start_multi_model_cluster.sh 啟動
bash scripts/prepare-ai-stack.sh --start
```

還不需要 AI 功能時，`docker compose up -d --build` 即可。

預設服務位址：

| 服務 | URL |
| --- | --- |
| SkyLab 入口（nginx） | http://localhost:8082 |
| Frontend dev server | http://localhost:5173 |
| Backend API | http://localhost:8000 |
| Swagger UI | http://localhost:8000/docs |
| MailCatcher（開發用收信匣；正式環境請設 `SMTP_HOST`） | http://localhost:1080 |
| Grafana（需 `--profile monitoring`） | http://localhost:8082/grafana/ |

第一次啟動後打開入口：初始化精靈（`/setup`）會建立管理員、測試 Proxmox 連線，並設定 IP 網段、Gateway 與平台入口。

## 本地開發

後端：

```bash
cd backend
uv sync
source .venv/bin/activate          # Windows: .venv\Scripts\activate
fastapi dev app/main.py
```

前端：

```bash
cd frontend
bun install
bun run dev                        # http://localhost:5173，/api 由 vite proxy 轉發到 :8000
```

後端沒有自動生成的 client。所有端點都在 `frontend/src/services/*.js` 以手寫方式包裝並附 Vitest mock 測試；路由變更時要同步更新對應的 service 檔。

完整說明見 [`docs/development.zh-TW.md`](docs/development.zh-TW.md)，後端結構見 [`backend/README.zh-TW.md`](backend/README.zh-TW.md)。

## 資料庫遷移

Schema 以 Alembic 管理。`backend/app/models/` 的任何變更都要建立遷移：

```bash
docker compose exec backend bash
alembic revision --autogenerate -m "Describe the change"
alembic upgrade head
```

容器啟動時 `scripts/prestart.sh` 會自動執行 `alembic upgrade head`。

## 測試

- **後端**：在 `backend/` 執行 `bash ./scripts/test.sh`（pytest，覆蓋率報告於 `backend/htmlcov/`），或在執行中的 stack 內 `docker compose exec backend bash scripts/tests-start.sh -x`
- **前端**：在 `frontend/` 執行 `bun run test`（Vitest，services 層）
- **CI**：GitHub Actions 跑後端測試、前端測試、桌面端測試、AI service 測試、migration 檢查與 CodeQL

## Proxmox 設定

PVE 連線**不在** `.env` 設定。首次安裝時在初始化精靈填入，之後由管理員在「PVE 連線」頁新增、編輯或同步；連線資訊加密存進資料庫。可同時接多個 PVE 入口（單台或叢集），叢集內多台主機會自動 failover（TCP ping 偵測）。

舊版的 `PROXMOX_HOST`、`PROXMOX_USER`、`PROXMOX_PASSWORD`、`PROXMOX_VERIFY_SSL` 後端已不再讀取。

## 文件

索引在 [`docs/README.zh-TW.md`](docs/README.zh-TW.md)。常用項目：

- [`docs/development.zh-TW.md`](docs/development.zh-TW.md) — 開發環境
- [`docs/deployment.zh-TW.md`](docs/deployment.zh-TW.md) — 正式部署、平台入口、HTTPS 憑證、LDAP
- [`docs/monitoring.zh-TW.md`](docs/monitoring.zh-TW.md) — 內建健康監控與選用的監控 stack
- [`docs/ai-api-user-manual.zh-TW.md`](docs/ai-api-user-manual.zh-TW.md) — LiteLLM 部署與對外 AI API
- [`docs/multi-machine-environment-sop.zh-TW.md`](docs/multi-machine-environment-sop.zh-TW.md) — 多機教學環境 SOP
- [`CONTRIBUTING.zh-TW.md`](CONTRIBUTING.zh-TW.md) — 貢獻指引
- [`SECURITY.zh-TW.md`](SECURITY.zh-TW.md) — 安全政策

## 授權

SkyLab 以 **GNU Affero General Public License v3.0** 釋出（見 [`LICENSE`](LICENSE)）。

- 在校內或組織內自行部署使用，不論有沒有改程式，除授權本身的條款外沒有其他義務。
- 若你修改了 SkyLab 並讓第三方透過網路使用（例如代管或託管服務），AGPL 要求你向那些使用者提供修改後的原始碼。
- 想把 SkyLab 當作服務提供、但不希望受 AGPL 原始碼公開條款約束，或需要其他條件的組織，可洽談**商業授權**。請開 issue 或聯絡維護者。

本專案源自 MIT 授權的 Full Stack FastAPI Template，其聲明保留在 [`NOTICE`](NOTICE)；SkyLab 所用的開源元件列在 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)，應用程式內的「帳號設定 → 關於」也會顯示同樣的資訊。「SkyLab」名稱與標誌屬於本專案；授權涵蓋的是程式碼，不是名稱。
