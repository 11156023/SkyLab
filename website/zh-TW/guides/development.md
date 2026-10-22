# 開發指南

本文說明如何在本機跑 SkyLab。正式部署見 [`deployment.zh-TW.md`](/zh-TW/guides/deployment)，後端結構見 [`../backend/README.zh-TW.md`](/zh-TW/guides/backend)。

## 前置需求

- Docker Engine 與 Compose ≥ 2.20（根目錄 compose 檔用到 `include`）
- Python 用 [uv](https://docs.astral.sh/uv/)、前端用 [Bun](https://bun.sh/)
- 後端需要 Python **3.11 以上**（`requires-python = ">=3.11"`；程式用到 3.10 沒有的 `datetime.UTC`）。CI 以 3.11 測試，正式映像跑 3.14；系統內建的版本太舊時 `uv sync` 會自動下載合適的直譯器
- 要實際建機器才需要連得到的 Proxmox VE 節點或叢集；介面、認證、文件與大多數測試不需要

## Docker Compose

```bash
cp -n .env.example .env         # 至少改 SECRET_KEY、FIRST_SUPERUSER*、POSTGRES_PASSWORD
docker compose watch            # build、啟動，並把原始碼變更同步進容器
```

根目錄 `docker-compose.yml` 啟動的服務：

| 服務 | 角色 | 主機 port |
| --- | --- | --- |
| `nginx` | 單一入口：`/api`、`/ws` → backend，`/grafana/` → Grafana，其餘 → frontend | `NGINX_HOST_PORT`（8082） |
| `backend` | FastAPI app | `BACKEND_HOST_PORT`（8000） |
| `worker` | arq 背景 worker（克隆、批次建置、刪除…） | – |
| `prestart` | 等待 DB、跑 Alembic、建立第一位 superuser 後結束 | – |
| `frontend` | Vite dev server（正式映像改為提供建置後的 bundle） | 5173 |
| `db` / `pgbouncer` | PostgreSQL 與應用程式實際連線的 transaction pooling 代理 | `POSTGRES_HOST_PORT`（5433） |
| `redis` | 流量限制、token 撤銷、arq 佇列、快取 | `REDIS_HOST_PORT`（6379） |
| `mailcatcher` | 開發時攔下所有寄出的信 | 1080（網頁）、1025（SMTP） |
| `litellm` | AI API gateway，由 `vllm-service/litellm/docker-compose.yml` include 進來 | 4000（僅 localhost） |
| monitoring profile | Prometheus、Grafana、Loki、Alloy、InfluxDB、exporters、cAdvisor | 見 [`monitoring.zh-TW.md`](/zh-TW/guides/monitoring) |

常用指令：

```bash
docker compose logs -f backend
docker compose exec backend bash
docker compose --profile monitoring up -d       # 加上監控 stack
docker compose down -v --remove-orphans         # 連 volume 一起清掉（含資料庫）
```

第一次啟動會比較久：`prestart` 要等 PostgreSQL 就緒並套用 200 多支 migration。起來後打開 http://localhost:8082。全新資料庫會先進初始化精靈（`/setup`）：建立（或接管）管理員、測試 Proxmox 連線、設定 IP 網段；Gateway 與平台入口兩步在開發環境可以略過。

### MailCatcher

用 Compose 時，除非 `.env` 設了 `SMTP_HOST`，後端會指向 MailCatcher（`SMTP_HOST=mailcatcher`、port 1025）。後端寄出的每封信（重設密碼、審核通知、告警）都會出現在 http://localhost:1080。注意：從正式 `.env` 複製來的 `SMTP_PORT=587`／`SMTP_TLS=True` 會讓 MailCatcher 收不到信。

## 在主機上跑個別服務

每個服務在 Compose 內與主機上用同一個 port，所以可以停掉某個容器、在本機跑那個服務，其餘留在 Docker。

### 後端

```bash
docker compose stop backend worker
cd backend
uv sync
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
fastapi dev app/main.py          # http://localhost:8000，Swagger 在 /docs
```

先啟用虛擬環境。全域安裝的 `fastapi` 雖然起得來，但缺 `paramiko` 等專案依賴，建機器時會在執行期噴「SSH backend is unavailable」。

主機行程讀根目錄 `.env`（`env_file="../.env"`）。其餘服務還在 Docker 時，把 `POSTGRES_SERVER` 指到 `localhost`、`POSTGRES_PORT` 指到 `POSTGRES_HOST_PORT`（5433）、`REDIS_URL` 指到 `redis://localhost:6379/0`、`AI_API_BASE_URL`／`LITELLM_RUNTIME_BASE_URL` 指到 `http://127.0.0.1:4000`。worker 是獨立行程：`uv run arq app.infrastructure.queue.worker.WorkerSettings`，或設 `REDIS_ENABLED=false` 讓背景任務在行程內執行。

### 前端

```bash
docker compose stop frontend
cd frontend
bun install
bun run dev                      # http://localhost:5173
```

Vite dev server 會把 `/api` 與 `/ws` 代理到 `http://localhost:8000`；要指到別的後端時設 `VITE_API_URL`。其他指令：`bun run build`（正式 bundle）、`bun run preview`、`bun run test`（Vitest）。

前端慣例：

- 路由集中在 `src/App.jsx`，側欄項目在 `src/components/Sidebar/Sidebar.jsx`。
- 頁面放在 `src/pages/<分類>/<功能>/XxxPage.jsx`，旁邊配同名 `.module.scss`。
- 沒有自動生成的 API client。每個端點在 `src/services/*.js` 都有手寫函式，建立在 `src/services/api.js` 的 `apiGet/apiPost/...`（帶 token、401 自動 refresh）之上，並附 Vitest mock 測試。頁面不直接呼叫 `fetch`。
- 所有頁面讀伺服器資料都走 `src/queries/*.js` 的 TanStack Query hook（一個領域一個檔，每個 hook 包一個 service 函式，自己管 query key 與輪詢間隔）。全站預設在 `src/queries/queryClient.js`：不重試、5 秒內視為新鮮、分頁隱藏時暫停輪詢。寫入仍直接呼叫 service，完成後用 `use…CacheUpdater` 就地改快取或讓該 key 過期重抓。事件處理裡需要當下的伺服器狀態時，呼叫 `queryClient.fetchQuery({ ...xxxQueryOptions(), staleTime: 0 })`，結果也會寫進共用快取。
- 載入與錯誤畫面一律用 `src/hooks/useQueryLoadState.js` 的 `useQueryView`、`useLoadError`、`useQueryErrorEffect`。不要用 `isPending`、`isError` 或 `data === undefined && isError` 推導載入與錯誤狀態：沒有資料時 TanStack Query v5 每次重抓都會把狀態改回 pending、清掉 error，畫面會在每次背景重試（輪詢、切回視窗）時閃回載入中，依它觸發的 toast 也會每次重跳。
- 失敗提示的 toast 依賴 `useLoadFailureMessage(query, 預設訊息)` 回傳的訊息字串，不要依賴錯誤物件（每次失敗都是新物件）。同一串失敗只提示一次，成功過一次之後再失敗才會再提示。
- 全站預設切回視窗會重抓。原本只在進頁與自己寫入後才讀取的查詢（設定表單、經 SSH 讀的 Gateway 狀態、單台機器的各種清單）要設 `refetchOnWindowFocus: false`。只初始化一次的表單要等進頁那次重抓結束（`isFetchedAfterMount`）才填值，不可以從快取裡的舊值開始。
- 拆成多支查詢的資料不會自己保持一致：一支的輪詢條件取決於另一支的資料時，狀態一變就要重抓被影響的那支。`setQueryData` 一定要在 `await cancelQueries(...)` 之後，否則在途的背景重抓會蓋掉剛寫入的值。以位置指定項目的清單操作（防火牆規則）在清單重抓期間要停用，確認視窗按下確認後也要再核對一次那一項。
- 頁面測試用 `src/queries/testing.jsx` 的 `withQueryClient` 包元件。查詢結果是存取追蹤的，要斷言的欄位必須在 render 期間讀過；TanStack Query 以 macrotask 通知，斷言前要先等一個 timer tick。
- React Compiler 在建置時執行（`vite.config.js` 以 `@rolldown/plugin-babel` 掛 `reactCompilerPreset`），新元件不必再手寫 `useMemo`／`useCallback`。違反 hooks 規則的元件會被整個略過，不會壞掉。在 render 期間讀可變實例的程式（例如 TanStack Virtual）要以 `"use no memo"` 退出。
- 所有 `<table>` 都用 TanStack Table v9，經 `src/components/DataTable`（`DataTable`、`DataTableHead`、`DataTableCells`，以及 `features.js` 的 `baseFeatures`／`sortableFeatures`）建立。欄位定義放在模組層級：v9 的 `FlexRender` 會把任何函式型的 `cell` 當成元件掛載，在 render 裡建立欄位的話，每次 render 每一格都會重新掛載。儲存格是元件，可以直接用 hook；頁面層級的處理函式經 `useTable({ meta })` 傳進去。列本身有互動狀態時保留列元件，由它自己畫 `<tr>`，再用 `<DataTableCells row={row} rowState={…} />` 把狀態交給各欄的儲存格。輸出的 DOM 與手寫表格相同，風格指南的表格 mixin 照常適用。
- 長表格用 `useVirtualRows`（`src/hooks/useVirtualRows.js`，TanStack Virtual、以視窗為捲動容器）。不到 60 列時每一列照常渲染，超過才只在 DOM 裡保留視窗附近的列。
- 樣式遵循 [`frontend-style-guide.zh-TW.md`](/zh-TW/guides/frontend-style-guide)：SCSS Modules、`_variables`／`_mixins` 由 Vite 注入、主題色用 `--color-*` 自訂屬性。
- 文字一律走 react-i18next，key 在 `src/locales/{zh-TW,en,ja}/`，三個語系都要加。

## 環境變數

唯一來源是 repo 根目錄的 `.env`，Compose 與 `backend/app/core/config.py` 都讀它。`.env.example` 分段註解了每個變數；非 local 部署前一定要改的是 `SECRET_KEY`、`FIRST_SUPERUSER_PASSWORD`、`POSTGRES_PASSWORD`（`ENVIRONMENT` 不是 `local` 時留著 `changethis` 後端會拒絕啟動）。

Proxmox 憑證**不是**環境變數：在初始化精靈或「PVE 連線」頁填入，加密存於資料庫。

LiteLLM 有自己的 `vllm-service/litellm/.env`（master key、上游金鑰、它的資料庫 URL）。絕不要把 master key 複製到根目錄 `.env`；後端只拿受限的 service key（`AI_API_API_KEY`）。`bash scripts/prepare-ai-stack.sh --init-env` 會把兩份都填好，見 [AI API 使用手冊](/zh-TW/guides/ai-api-user-manual)。

絕不提交 `.env`。

## 資料庫與遷移

應用程式經 PgBouncer（transaction pooling）連資料庫，對程式碼的影響：

- advisory lock 必須是交易層級（`pg_advisory_xact_lock`），不可用 session 層級。
- 不要用假設跨交易仍是同一條 server 連線的 `SET`、`LISTEN/NOTIFY`、temp table。

Schema 變更：

```bash
docker compose exec backend bash
alembic revision --autogenerate -m "Describe the change"
alembic upgrade head
```

容器啟動時會自動跑 migration（`scripts/prestart.sh`）。revision id 保持在 32 字元內，並檢視自動產生的內容：enum 變更（例如 `AuditAction`）要自己補 `ALTER TYPE … ADD VALUE`。CI 的 `migration-check` workflow 會驗證 model 與 migration 一致。

## 測試與 lint

後端：

```bash
cd backend
bash ./scripts/test.sh                       # pytest + 覆蓋率（htmlcov/）
uv run pytest tests/api/routes/test_login.py  # 單一檔案
uv run ruff check . && uv run ruff format --check .
uv run mypy .
```

需要資料庫的測試用 `db` fixture，它會拒絕名稱不像測試庫的資料庫（`PYTEST_ALLOW_NON_TEST_DB=1` 可覆蓋）。要用拋棄式本機資料庫時，在空閒 port 起 `postgres` 與 `redis` 容器並把 `POSTGRES_*`／`REDIS_URL` 指過去，或直接在 stack 內跑 `docker compose exec backend bash scripts/tests-start.sh -x`。測試一律停用 Sentry。

前端：

```bash
cd frontend
bun run test          # Vitest，services 層
bun run build         # 抓 import 與語法錯誤
```

Pre-commit hooks（ruff、ruff-format、YAML/TOML 檢查、大檔檢查；定義在根目錄的 `.pre-commit-config.yaml`）由 [prek](https://prek.j178.dev/) 管理：

```bash
cd backend
uv run prek install -f        # 一次即可
uv run prek run --all-files   # 手動執行
```

## 本機網址

| URL | 內容 |
| --- | --- |
| http://localhost:8082 | 經 nginx 的 SkyLab（使用者看到的入口） |
| http://localhost:5173 | Vite dev server |
| http://localhost:8000/docs | Swagger UI，ReDoc 在 http://localhost:8000/redoc |
| http://localhost:1080 | MailCatcher |
| http://localhost:8082/grafana/ | Grafana（只在 monitoring profile） |
| http://127.0.0.1:9090 | Prometheus（monitoring profile，只綁本機） |

## 常見坑

- **Port 80／特權 port**：入口預設 8082，因為 rootless Docker 綁不了 1024 以下的 port；Windows 上 port 80 也常被 HTTP.sys 佔住。
- **Docker 網段與 PVE 網路重疊**：預設 `docker0` 的 172.17/16 與 Compose 自動網段可能跟校內 PVE 網段撞到；容器連不到 PVE 主機時，在 `daemon.json` 改 `bip` 與 `default-address-pools`。
- **沒有 Proxmox 時的排程錯誤**：還沒有任何 PVE 連線前，排程任務每輪都會記錯誤，空資料庫上這是預期行為。
- **WebSocket 代理**：VNC／terminal 的兩個 pump 用 `asyncio.Event` 通知斷線；送收前一律先檢查，避免「receive after disconnect」。
- **日誌格式**：vmid 用 `%s` 不要用 `%d`，它傳到 logger 時常常已是字串。
- **禁止 silent fallback**：捕到例外只能 re-raise 或記 ERROR，不可吞掉後用預設值繼續跑（曾因此讓靜態 IP 無聲退回 DHCP）。
