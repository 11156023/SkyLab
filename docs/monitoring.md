# SkyLab 系統監控

SkyLab 的監控分成兩層：

| 層 | 看什麼 | 在哪裡 | 需要額外容器？ |
|---|---|---|---|
| **內建** | 平台健康（DB、Redis、worker、PVE API 連線、排程任務心跳）、系統告警＋Email | 管理員「資源監控」頁的「系統健康」卡、活動警告 | 否 |
| **監控 stack**（選用） | API 流量／延遲／錯誤率、排程與佇列指標、容器與主機資源、PostgreSQL／Redis、集中日誌、Proxmox 節點／VM 用量、外部探測與推播 | Grafana、Prometheus、Uptime Kuma | `docker compose --profile monitoring` |

Proxmox 節點／VM 的資源用量**不經過 SkyLab 後端**：由 PVE 內建的 Metric Server 直接推到監控 stack 的 InfluxDB。後端只檢查「自己連不連得到 PVE API」。

---

## 1. 內建（不必另外裝東西）

### 健康檢查端點

| 端點 | 用途 | 權限 |
|---|---|---|
| `GET /api/v1/utils/health-check/` | Liveness：程序活著就回 `true`（compose healthcheck 用） | 免登入 |
| `GET /api/v1/utils/health-check/ready` | Readiness：DB 與 Redis 都通才 200，否則 **503**。回傳 `{"status":"ok","checks":{"database":true,"redis":true}}`，不帶錯誤細節 | 免登入 |
| `GET /api/v1/monitoring/system-health` | 完整報告：各元件狀態與延遲、背景迴圈與每個排程任務的心跳 | 管理員 |
| `GET /metrics` | Prometheus 格式指標（只在內網 `backend:8000`，nginx 不轉發） | 內網；可設 `METRICS_TOKEN` |

### 排程任務心跳

主排程（`scheduler`，60 秒一輪、17 個任務）、Web Push 推播（`web_push`）、WireGuard 同步（`wireguard`）每次執行都會記錄：上次執行、上次成功、耗時、連續失敗次數、最近一次錯誤。資料寫在 Redis（`skylab:hb:*`，7 天過期），Redis 不可用時退回行程記憶體。

狀態判定（`services/monitoring/health_policy.py`）：

- **連續失敗**：連續失敗 ≥ 3 次
- **偶發失敗**：失敗 1–2 次（卡片標紅，但不拉低整體狀態）
- **停擺**：超過 `max(5 × 間隔, 10 分鐘)` 沒有執行
- **尚未執行**：這次啟動後還沒輪到

注意：部分任務在自己內部就把例外吞掉並回傳 0（例如 `process_pending_deletions`），這類任務失敗時心跳仍會顯示成功，要看後端日誌。

### 系統告警

排程任務 `process_system_health_alerts` 每分鐘（依「治理設定 → 告警檢查間隔」，最少 60 秒）評估一次，發現下列問題時寫入 `scope=system` 的告警，出現在「資源監控 → 活動警告」，並依「告警 Email」開關寄信給所有管理員：

- 排程任務連續失敗 ≥ 3 次，或停擺
- 背景迴圈停擺（沒有任何行程拿到 leader）
- worker 沒有心跳、Redis 連不上、某個 PVE 連線連不上

同一個問題要**連續兩輪**都出現才開告警（吸收部署時 worker 晚起、PVE 瞬斷）；問題消失就自動解除；冷卻時間沿用資源告警的設定。

**資料庫掛掉、或整個 backend 掛掉時，告警本身寫不進去也寄不出去**。這兩種情況要靠外部探測（下面的 Uptime Kuma）。

### Request ID

nginx 為每個請求產生 `$request_id`，以 `X-Request-ID` 轉給 backend 並寫進 nginx access log（`rid=...`）；backend 寫進 JSON 日誌的 `request_id` 欄位、Sentry 的 tag，並回傳在回應標頭 `X-Request-ID`。使用者回報錯誤時，從瀏覽器 DevTools 看到的這個 id 可以直接在 Grafana「SkyLab 日誌」儀表板的 Request ID 欄位查到整條路徑。

### Sentry

- 後端與 worker：`.env` 設 `SENTRY_DSN`（可另設 `SENTRY_RELEASE`、`SENTRY_TRACES_SAMPLE_RATE`）。
- 前端（瀏覽器）：`.env` 設 `VITE_SENTRY_DSN` 後**重新建置 frontend 映像**（`docker compose build frontend`）。沒設時 SDK 在建置階段就被移除，不增加 bundle。建議在 Sentry 另開一個 Browser 專案，和後端分開看。
- 兩邊都不送個資（`send_default_pii=False`）。測試（pytest）一律停用 Sentry，不會把測試產生的例外送到正式專案。

