# 部署指南

本文說明如何以 Docker Compose 把 SkyLab 部署到伺服器。開發環境見[開發指南](/zh-TW/guides/development)；AI stack（LiteLLM、vLLM、金鑰）的細節見 [AI API 使用手冊](/zh-TW/guides/ai-api-user-manual)。

## 總覽

- repo 根目錄的 `docker-compose.yml` 就是整套部署。它以 `include` 引用 `vllm-service/litellm/` 的 LiteLLM compose 檔；兩邊各自保留 `.env`。
- 內建的 `nginx` 容器是唯一對外入口（主機 port 由 `NGINX_HOST_PORT` 決定，預設 8082）：`/api`、`/ws` → backend，`/grafana/` → Grafana，其餘 → frontend。
- 平台是內部工具，會請搜尋引擎不要收錄：nginx 的每個回應都帶 `X-Robots-Tag: noindex, nofollow`，前端頁面也有相同的 robots meta 標籤。專案對外的門面是官網與這份文件。
- 要用網域＋HTTPS 對外時，讓平台也經 Gateway VM 的 nginx（下方「平台入口」）。SkyLab 不簽發憑證，由管理員自備。
- 資料庫是 stack 內的 PostgreSQL 18（經 PgBouncer），Redis 放佇列與快取，`arq` worker 跑背景任務。migration 在啟動時自動執行。
- 部署到測試環境是手動觸發的 GitHub Actions workflow，在 self-hosted runner 上執行（見下方）。push 到 `main` 不會自動部署。

## 1. 準備伺服器

- Linux 主機，裝好 Docker Engine 與 Compose ≥ 2.20。rootless Docker 可用（預設 8082 避開特權 port），但注意下方來源 IP 的限制。
- 主機要連得到每個 Proxmox VE API（8006），並能以 SSH 連到每個 PVE 節點（LXC 操作要在節點上執行 `pct`）、經 SSH 連得到 Gateway VM，模型在別台時也要連得到 vLLM 主機。
- clone repo（例如到 `/opt/skylab/app`）並複製環境範本。就算不用 AI stack，少了 `vllm-service/litellm/.env` Compose 也會拒絕啟動：

```bash
cp -n .env.example .env
cp -n vllm-service/litellm/.env.example vllm-service/litellm/.env
```

### 密鑰

用下列指令產生強隨機值：

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

`SECRET_KEY` 不只簽發 token，也衍生加密資料庫內憑證（Proxmox 與 LDAP 密碼、TOTP secret、Gateway SSH 金鑰等）的金鑰，因此：

- 必須是至少 32 字元的固定值，backend 與 worker 共用同一把（兩者都從 `.env` 讀）。
- `ENVIRONMENT` 不是 `local` 時，`SECRET_KEY` 沒設、留空或仍是 `changethis` 會讓 backend 拒絕啟動。短於 32 字元只記警告，但應該換掉。
- 絕不要只改 `.env` 來更換：所有已存的憑證會全部解不開。要用 `backend/scripts/rotate_secret_key.py` 更換，它會在一個交易內用新金鑰重新加密既有值。伺服器上在 backend 容器內執行；容器裡沒有掛專案 `.env`，所以加 `--skip-env-update`，改為印出新金鑰：

```bash
docker compose exec backend python -m scripts.rotate_secret_key                              # 預覽，不寫入
docker compose exec backend python -m scripts.rotate_secret_key --apply --skip-env-update   # 更換並印出新金鑰
```

把印出的值填進 `.env` 的 `SECRET_KEY`，再執行 `docker compose up -d backend worker`，讓兩者以新值重建；所有使用者要重新登入。在開發用的 checkout 裡，`cd backend && uv run python -m scripts.rotate_secret_key --apply` 會直接更新 `../.env`。

## 2. 環境變數

所有變數都列在[環境變數對照表](/zh-TW/operations/environment)（由 `.env.example` 產生）。伺服器上要注意的：

