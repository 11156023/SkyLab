# 快速開始

本頁帶你從一台空機器走到一個接好一條 Proxmox VE 連線、可以使用的 SkyLab，到各角色的操作指南開始的地方為止。要上正式伺服器請接著讀[部署指南](/zh-TW/guides/deployment)；要改程式請讀[開發指南](/zh-TW/guides/development)。

## 前置需求

| 項目 | 用途 |
| --- | --- |
| 一台裝有 Docker Engine 與 Compose ≥ 2.20 的 Linux 主機 | 整個 stack 就是一份 `docker-compose.yml`；根目錄的檔案用了 `include`，舊版 Compose 看不懂。rootless Docker 可以用。 |
| 一台或一組該主機連得到 8006 埠的 Proxmox VE 節點／叢集 | SkyLab 透過 PVE API 操作，少數操作（LXC 終端機、`pct`、節點指令）走 SSH。需要一個有權限克隆、設定、啟動機器的 PVE 帳號。 |
| 一段給 SkyLab 建出來的機器用的 IP 範圍 | 初始化精靈會問子網、閘道與 DNS，cloud-init 會把它們寫進每台新機器。這段範圍必須在你要用的 PVE bridge 上可路由。 |
| 選用：一台 Debian 13 VM 當閘道 | 只有桌面端 WireGuard 連入、埠轉發、網域反向代理與 HTTPS 平台入口才需要。之後可從「閘道 VM」頁再加。 |

主機上除了 Docker 不必裝任何東西。`uv` 與 `Bun` 只有在容器外跑後端或前端時才需要。

## 1. 取得程式碼並設定

```bash
git clone https://github.com/SkyLab-tw/SkyLab.git
cd SkyLab
cp -n .env.example .env
cp -n vllm-service/litellm/.env.example vllm-service/litellm/.env   # 沒有用 AI stack 時 Compose 也會讀它
```

`.env.example` 以編號章節說明每一個變數。第一次啟動前至少要改：

| 變數 | 說明 |
| --- | --- |
| `SECRET_KEY` | 至少 32 個隨機字元。它不只簽發 token，**也**用來加密存在資料庫裡的憑證（PVE 密碼、LDAP 綁定密碼、TOTP 祕密、閘道 SSH 金鑰）。之後絕不能手動改，要用 `backend/scripts/rotate_secret_key.py` 輪替。 |
| `FIRST_SUPERUSER`、`FIRST_SUPERUSER_PASSWORD` | 起始管理員。初始化精靈可以接管或停用這個帳號。 |
| `POSTGRES_PASSWORD` | stack 使用的資料庫密碼。 |
| `ENVIRONMENT` | 筆電上用 `local`，伺服器用 `production`。不是 `local` 時，只要上面任一值還是 `changethis`，後端就拒絕啟動。 |
| `FRONTEND_HOST` | 使用者實際開啟的網址，用在郵件與桌面端。 |

產生機密值：

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Proxmox 憑證**不是**環境變數，而是在初始化精靈輸入、加密存進資料庫。

## 2. 啟動 stack

```bash
docker compose up -d --build --scale litellm=0
```

`--scale litellm=0` 會略過從 `vllm-service/litellm/` 引入的 LiteLLM 服務：步驟 5 的 AI 設定產生 `config.yaml` 之前，它起不來。要邊改程式邊熱重載，請改在主機上跑後端與前端，見[開發指南](/zh-TW/guides/development)。

第一次啟動要幾分鐘：`prestart` 等 PostgreSQL 就緒、套用兩百多支 migration、建立起始管理員後結束。用這兩個指令看進度：

```bash
docker compose logs -f prestart backend
curl -s http://localhost:8082/api/v1/utils/health-check/ready
```

資料庫與 Redis 連得到之前，就緒端點會回 `503`。還沒有 Proxmox 連線時，排程器每分鐘會記一筆錯誤，空資料庫上這是預期行為。

入口是 nginx，host 埠為 `NGINX_HOST_PORT`（預設 8082）：`/api` 與 `/ws` 轉到後端，其餘轉到前端。

## 3. 執行初始化精靈

開啟 `http://<host>:8082`。資料庫是空的時候會被導到 `/setup`，這個精靈不需登入，完成後就永久關閉。步驟如下：

1. **管理員。** 建立系統管理員，或輸入 `.env` 管理員的信箱接管該帳號。密碼要符合複雜度政策（8 到 128 字元，含大寫、小寫、數字與符號）。精靈拒絕把既有的非管理員帳號升級成管理員。
2. **Proxmox 連線。** 主機、埠、帳密，私有 CA 的話還有 CA 憑證。存檔前會先測試連線；節點名稱在所有連線間必須唯一。新機器用的 pool、storage 與預設節點是逐連線設定。
3. **IP 網段。** SkyLab 建出來的機器用的 CIDR、閘道、DNS 與 PVE bridge。靜態位址從這段範圍分配；此時不會同步防火牆。
4. **閘道 VM。** 選用。閘道的 SSH 資訊，精靈可以替你跑安裝程式。開發環境可跳過。
5. **平台入口。** 選用。把 SkyLab 本身透過閘道的 nginx 掛到一個網域上，HTTPS 憑證由管理員自備。開發環境可跳過。
6. **完成。** 精靈把安裝標記為完成，之後任何對它的請求都回 403。

精靈設定的所有內容之後都能在管理頁面（「PVE 管理」、「IP 管理」、「閘道 VM」）修改。

## 4. 第一次登入

用管理員帳號登入。輸入密碼之後：

- **兩步驟驗證。** TOTP 是逐帳號選用。管理員可以對個別使用者強制啟用；裝置遺失時由使用者清單重設，或執行 `docker compose exec backend python app/reset_totp.py <email>`。
- **服務檢查。** 每次登入、進入儀表板前，SkyLab 都會檢查資料庫、Redis、worker、Proxmox、閘道與 AI 閘道。有項目失敗時，任何人都可以重新檢查或帶著警告繼續；管理員還看得到是哪個元件失敗、原因是什麼。
- **首次設定。** 帳號第一次登入時，會有一個簡短的精靈設定語言、個人資料、外觀，以及選用的兩步驟驗證。語言與外觀存在瀏覽器。

## 5. 下一步

- **範本。** 機器都是從平台範本克隆出來的。老師先準備一台基底 VM 或容器，再到「機器範本」頁用「從 VM 建立範本」；映像裡要有 cloud-init（Windows 是 cloudbase-init），SkyLab 才能設定登入帳號、密碼與 IP。
- **治理與配額。** 「資源政策」頁控制 TTL 回收、閒置偵測、資源告警、挖礦偵測與建置併發數，配額也在這一頁：一份全域預設值加上逐使用者的覆寫，申請表單會依此設限。
- **AI。** 先啟動 vLLM 模型，跑 `bash scripts/prepare-ai-stack.sh --init-env` 填入金鑰，再跑 `bash scripts/prepare-ai-stack.sh --start` 產生 LiteLLM 設定並啟動含 LiteLLM 在內的整個 stack；見 [AI API 使用與部署手冊](/zh-TW/guides/ai-api-user-manual)。
- **監控。** `docker compose --profile monitoring up -d` 加上 Prometheus、Grafana、Loki、Alloy 與 InfluxDB；見[系統監控](/zh-TW/guides/monitoring)。
- **註冊與 Google 登入。** 自助註冊、管理員審核與 Google OAuth 見[註冊設定與 Google 登入](/zh-TW/guides/registration-and-google-login)。
- **正式環境強化。** 平台入口、HTTPS、LDAP over TLS 與維運檢查清單都在[部署指南](/zh-TW/guides/deployment)。