### 容器日誌輪替

`docker-compose.yml` 所有服務都套用 `x-logging`：json-file、單檔 10 MB、保留 5 個（`DOCKER_LOG_MAX_SIZE`／`DOCKER_LOG_MAX_FILE` 可調）。應用程式自己的 `logs/app.log`（每日輪替、30 天）與 `logs/error.log` 不變。

### 容器 healthcheck

db、pgbouncer、redis、backend 原本就有；新增：

- **worker**：檢查 arq 每 60 秒寫進 Redis 的 `skylab:tasks:health-check`（TTL 61 秒）
- **nginx**：`/nginx-health`
- **frontend**：首頁 200

`docker compose ps` 會顯示 `(healthy)`／`(unhealthy)`。注意 Docker Compose 本身**不會**自動重啟 unhealthy 的容器，需要搭配下面的 Uptime Kuma 通知。

---

## 2. 監控 stack

### 啟動

```bash
# 先在 .env 設好（至少）：
#   GRAFANA_ADMIN_PASSWORD、INFLUXDB_ADMIN_PASSWORD、INFLUXDB_ADMIN_TOKEN
docker compose --profile monitoring up -d
```

之後每次 `docker compose up -d` 都要帶 `--profile monitoring`（或在 `.env` 設 `COMPOSE_PROFILES=monitoring`），否則監控容器不會一起起來。

| 服務 | 用途 | 入口 |
|---|---|---|
| Grafana | 儀表板 | `http://<SkyLab>/grafana/`（經 nginx）；本機也可 `http://127.0.0.1:3000/grafana/` |
| Prometheus | 指標與告警規則 | `http://127.0.0.1:9090`（只綁本機，SSH tunnel 使用） |
| Loki + Alloy | 所有容器日誌，保留 14 天 | 在 Grafana 查 |
| InfluxDB 2 | Proxmox Metric Server 推送目的地 | `:8086`（見下方設定） |
| postgres-exporter／redis-exporter／cAdvisor／node-exporter | 資料庫、快取、容器、主機指標 | Prometheus 內部抓取 |
| Uptime Kuma | 外部探測＋推播通知 | `http://127.0.0.1:3001` |

### Grafana 儀表板（已自動匯入，資料夾「SkyLab」）

- **SkyLab 平台**（首頁）：backend 狀態、請求量、5xx 比例、p95 延遲、WebSocket 連線、佇列積壓、最慢／錯誤最多的路由、排程任務狀態表、任務失敗與耗時、背景任務紀錄、依賴元件狀態與延遲
- **SkyLab 基礎設施**：各容器 CPU／記憶體／網路、PostgreSQL（連線、交易、cache 命中率、deadlock、大小）、Redis、主機 CPU／記憶體／磁碟
- **SkyLab 日誌**：依服務與關鍵字篩選、錯誤日誌、以 Request ID 追蹤
- **Proxmox VE（Metric Server）**：節點 CPU／記憶體／IO wait／load、CPU 與記憶體最高的 VM／LXC、各儲存使用率

首次登入 `admin`／`GRAFANA_ADMIN_PASSWORD`。對外網址不是 `http://localhost` 時，設 `GRAFANA_ROOT_URL=https://你的網域/grafana/`。

### Proxmox VE Metric Server 設定

1. `.env` 設：
   - `INFLUXDB_ADMIN_TOKEN`：換成隨機長字串（`openssl rand -hex 32`）
   - `INFLUXDB_BIND_ADDRESS`：PVE 節點連得到的 SkyLab 主機 IP（或 `0.0.0.0`），預設只綁 127.0.0.1
2. `docker compose --profile monitoring up -d influxdb`
3. **建立 PVE 專用、只能寫入的 token**（不要把 admin token 放到 PVE）：
   ```bash
   docker compose exec influxdb influx bucket list --org skylab   # 記下 proxmox 的 bucket ID
   docker compose exec influxdb influx auth create --org skylab \
     --write-bucket <bucket-id> --description "proxmox metric server"
   ```
4. PVE 網頁：**Datacenter → Metric Server → Add → InfluxDB**
   - Name：`skylab`
   - Server：SkyLab 主機 IP，Port：`8086`
   - Protocol：`HTTP`（InfluxDB 2 的 HTTP API）
   - Organization：`skylab`，Bucket：`proxmox`
   - Token：第 3 步產生的寫入 token

   或在任一節點下指令：
   ```bash
   pvesh create /cluster/metrics/server/skylab --type influxdb \
     --server <SkyLab IP> --port 8086 --influxdbproto http \
     --organization skylab --bucket proxmox --token <寫入 token>
   ```
