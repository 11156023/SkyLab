# 升級與 migration

如何把運行中的 SkyLab 升到新的 commit。整個 stack 就是一份 `docker-compose.yml`，升級就是拉新程式碼、重建映像，然後讓 `prestart` 套用資料庫 migration。

## 升級之前

1. **先備份。** 匯出資料庫並複製 `.env`，見[備份與還原](/zh-TW/operations/backup)。沒有備份就無法安全地回退 migration。
2. **讀更新紀錄。** [更新紀錄](/zh-TW/changelog)列出你的 commit 之後的變更。提到 `.env`、閘道、LDAP 或 `SECRET_KEY` 的項目通常需要你動手。
3. **確認磁碟空間。** `docker compose build` 會保留舊的映像層直到你清理。
4. **挑離峰時間。** 後端與 worker 會重新啟動，主控台與執行中的任務會中斷，Proxmox 上的機器照常運行。
5. **留意 `db` 映像。** 升級若改了 `db` 服務的 PostgreSQL 大版本（目前是 `postgres:18`），既有的資料 volume 不能直接沿用：升級前先匯出，再還原到新版本。

## 標準升級流程

```bash
cd /opt/skylab/app                       # 你的 checkout
git fetch origin
git log --oneline HEAD..origin/main      # 看看會拿到什麼
git pull --ff-only origin main

docker compose up -d --build --scale litellm=0   # 有 AI stack 時改跑：bash scripts/prepare-ai-stack.sh --start
docker compose logs -f prestart          # 等資料庫就緒、跑 "alembic upgrade head" 後結束
```

`prestart` 會套用舊版本到 `head` 之間的每一支 migration，起始管理員不存在時順便建立，然後結束。後端只在 `prestart` 成功結束後才啟動。就緒端點再次回 `200` 就表示升級完成：

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8082/api/v1/utils/health-check/ready
```

測試環境改由 GitHub Actions 的「Deploy to PVE Test」workflow 升級，它在 self-hosted runner 上做同樣的事，見[部署指南](/zh-TW/guides/deployment#_8-持續部署-測試環境)。

## 新增的環境變數

拉完程式碼後比對範本與你的檔案：

```bash
diff <(grep -oE '^#? ?[A-Z_]+=' .env.example | tr -d '# ' | sort) \
     <(grep -oE '^[A-Z_]+=' .env | sort)
```

只出現在 `.env.example` 的變數就是新增的。大多數有預設值；[環境變數對照表](/zh-TW/operations/environment)標出哪些必填。有跑 AI stack 的話，`vllm-service/litellm/.env` 也要比對。

## 檢查 migration 狀態

```bash
docker compose exec backend alembic current      # 資料庫目前的 revision
docker compose exec backend alembic history | head
docker compose exec backend alembic heads        # 必須只印出一個 head
```

- 升級成功後 `current` 會等於 `heads`。
- `heads` 讀的是映像裡的 migration 腳本，不是資料庫。出現一個以上的 head 代表程式碼裡有沒合併的 migration 分支，`upgrade head` 會拒絕執行；先在程式碼裡合併 head。
- `current` 出現「Can't locate revision」時，代表資料庫被這份 checkout 沒有的程式碼升級過（共用開發資料庫常見）。不要硬改，先找出建立那支 revision 的程式碼。
- migration 失敗時交易會回滾，資料庫停在 `current` 顯示的 revision。修好原因後再跑一次上面的啟動指令，`prestart` 會從那裡繼續。

migration 直接連 PostgreSQL、不經 PgBouncer，因為它要在長交易裡執行 DDL。這部分不用設定，`prestart` 已經接好（`POSTGRES_SERVER=db`）。

## 回退

沒有自動降級。要撤回一個版本：

1. `docker compose down`（保留 volume）。
2. 還原升級前的資料庫備份（見[備份與還原](/zh-TW/operations/backup#還原資料庫)）。
3. `git checkout <previous commit>`，再用標準升級流程的同一個指令重建並啟動。

只回退程式碼而不還原資料庫，只有在新 migration 純粹是新增欄位時才安全，而更新紀錄不保證這一點。

## 需要動手的重大變更

這些變更上線時需要維運者做事。跨過它們升級就要做對應動作。

| 變更 | 動作 |
| --- | --- |
| LDAP 連線會驗證伺服器憑證與主機名稱 | 把校內 CA 掛進 `backend` 與 `worker` 並設 `LDAP_CA_CERT_FILE`，見[部署指南](/zh-TW/guides/deployment#_6-ldap-active-directory-over-tls)。 |
| `SECRET_KEY` 同時加密儲存的憑證 | 絕不手改，用 `backend/scripts/rotate_secret_key.py` 輪替。 |
| 閘道從 HAProxy／Traefik／certbot 改為 nginx，憑證由管理員自備 | 在 Debian 13 的閘道重跑安裝程式（會停掉舊的 HAProxy／Traefik 服務），再到「閘道 VM」頁的「HTTPS 憑證」分頁填憑證路徑。certbot 已簽好的憑證可以沿用：填 `/etc/letsencrypt/live/<名稱>/fullchain.pem` 與 `privkey.pem`，之後要自己續期。 |
| Proxmox 設定從 `.env` 移到逐連線紀錄 | `PROXMOX_*` 變數已不讀，到「PVE 管理」頁設定連線。 |
| 內建 nginx 只信任 `SKYLAB_TRUSTED_PROXY` 送來的 `X-Real-IP`／`X-Forwarded-Proto` | SkyLab 經閘道對外時，把這個變數設成閘道的位址。 |
| 資料庫正規化到 3NF（2026 年 10 月） | 從未記錄在批次任務裡的班級機器升級後顯示為「未建機」，從班級工作頁重建。 |
| 範本改為平台範本，移除 PVE 端轉換 | 既有的 PVE 範本照常可用；新範本從「範本」頁建立，不再轉換母機。 |

## 升級閘道與桌面端

- 閘道 VM 從「閘道 VM」頁的「安裝服務」分頁更新：「重新安裝」執行的是 backend 映像裡附的 `gateway/install.sh`，所以要先升級 SkyLab。重新安裝會清空自動產生的 nginx 設定檔，「同步規則到 Gateway」會把規則寫回（分頁看到安裝結束時也會自動做一次）。安裝程式只接受 Debian 13。
- SkyLab Connect 啟動時會到 GitHub（`1Ray0/SkyLab-Connect-Releases`）檢查有沒有新版，有的話提示下載新的安裝程式。`DESKTOP_CLIENT_DOWNLOAD_URL` 只決定平台上的下載連結指到哪裡；桌面端發版不需要改後端。

## 升級 AI stack

LiteLLM 跑在 Compose stack 裡，有自己的 `vllm-service/litellm/.env`；vLLM 推論引擎則在 Compose 之外執行。拉完程式碼後改跑 `bash scripts/prepare-ai-stack.sh --start`（取代 `docker compose up -d --build`）：它會重新讀 `vllm-service/models.json`、重寫 LiteLLM 設定與 Prometheus 目標、重建 LiteLLM 容器、同步 service key 的模型白名單，最後執行 `docker compose up -d --build`。LiteLLM 映像預設是 `litellm/litellm:latest`，`up` 不會自己拉新版的 `latest`：要更新就跑 `docker compose pull litellm`，或在 `vllm-service/litellm/.env` 用 `LITELLM_IMAGE` 固定一個測過的版本。見 [AI API 使用與部署手冊](/zh-TW/guides/ai-api-user-manual#_5-修改連線、重啟與回滾)。
