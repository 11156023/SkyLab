# 疑難排解

部署與營運 SkyLab 時實際遇過的故障，附原因與解法。每一條都從「你看到什麼」開始。AI stack 的問題另見 [AI API 使用與部署手冊](/zh-TW/guides/ai-api-user-manual#_7-常見問題)的常見問題章節。

## 啟動與設定

### 後端一啟動就結束，訊息提到 `changethis`

`ENVIRONMENT` 不是 `local`，而 `SECRET_KEY`、`FIRST_SUPERUSER_PASSWORD` 或 `POSTGRES_PASSWORD` 還是範本值（沒設 `SECRET_KEY` 也一樣會被拒絕）。在 `.env` 填入真實值再重新啟動。`SECRET_KEY` 請用至少 32 個字元；比這短只會記一筆警告。

### 改了 `SECRET_KEY` 之後存好的憑證全部失效

`SECRET_KEY` 衍生出加密 PVE、LDAP 與閘道憑證、TOTP 祕密與系統代發機器密碼的金鑰。手動改掉會讓這些全部解不開。先把舊值放回去，再正規輪替：

```bash
cd backend
uv run python -m scripts.rotate_secret_key            # 預覽
uv run python -m scripts.rotate_secret_key --apply    # 重新加密並更新 ../.env
```

之後重新啟動後端與 worker，所有人要重新登入。

### 入口起不來在 80 埠

預設 8082 是刻意的：rootless Docker 綁不了 1024 以下的埠，Windows 上 80 埠通常被 HTTP.sys 佔住。可以保留 8082 並在前面放平台入口，或在允許的主機上設 `NGINX_HOST_PORT=80`。

### 排程器每分鐘記一筆錯誤

資料庫裡沒有 Proxmox 連線時，每個排程任務都會失敗並照實記錄。初始化精靈或「PVE 管理」頁有一條可用連線後就停了。

### 開發時 MailCatcher 收不到任何郵件

從伺服器複製來的 `.env` 帶著 `SMTP_PORT=587` 與 `SMTP_TLS=True`，而 MailCatcher 聽的是 1025 且不走 TLS。拿掉那幾行，或設 `SMTP_HOST=mailcatcher`、`SMTP_PORT=1025`、`SMTP_TLS=False`。

### 在主機上跑後端出現「SSH backend is unavailable」

用到的是全域安裝的 `fastapi` 而不是專案的虛擬環境，所以 `paramiko` 等相依套件不在。在 `backend/` 跑 `uv sync`，再用 `uv run fastapi dev app/main.py` 啟動（或先啟用 repo 根目錄的 `.venv`）。

## Proxmox

### PVE 連線測試出現憑證錯誤，或所有機器都顯示「建立中」／「建立失敗」

兩種不同的原因看起來一樣：

- **私有 CA 沒有 `keyUsage` 延伸欄位。** 新版 Python 與 urllib3 做嚴格驗證，沒有 `keyUsage` 的 PVE CA 會被拒絕，整條連線都查不到。目前的 SkyLab 版本已處理；若是舊版請升級。把 CA 憑證貼進連線的 CA 欄位，不要關閉驗證。
- **容器根本連不到 PVE 主機。** 見下一條。

連線掛掉期間，機器會以資料庫資料與佔位狀態顯示，連線恢復後就會回來。

### 容器連不到 PVE 主機，但主機本身連得到

PVE 的網段和 Docker 自己的範圍重疊。Docker bridge 用 172.17.0.0/16，Compose 再往上配更多 172.x 網段，所以 172.17.x 或 172.20.x 的 PVE 主機會被路由進容器網路而不是出主機。在 `/etc/docker/daemon.json` 把 Docker 搬走：

```json
{
  "bip": "10.254.0.1/24",
  "default-address-pools": [{ "base": "10.253.0.0/16", "size": 24 }]
}
```

重新啟動 Docker，再 `docker compose down` 與 `up -d` 讓 Compose 網路重建。

### 快照分頁不見了，或快照 API 回 409

這台機器的 storage 不支援快照。SkyLab 向 PVE 查每台機器的 `snapshot` 功能，不可用時隱藏分頁，寫入則回 409。常見原因是厚配置的 LVM。需要快照的話，把範本與克隆機放到 LVM-thin、ZFS 或支援 qcow2 的 storage。備份不受影響。

### Windows 機器開起來了但設定的密碼登不進去

cloudbase-init 會拒絕不符合 Windows 複雜度規則的密碼（大寫、小寫、數字、符號至少三類，且不可包含帳號名稱）。SkyLab 在建機前會檢查使用者自訂的 Windows 密碼；在 SkyLab 之外設定的密碼不在檢查範圍。從機器設定重設密碼即可。

## 登入與帳號

### 上課一開始整班都拿到 `429 Too Many Requests`

rootless Docker 下每條連線的來源位址都是 Docker bridge，所有使用者共用同一個 IP，逐 IP 的登入上限就被打滿。把 `.env` 的 `LOGIN_RATE_LIMIT_PER_IP` 調高到能容納整班，或把 rootless 的 port driver 換成會保留來源位址的（`slirp4netns` port driver，見 Docker rootless 文件）。逐帳號的上限不受影響。

### 升級後 LDAP 登入出現 `ldap.serverUnavailable`

SkyLab 會驗證目錄伺服器的憑證與主機名稱。用自簽憑證或校內私有 CA 的網域控制站需要設定 CA：

1. 把 CA（PEM）放到主機上，例如 `./certs/campus-ca.pem`。
2. 掛進 `backend` **與** `worker` 兩個容器。
3. 在 `.env` 設 `LDAP_CA_CERT_FILE=/app/certs/campus-ca.pem` 後重新啟動。

LDAP URI 裡的主機必須出現在憑證的 subjectAltName（DNS 或 IP 項目）。

### 管理員的 TOTP 裝置遺失

```bash
docker compose exec backend python app/reset_totp.py <email>
```

在容器裡不要包 `uv run`：映像裡沒有 workspace 的鎖檔，`uv run` 會重新解析相依套件而失敗。其他使用者由管理員從使用者清單重設。

### 瀏覽器登入正常，但腳本呼叫 `/api/v1/login/access-token` 被拒

Cloudflare Turnstile 已啟用（`TURNSTILE_SITE_KEY` 與 `TURNSTILE_SECRET_KEY` 都有設）。每次密碼或 LDAP 登入與每次註冊都必須帶 `X-Turnstile-Token` 標頭。測試環境用 Turnstile 的測試金鑰組，或從小工具取得 token。site key 必須登記在使用者實際開啟的網域。

### Grafana 要求輸入密碼

Grafana 自己的登入已關閉。以 SkyLab 管理員登入後先開「資源監控」頁：它會核發 nginx 檢查用的 cookie，nginx 才會把你的身分交給 Grafana。之後再按「在 Grafana 查看詳細」或開啟 `/grafana/`。內建 `admin` 帳號無法登入；緊急處理程序見[系統監控](/zh-TW/guides/monitoring)的「登入 Grafana」一節。

## 閘道與網路

### 閘道安裝程式還沒改任何東西就停了

安裝程式只支援 Debian 13（trixie）。它的 nftables 規則用了 `nft destroy table`，需要 nftables 1.0.8，而 Debian 12 是 1.0.6。在 Debian 13 重建閘道後再跑一次安裝程式。

### 發布很多教學環境後閘道上的 nginx 無法重新載入

`nginx -t` 回報 `could not build server_names_hash`。許多長主機名超過了預設 64 位元組的雜湊桶。SkyLab 會把夠大的 `server_names_hash_bucket_size` 寫進產生的 `http.conf`；在舊版上看到這個錯誤時，升級後從「閘道 VM」頁重新同步。單一 server 區塊永遠不會觸發，所以只有規模大了才看得到。

### 經平台入口進來的使用者被記成閘道的位址

在 `.env` 設 `SKYLAB_TRUSTED_PROXY` 為閘道的 IP 或 CIDR，內層 nginx 才會信任它送來的 `X-Forwarded-For`。網域經 Cloudflare 代理（橘色雲）時，在平台入口勾選該選項，SkyLab 會把 Cloudflare 的網段與 `CF-Connecting-IP` 加進閘道設定。

### 封鎖的網段仍然能從機器連到

封鎖規則刻意插在預設閘道規則之前；封鎖網段不可與實驗室子網重疊，且機器的擁有者仍然可以自己加規則。到防火牆畫面檢查規則順序與擁有者的規則。

## 監控

### rootless Docker 下 Grafana 的容器面板是空的

要三件事：`.env` 裡的 rootless socket 與資料根目錄（`DOCKER_SOCKET`、`CONTAINERD_SOCKET`、`DOCKER_DATA_ROOT`）、cAdvisor 能讀主機 cgroup，以及使用者的 cgroup v2 委派（`docker info --format '{{.CgroupDriver}} {{.CgroupVersion}}'` 應印出 `systemd 2`）。[系統監控](/zh-TW/guides/monitoring)的「rootless Docker」一節有確切的設定行。

### Proxmox 儀表板什麼都沒有

PVE 節點與機器的指標不經過後端。每個 PVE 節點內建的 Metric Server 必須推送到 SkyLab 的 InfluxDB，也就是 `INFLUXDB_BIND_ADDRESS` 要讓節點連得到。步驟在[系統監控](/zh-TW/guides/monitoring)的「Proxmox VE Metric Server 設定」。

### 負載高時每個頁面都變慢或逾時

發生時比對兩個健康檢查端點：

| 看到的情況 | 可能原因 |
| --- | --- |
| `/api/v1/utils/health-check/` 馬上回應，`/api/v1/utils/health-check/ready` 很慢或回 503 | 資料庫連線（PgBouncer 連線池）用完了 |
| `/api/v1/utils/health-check/` 也很慢 | 後端的 event loop 被同步呼叫卡住 |
| 只有 AI API 呼叫變慢或回 429／503 | 模型端容量不足：看 [AI API 使用手冊](/zh-TW/guides/ai-api-user-manual)裡的限制，以及 LiteLLM 與 vLLM 儀表板 |

要看是誰佔著連線，進入 `docker compose exec db psql -U postgres -d app`（`.env.example` 的預設值）後執行 `SELECT pid, state, now() - xact_start AS xact_age, wait_event, left(query, 160) FROM pg_stat_activity WHERE datname = current_database() ORDER BY xact_start NULLS LAST;`。`idle in transaction` 的列很多、而且存在時間隨流量增加時，代表有程式路徑在慢速呼叫期間沒有結束交易，見[後端](/zh-TW/guides/backend)。

## 取得更多資訊

- `GET /api/v1/utils/health-check/ready` 告訴你資料庫與 Redis 是否連得到（否則回 503）。
- 資源監控頁的「系統健康」卡片列出 SkyLab 檢查的每個元件，包括 Proxmox、閘道與 AI 閘道。
- 每個 API 回應都帶 `X-Request-ID` 標頭。拿這個值到 `docker compose logs backend worker nginx` 搜尋（JSON 日誌裡是 `request_id`）。
- 排程任務會把心跳寫進 Redis；太久沒跑的任務會以系統告警回報。
- 回報問題時請附上 SkyLab 的 commit（帳號設定 → 關於可看到）、PVE 版本與 request id。