5. 設定是整個叢集共用；多個 PVE 連線（多個叢集）就在每個叢集各設一次。約 10 秒後 Grafana 的 Proxmox 儀表板就有資料。

有多組 SkyLab 或想用社群版儀表板時，也可以在 Grafana 匯入 ID `15356`（Proxmox [Flux]），資料來源選「InfluxDB (Proxmox)」。

### Uptime Kuma（建議設定的監看與通知）

Prometheus 規則（`monitoring/prometheus/rules/skylab.yml`）的觸發狀態可以在 Prometheus／Grafana 看到，但**推播通知交給 Uptime Kuma**（不需要另外架 Alertmanager）。首次開啟 `http://127.0.0.1:3001` 建立管理帳號後，新增：

| 類型 | 目標 | 用意 |
|---|---|---|
| HTTP(s) | `http://nginx/api/v1/utils/health-check/ready`（期望 200） | 整體服務、DB、Redis |
| HTTP(s) | `http://nginx/`（對外入口） | 前端／nginx |
| HTTP(s) - Keyword | `http://prometheus:9090/api/v1/alerts`，關鍵字 `"state":"firing"`，**Invert Keyword** | 任一 Prometheus 告警觸發就通知 |
| TCP Port | 各 PVE 節點 `:8006` | PVE 本身 |

Uptime Kuma 跑在同一個 compose 網路裡，所以可以直接用服務名稱（`nginx`、`prometheus`）。通知管道（Email、Discord、LINE Notify 替代方案、Telegram…）在 Settings → Notifications 設定。**同一台主機整個掛掉時 Uptime Kuma 也會一起掛**：重要環境建議在另一台機器再放一個 Uptime Kuma 監看對外網址。

### /metrics 驗證（選用）

`/metrics` 只在 compose 內網與 `127.0.0.1:8000` 開放，nginx 對 `/metrics` 回 404。若主機上還有其他不信任的程式，可以加上 token：

1. `.env` 設 `METRICS_TOKEN=<隨機字串>`
2. 把同一個字串寫進 `monitoring/prometheus/metrics_token`（單行，勿提交到 git）
3. 取消 `monitoring/prometheus/prometheus.yml` 中 `authorization` 三行的註解，重啟 prometheus

### 指標一覽（backend `/metrics`）

| 指標 | 說明 |
|---|---|
| `http_requests_total{method,path,status}`、`http_request_duration_seconds` | 以路由樣板為 label；對不到路由的請求一律 `path="<unmatched>"` |
| `http_requests_in_progress` | 處理中的請求數 |
| `skylab_websocket_connections{kind}` | vnc／terminal／jobs／classroom／classroom_watch／course_progress |
| `skylab_scheduler_task_runs_total{loop,task,result}`、`skylab_scheduler_task_duration_seconds` | 排程任務執行次數與耗時 |
| `skylab_scheduler_task_last_success_timestamp_seconds`、`skylab_scheduler_task_consecutive_failures` | 心跳 |
| `skylab_scheduler_loop_last_tick_timestamp_seconds`、`skylab_scheduler_loop_is_leader` | 迴圈是否在跑、這個行程是不是 leader |
| `skylab_dependency_up{component}`、`skylab_dependency_latency_seconds` | database／redis／worker／pve:&lt;id&gt; |
| `skylab_queue_jobs{queue}` | arq 佇列等待中的任務數 |
| `skylab_task_records{status}` | queued／running（當下）、failed_24h／succeeded_24h |

依賴元件與佇列指標在 Prometheus 抓取時才更新（最多每 5 秒一次）；PVE 連線狀態沿用最近一次系統健康檢查的結果，不會因為 Prometheus 抓取而去打 PVE。

### 資源與保留期

| 元件 | 保留 | 調整 |
|---|---|---|
| Prometheus | 15 天 | `PROMETHEUS_RETENTION` |
| Loki | 14 天 | `monitoring/loki/loki-config.yml` 的 `retention_period` |
| InfluxDB（Proxmox） | 30 天 | `INFLUXDB_RETENTION`（只在第一次初始化時生效） |

整套監控 stack 約需 1–1.5 GB 記憶體。cAdvisor 與 node-exporter 讀的是主機資訊，在 Docker Desktop（Windows／macOS）上看到的是 Docker VM 而不是實體主機。