| 變數 | 說明 |
| --- | --- |
| `SECRET_KEY`、`FIRST_SUPERUSER`、`FIRST_SUPERUSER_PASSWORD`、`POSTGRES_PASSWORD` | 必改；初始化精靈可接管或停用 `.env` 的管理員 |
| `ENVIRONMENT` | 伺服器上設 `production`；只要不是 `local`，`SECRET_KEY` 沒設或上列變數仍是 `changethis` 都會讓 backend 無法啟動 |
| `FRONTEND_HOST` | 使用者開啟的對外網址（用於信件與 SkyLab Connect 裝置登入）；啟用平台入口後要更新 |
| `NGINX_HOST_PORT` | 入口的主機 port，預設 8082 |
| `SKYLAB_TRUSTED_PROXY` | 用平台入口時填 Gateway 連進來的來源 IP／CIDR；預設不信任任何代理 |
| `ENABLE_SIGNUP` | 首次啟動時的公開註冊預設值；之後由管理員後台控制信箱註冊及 Google 首次登入自動建帳。不是 `local` 時，設 `true` 還必須填好兩把 Turnstile 金鑰與真正的 SMTP（`SMTP_HOST` 不是 `mailcatcher`，並設 `EMAILS_FROM_EMAIL`），否則 backend 拒絕啟動；不開放註冊就設 `false` |
| `LOGIN_RATE_LIMIT_PER_ACCOUNT`、`LOGIN_RATE_LIMIT_PER_IP` | 每分鐘登入節流；整班從同一個 NAT 出口登入時 IP 上限要夠大 |
| `BACKEND_CORS_ORIGINS` | 全部經 nginx 同源時留空；`production` 下不接受 `*` |
| `REDIS_ENABLED`、`REDIS_URL` | 容器內保持 `true` |
| `VLLM_BASE_URL`、`VLLM_API_KEY`、`VLLM_MODEL_NAME` | 內建 AI 助手（System AI）用的模型 |
| `AI_API_BASE_URL`、`AI_API_API_KEY`、`AI_API_PUBLIC_BASE_URL`、`LITELLM_RUNTIME_*` | 經 LiteLLM 對外的 AI API；`AI_API_API_KEY` 是受限的 service key，絕不是 master key |
| `SMTP_*`、`EMAILS_FROM_EMAIL` | 寄信（註冊驗證信、告警信）；沒設 `SMTP_HOST` 時信件送到 MailCatcher 容器，它只供本機開發 |
| `GOOGLE_CLIENT_ID` | Google OAuth Client ID，建置前端映像時也會帶入；哪些 Google 帳號能登入由 Google Cloud「目標對象」決定，詳見[註冊設定與 Google 登入](/zh-TW/guides/registration-and-google-login) |
| `TURNSTILE_SITE_KEY`、`TURNSTILE_SECRET_KEY` | 密碼／LDAP 登入與註冊頁的 Cloudflare Turnstile；兩個都填才啟用 |
| `LDAP_CA_CERT_FILE` | LDAP/AD over TLS 的 CA，見下方 |
| `SENTRY_DSN`、`SENTRY_RELEASE`、`VITE_SENTRY_DSN` | 錯誤追蹤；前端的值在建置映像時帶入 |
| `DESKTOP_CLIENT_DOWNLOAD_URL`、`WIREGUARD_*` | SkyLab Connect 與 WireGuard 隧道，見 [WireGuard 與 SkyLab Connect](/zh-TW/guides/wireguard-desktop-architecture) |
| `LOG_LEVEL`、`LOG_JSON`、`DOCKER_LOG_MAX_*` | 日誌；監控 stack 要解析日誌時保持 JSON |
| `COMPOSE_PROFILES=monitoring`、`GRAFANA_*`、`INFLUXDB_*`、`METRICS_TOKEN`、`DOCKER_SOCKET`… | 選用的監控 stack，見[系統監控](/zh-TW/guides/monitoring) |

Proxmox 連線**不是**環境變數：在初始化精靈或「PVE 管理」頁填入並加密存放；舊的 `PROXMOX_*` 變數會被忽略。

### System AI 模型設定檔

內建 AI 助手送出的每個請求都會套用 `backend/config/llm-model-profiles.yaml` 裡該模型的契約。模型 key 必須和 `VLLM_MODEL_NAME` 及上游 served model ID 完全相同；沒有對應 key 的模型會在送出推論前被拒絕，不會退回猜測的模型家族，所以部署用的是別的 ID 時，要新增或改名對應的 key。

- 契約決定每次 System AI 呼叫（包含 adherence 檢查）的 thinking：GPT-OSS 送 `reasoning_effort=low`，Gemma 送 `chat_template_kwargs.enable_thinking=false`，`Qwen/Qwen3.8-27B` 則送 `chat_template_kwargs={"enable_thinking": true, "reasoning_effort": "low"}`，因為它的 [chat template](https://huggingface.co/Qwen/Qwen3.8-27B/blob/main/chat_template.jinja) 不指定時預設是 `xhigh`。舊的 `enable_thinking`／`reasoning_effort` 輸入不會覆蓋契約；其他 template kwargs 照樣保留。
- 契約也列出模型支援的 JSON 回應格式與 tool choice 模式。取樣參數、token 上限與逾時仍在 `backend/config/system-ai.json`；啟用 thinking 時，adherence 檢查的 128 token 上限可能不夠，換模型後要實際驗證輸出。
- 這個檔案包在 backend 映像裡：改完要執行 `docker compose up -d --build backend worker`。

## 3. 啟動 stack

```bash
# 不用 AI stack：不建立 LiteLLM 容器
docker compose up -d --build --scale litellm=0

# 使用 AI stack（需要裝了 PyYAML 與 python-dotenv 的 Python，見 AI API 使用手冊）：
bash scripts/prepare-ai-stack.sh --init-env   # 補齊缺少的金鑰
bash scripts/prepare-ai-stack.sh --start      # 檢查模型與金鑰、產生 config.yaml、啟動全部服務
```

LiteLLM 會掛載只有 `prepare-ai-stack.sh` 才會產生的 `vllm-service/litellm/config.yaml`，所以 AI stack 設好之前，單純的 `docker compose up` 會在 `litellm` 容器失敗。不用 AI stack 的期間，每次 `docker compose up` 都要加 `--scale litellm=0`。（Docker Desktop 不會在這裡失敗，而是建出一個空的 `config.yaml/` 目錄；跑輔助腳本前要先刪掉它。）

`prestart` 會等 PostgreSQL、套用 Alembic migration、建立第一位 superuser；就緒後 `docker compose ps` 會顯示各服務 `(healthy)`。開啟 `http://<主機>:8082` 完成初始化精靈：管理員 → PVE 連線 → IP 網段 → Gateway → 平台入口 → 完成（Gateway 與平台入口兩步可略過，之後再設）。

升級與備份各有專頁：[升級與 migration](/zh-TW/operations/upgrade)、[備份與還原](/zh-TW/operations/backup)。

## 4. 平台入口：主系統經 Gateway 的 nginx 對外

Gateway 主機上的 nginx 原本只代理 VM 的網域與 Port 轉發；「平台入口」讓 SkyLab 主系統自己也走同一台 nginx，用網域加 HTTPS 對外（Web Push 需要 https）。

```
使用者 ──https──▶ Gateway nginx（終結 TLS）──http──▶ 部署機 :8082（內建 nginx）──▶ backend / frontend
```

**設定位置：** 側欄「閘道 VM」頁的「平台入口」分頁；全新安裝時初始化精靈（`/setup`）的「Gateway」「平台入口」兩步也能設定。

1. 填主系統的網域，以及 Gateway 連得到的部署機位址與 port（預設 8082）。
2. 儲存時後端會先從 Gateway 實際連一次 `http://<部署機>:<port>/nginx-health`，連不到就不存；通過後把設定寫進 Gateway 的 `/etc/nginx/skylab/http.conf`（與 VM 網域同一份檔案、同一套 `nginx -t` 失敗還原）。重裝 Gateway 會清空這份檔案；「安裝服務」分頁的「同步規則到 Gateway」會把它連同 VM 規則一起寫回（該分頁看到安裝結束時也會自動做一次）。
3. HTTPS 憑證見下一節：開 HTTPS 前憑證要先設定好，而且要涵蓋平台網域。

## 5. Gateway 的 HTTPS 憑證（管理員自備）

SkyLab **不簽發憑證**。管理員自己準備一張憑證放到 Gateway 主機上，SkyLab 只記路徑；平台入口與所有機器發布的網域**共用這一張**，所以建議用涵蓋整個網域的萬用憑證（例如 `example.com` + `*.example.com`）。萬用字元只涵蓋一層子網域，`a.b.example.com` 不在 `*.example.com` 的範圍內。

**設定位置：** 「閘道 VM」頁的「HTTPS 憑證」分頁；初始化精靈在平台入口步驟勾 HTTPS 時也能填。

1. 把 fullchain 憑證與私鑰放到 Gateway（安裝腳本會建好 `/etc/ssl/skylab/`，權限 750）：
   ```bash
   install -m 644 fullchain.pem /etc/ssl/skylab/fullchain.pem
   install -m 600 privkey.pem   /etc/ssl/skylab/privkey.pem
   ```
   私鑰不能有密碼保護（nginx 啟動時沒辦法輸入）。
2. 在「HTTPS 憑證」分頁填兩個完整路徑後儲存。後端會先經 SSH 在 Gateway 上檢查：讀得到、格式正確、私鑰配對、還沒過期；平台入口已開 HTTPS 的話還要涵蓋平台網域。通過後重寫 `/etc/nginx/skylab/http.conf`（`nginx -t` 失敗會還原）並 reload。
3. 分頁下方的「Gateway 上的憑證」卡片列出到期日、憑證涵蓋的名稱，以及**沒被涵蓋到的 HTTPS 網域**（這些網域仍可連，但瀏覽器會警告）。

**換新憑證：** 直接覆蓋同路徑的檔案，再按分頁上的「重新套用」（或在 Gateway 上 `nginx -t && systemctl reload nginx`）。平台健康監控會在憑證剩不到 14 天或已過期時把 Gateway 標成「需要處理」。

**還沒設定憑證時**，HTTPS 網域掛 Gateway 安裝時產生的自簽憑證（`/etc/nginx/skylab/fallback.crt`），連得上但瀏覽器會警告。

**還要手動完成的三件事**（頁面上也會列出）：

| 項目 | 做法 |
|---|---|
| DNS | 網域在 Cloudflare 管理的 zone 內、且「網域管理」已設定 API Token 與預設 DNS 目標時，儲存平台入口就會在 nginx 套用後把網域指到預設 DNS 目標；換網域或停用時刪掉這筆紀錄。同名但型別不同的位址紀錄（例如舊入口的 AAAA）會擋下儲存，請先到 Cloudflare 處理。其他情況請自己把主系統網域指到 Gateway 的對外 IP。 |
| 信任 Gateway | 部署機 `.env` 設 `SKYLAB_TRUSTED_PROXY=<Gateway 連進來的來源 IP 或 CIDR>`，再 `docker compose up -d nginx`。沒設的話後端看到的來源 IP 全是 Gateway，依 IP 的限流與稽核日誌都會失準，Grafana 免密碼登入的 cookie 也不會帶 `Secure`。 |
| 網址相關設定 | `.env` 的 `FRONTEND_HOST` 改成新的 https 網址；Google 登入的授權來源、Turnstile 的網域清單一併更新。 |

**注意：**

- **保留直連備援。** 後端是經 SSH 管 Gateway 的 nginx；Gateway 掛掉時從網域進不來，也就沒辦法從介面修它。請保留從內網或 VPN 直連 `http://<部署機>:8082` 的路。
- **rootless Docker** 不保留來源 IP，容器裡的 nginx 看到的來源一律是 Docker 的轉發位址。這時 `SKYLAB_TRUSTED_PROXY` 要填那個位址（在平台入口分頁的「後端看到的來源 IP」可以看到），並且用防火牆把部署機的對外 port 限制成只有 Gateway 連得到，否則直連的人可以自帶 `X-Real-IP` 偽造來源。
- 主系統網域會被保留：即使平台入口暫時停用，VM 擁有者也不能把這個網域發布到自己的機器上。

**經由 Cloudflare 代理（橘色雲）**是平台入口上的一個勾選框，預設不勾：

| | DNS only（預設） | 經 Cloudflare 代理 |
|---|---|---|
| SkyLab 建的 DNS 紀錄 | 灰色雲 | 橘色雲 |
| Gateway 的對外 IP | 查 DNS 就看得到 | 藏在 Cloudflare 後面 |
| 主系統看到的使用者 IP | 連線來源 | 取自 `CF-Connecting-IP`，只採信 [Cloudflare 公布的網段](https://www.cloudflare.com/ips/) |
| 上傳 | 不受 Cloudflare 限制 | 受 Cloudflare 方案限制（免費方案每個請求 100 MB） |
| Cloudflare 的 SSL/TLS 模式 | 不相關 | Gateway 開 HTTPS 時要設 **完整（嚴格）／Full (strict)**；設成彈性（Flexible）會讓 Gateway 的 HTTP 轉 HTTPS 無限轉址 |

勾選後，Gateway 的 nginx 會在平台入口的 server 區塊加上每個 Cloudflare 網段的 `set_real_ip_from` 與 `real_ip_header CF-Connecting-IP`，所以 `SKYLAB_TRUSTED_PROXY` 仍然填 Gateway。網段清單寫在後端（`nginx_gateway_service.py` 的 `CLOUDFLARE_IP_RANGES`）；Cloudflare 新增網段時要跟著更新，否則從新網段來的使用者會被記成 Cloudflare 的位址。網域不在 Cloudflare 管理的 zone 內時，勾選只會加上 nginx 的設定，紀錄要自己到 Cloudflare 切成橘色雲。DNS 紀錄的代理狀態和勾選不一致時，狀態卡會提出警告。

## 6. LDAP / Active Directory over TLS

`ldaps://` 與 StartTLS 一律驗證伺服器憑證與主機名稱。LDAP 伺服器 URI 裡的主機（DNS 名稱如 `dc01.campus.example`，或 IP 如 `192.168.10.5`）必須列在目錄伺服器憑證的 subjectAltName（名稱用 DNS 項、IP 用 IP Address 項），否則握手會因主機名稱／IP 不符而失敗。沒有 keyUsage 擴充的私有 CA 可以接受。

網域控制站若用自簽憑證或校內／企業私有 CA 簽的憑證，在設好 CA 之前 LDAP 登入會失敗（bind 失敗，錯誤顯示為 `ldap.serverUnavailable`）：

1. 把 CA 憑證（PEM）放到主機，例如 `docker-compose.yml` 旁的 `./certs/campus-ca.pem`。
2. 掛進 `backend` **與** `worker` 兩個容器，例如用 Compose override 檔：

   ```yaml
   services:
     backend:
       volumes:
         - ./certs:/app/certs:ro
     worker:
       volumes:
         - ./certs:/app/certs:ro
   ```

3. 在 `.env` 設路徑並重啟 stack：

   ```bash
   LDAP_CA_CERT_FILE=/app/certs/campus-ca.pem
   ```

`LDAP_CA_CERT_FILE` 留空＝使用系統信任庫，網域控制站用公開 CA 憑證時這樣就夠。

## 7. Gateway VM

Gateway 是另一台 Linux VM，用 `gateway/install.sh` 安裝（nginx stream + http、WireGuard、nftables ACL、SNAT、選用的 Prometheus exporter）。安裝腳本**只支援 Debian 13（trixie）**，其他系統在動到任何設定前就會停止：它的 nftables 規則用到 `nft destroy table`，需要 nftables 1.0.8 以上，而 Debian 12 附的是 1.0.6。Debian 12 的 Gateway 請先升級或改用 Debian 13 重建，再（重新）執行安裝腳本。SkyLab 從「閘道 VM」頁經 SSH 管理它，該頁的「安裝服務」分頁也能代跑安裝腳本（「一鍵安裝」）。細節見 [WireGuard 與 SkyLab Connect](/zh-TW/guides/wireguard-desktop-architecture) 與[系統監控](/zh-TW/guides/monitoring)的「Gateway 監控」。

## 8. 持續部署（測試環境）

`.github/workflows/deploy-pve-test.yml`（「Deploy to PVE Test」）從 Actions 分頁手動觸發（`workflow_dispatch`），只在 `SkyLab-tw/SkyLab` repo 執行。它在標籤 `skylab-main` 的 self-hosted runner 上跑，動作前先等 `pve-test` environment 審核通過。

在 runner 上：

- 機密與模型路由不進 repo。workflow 先在 `/opt/skylab` 的檔案裡補上缺少的 LiteLLM master／salt／DB 值與 SkyLab service key（`prepare-ai-stack.sh --init-env`；缺上游 vLLM 金鑰時在這一步失敗），再把 `/opt/skylab/.env`、`/opt/skylab/vllm-service/litellm/.env`、`/opt/skylab/vllm-service/models.json`（有的話還有 `.env.API`）複製進 checkout。
- 接著用 `prepare-ai-stack.sh --start` 部署，最後一步是 `docker compose up -d --build`（那份 `.env` 設了 `COMPOSE_PROFILES=monitoring` 時會一起帶監控 stack），等 backend 健康檢查與 LiteLLM readiness 通過，再對每個公開 AI 模型跑 smoke test。模型測試失敗只會發出警告，部署照樣保留。
- smoke test 前先刪掉舊的 `pve-deploy-smoke` 金鑰、核發一日有效的新金鑰；測試後無論成功失敗都清理（未使用的直接刪除，已有用量的撤銷並保留帳務紀錄）。清理失敗會讓 workflow 報錯；遺留的金鑰也會自動過期。

要建新 runner：在部署機以專用使用者安裝 GitHub Actions runner、加上 `skylab-main` 標籤、在 repo 設定建立需要審核者的 `pve-test` environment，並把上述檔案放到 `/opt/skylab`。

## 9. 維運檢查清單

- 健康：`GET /api/v1/utils/health-check/ready` 在資料庫或 Redis 掛掉時回 503；其餘看資源監控頁的「系統健康」卡與選用的 Grafana 儀表板（[系統監控](/zh-TW/guides/monitoring)）。
- 日誌：`docker compose logs backend worker nginx`；JSON 日誌帶 `request_id`，回應標頭 `X-Request-ID` 也會回傳同一個值。
- 管理員遺失 TOTP 裝置：`docker compose exec backend python app/reset_totp.py <email>`（容器內不要用 `uv run`）。
- 機器在 SkyLab 內備份（逐台機器備份／還原）；平台本身（資料庫與 `.env`）的備份見[備份與還原](/zh-TW/operations/backup)。
